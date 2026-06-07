import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, ErrorMsg } from './common'
import { Spinner } from '@/components/ui/spinner'
import { parsePptx, type SlideData } from '../worker/parsers/pptx'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { useI18n } from '@/i18n'
import { extractTitle } from './pptx/extractTitle'
import './pptx/pptx.css'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.5
const ZOOM_MAX = 4

interface RenderedSlide {
  css: string
  html: string
}

/**
 * Per-slide display. Receives the rendered { css, html } from the parent
 * (which gets them from the render worker) and shows them, or a blank
 * placeholder of the correct aspect ratio while waiting. The page wrapper
 * reserves vertical space using the slide's intrinsic aspect ratio so the
 * scroll position is stable even before later slides finish rendering.
 *
 * React.memo on the comparator ensures only the SlideItem whose `rendered`
 * prop just got populated re-renders — sibling SlideItems short-circuit.
 * Without this, every worker-progress setState would re-render all 45
 * children, drowning out the responsiveness gain from moving the rendering
 * off-main-thread in the first place.
 */
const SlideItem = memo(
  function SlideItem({
    slide,
    idx,
    rendered,
    registerRef,
  }: {
    slide: SlideData
    idx: number
    rendered: RenderedSlide | undefined
    registerRef: (idx: number, el: HTMLDivElement | null) => void
  }) {
    return (
      <div
        ref={(el) => registerRef(idx, el)}
        className="ipm-pptx-slide-page"
        data-idx={idx}
      >
        <div
          className={`ipm-pptx-slide sld-${idx}`}
          style={{ ['--slide-aspect' as string]: String(slide.width / slide.height) }}
        >
          {rendered ? (
            <>
              {/* Per-slide <style>. CSS is already scoped to .ipm-pptx-root
                  .sld-N via prefixSelectors so siblings don't collide. */}
              <style dangerouslySetInnerHTML={{ __html: rendered.css }} />
              {/* slide-inner is NID's absolute-positioning container. Shape
                  <div>s from _buildShapeParts position relative to it. */}
              <div className="slide-inner" dangerouslySetInnerHTML={{ __html: rendered.html }} />
            </>
          ) : null}
        </div>
      </div>
    )
  },
  (prev, next) =>
    prev.rendered === next.rendered &&
    prev.slide === next.slide &&
    prev.idx === next.idx &&
    prev.registerRef === next.registerRef,
)

export function PptxViewer({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement>(null)
  const slideRefs = useRef<HTMLDivElement[]>([])
  const [error, setError] = useState<string | null>(null)
  const [slides, setSlides] = useState<SlideData[]>([])
  // Render results from the worker. Keyed by slide index. As each slide
  // finishes rendering in the worker, its entry is added here and the
  // corresponding SlideItem flips from placeholder to content.
  const [renderedMap, setRenderedMap] = useState<Record<number, RenderedSlide>>({})
  const [scale, setScale] = useState(1)
  const [current, setCurrent] = useState(1)
  const [toc, setToc] = useState<TocItem[]>([])
  const [loading, setLoading] = useState(true)
  // Visible viewport size of the scroll container, used to compute fit-page
  // dimensions so each slide fits within both width AND height (not just width).
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  // Ref-mirrored "current" + "slide count" so the keyboard listener can be
  // installed once without re-attaching on every scroll tick.
  const currentRef = useRef(1)
  useEffect(() => { currentRef.current = current }, [current])
  const slideCountRef = useRef(0)
  useEffect(() => { slideCountRef.current = slides.length }, [slides.length])

  // Stable callback for SlideItem to publish its DOM ref. Used by keyboard
  // nav, page goto, and IntersectionObserver for "current slide" tracking.
  // Stable identity is required so memo'd SlideItems don't re-render when
  // the parent re-renders.
  const registerSlideRef = useCallback((idx: number, el: HTMLDivElement | null) => {
    if (el) slideRefs.current[idx] = el
  }, [])

  // Fetch + parse + spawn the render worker. Parse runs on main thread (native
  // DOMParser) and is ~900ms for a 45-slide deck. Render runs in a separate
  // Web Worker so the ~700ms-per-slide slideToHtml work doesn't block the
  // main thread — modal close stays responsive, scroll stays responsive,
  // future input events get processed promptly. As each slide finishes
  // rendering, the worker posts it back and the corresponding SlideItem
  // flips from placeholder to content.
  useEffect(() => {
    let cancelled = false
    let worker: Worker | null = null
    setError(null); setSlides([]); setRenderedMap({}); setCurrent(1); setToc([])
    setLoading(true)
    slideRefs.current = []
    const tStart = performance.now()
    console.log('[pptx-timing] load start')
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then(async (buf) => {
        if (cancelled) return
        const tFetchDone = performance.now()
        console.log(`[pptx-timing] fetch+arrayBuffer done: ${(tFetchDone - tStart).toFixed(0)} ms`)
        const result = await parsePptx(buf)
        if (cancelled) return
        const tParseDone = performance.now()
        console.log(`[pptx-timing] parse done (${result.slides.length} slides): ${(tParseDone - tFetchDone).toFixed(0)} ms`)
        // Eager pass: extract titles for the TOC. Cheap (~milliseconds) and
        // means the TOC is fully populated before any slide finishes rendering.
        const allToc: TocItem[] = []
        for (let i = 0; i < result.slides.length; i++) {
          const title = extractTitle(result.slides[i])
          const label = title || t('preview.slideN', { n: i + 1 })
          const prefix = title ? `${i + 1}. ` : ''
          allToc.push({ id: `slide-${i}`, label: `${prefix}${label}` })
        }
        const tTitlesDone = performance.now()
        console.log(`[pptx-timing] extractTitle (${result.slides.length} slides): ${(tTitlesDone - tParseDone).toFixed(0)} ms`)
        setSlides(result.slides)
        setToc(allToc)
        setLoading(false)
        console.log(`[pptx-timing] skeleton ready (parse + titles): ${(performance.now() - tStart).toFixed(0)} ms`)
        // Spawn the render worker. We send slides ONE AT A TIME instead of
        // all in one batch — structured-clone of the full deck blocked the
        // main thread for ~8s on the test deck (45 slides × heavy nested
        // shapes / image strings). Per-slide messages clone in 10-50ms each.
        //
        // The next slide is dispatched from the worker's onmessage handler so
        // we don't queue all 45 sends synchronously; each send happens only
        // after the previous result lands. This gives the browser room to
        // paint each rendered slide before kicking off the next render.
        let firstSlideLogged = false
        let lastSlideLogged = false
        let nextToSend = 0
        const totalSlides = result.slides.length
        const sendNext = () => {
          if (cancelled || !worker || nextToSend >= totalSlides) return
          const idx = nextToSend++
          worker.postMessage({ slide: result.slides[idx], idx })
        }
        worker = new Worker(new URL('../worker/pptxRender.worker.ts', import.meta.url), { type: 'module' })
        worker.onmessage = (ev: MessageEvent<{ idx: number; css: string; html: string }>) => {
          if (cancelled) return
          const { idx, css, html } = ev.data
          if (idx === 0 && !firstSlideLogged) {
            firstSlideLogged = true
            console.log(`[pptx-timing] first slide rendered: ${(performance.now() - tStart).toFixed(0)} ms`)
          }
          if (idx === totalSlides - 1 && !lastSlideLogged) {
            lastSlideLogged = true
            console.log(`[pptx-timing] all slides rendered: ${(performance.now() - tStart).toFixed(0)} ms`)
          }
          setRenderedMap((prev) => ({ ...prev, [idx]: { css, html } }))
          // Queue the next render. setTimeout(0) yields a macrotask so React
          // commits + paints this slide before the next render starts in the
          // worker (otherwise rapid back-to-back postMessages can starve the
          // paint pipeline despite the worker being on a separate thread).
          setTimeout(sendNext, 0)
        }
        // Kick off the chain. First send happens immediately so the worker
        // starts rendering as soon as it's ready.
        sendNext()
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
      if (worker) worker.terminate()
    }
  }, [path, t])

  // ResizeObserver attached via callback ref. We CANNOT do this in a useEffect
  // with deps=[] because the container <div> is only rendered when !loading;
  // useEffect runs at mount time, when the container ref is still null (the
  // viewer is showing the loading spinner). With useEffect the observer would
  // never attach and containerSize would stay at {0,0}, making the CSS calc
  // produce a negative width and slides render as blank boxes.
  const observerRef = useRef<ResizeObserver | null>(null)
  const setContainer = useCallback((el: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect()
      observerRef.current = null
    }
    containerRef.current = el
    if (!el) return
    setContainerSize({ w: el.clientWidth, h: el.clientHeight })
    const ro = new ResizeObserver(() => {
      setContainerSize({ w: el.clientWidth, h: el.clientHeight })
    })
    ro.observe(el)
    observerRef.current = ro
  }, [])

  // Keyboard navigation: PageDown / PageUp / Arrow keys to step one slide at a
  // time, mirroring the PowerPoint reading view and Acrobat behaviour. Listener
  // is installed once via refs so it doesn't re-attach on every IntersectionObserver
  // tick.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Ignore when the user is typing in an input/textarea/editable element.
      const tgt = e.target as HTMLElement | null
      if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) return
      if (e.key === 'PageDown' || e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        const next = Math.min(currentRef.current + 1, slideCountRef.current)
        const el = slideRefs.current[next - 1]
        if (!el) return
        e.preventDefault()
        el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      } else if (e.key === 'PageUp' || e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        const prev = Math.max(currentRef.current - 1, 1)
        const el = slideRefs.current[prev - 1]
        if (!el) return
        e.preventDefault()
        el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      } else if (e.key === 'Home') {
        const el = slideRefs.current[0]
        if (!el) return
        e.preventDefault()
        el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      } else if (e.key === 'End') {
        const el = slideRefs.current[slideCountRef.current - 1]
        if (!el) return
        e.preventDefault()
        el.scrollIntoView({ block: 'start', behavior: 'smooth' })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // IntersectionObserver: track which slide centre is in the viewport, for the
  // toolbar "current slide" indicator.
  useEffect(() => {
    const container = containerRef.current
    if (!container || slides.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        let best = -1
        let bestRatio = 0
        for (const e of entries) {
          if (e.intersectionRatio > bestRatio) {
            bestRatio = e.intersectionRatio
            best = Number((e.target as HTMLElement).dataset.idx)
          }
        }
        if (best >= 0) setCurrent(best + 1)
      },
      {
        root: container,
        rootMargin: '-40% 0px -40% 0px',
        threshold: [0, 0.25, 0.5, 0.75, 1],
      },
    )
    for (const el of slideRefs.current) {
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [slides])

  // Register toolbar capabilities.
  usePreviewToolbar({
    pages: {
      count: slides.length,
      current,
      goto: (n: number) => {
        const el = slideRefs.current[n - 1]
        if (el) el.scrollIntoView({ block: 'start', behavior: 'auto' })
      },
    },
    zoom: {
      scale,
      in: () => setScale((s) => Math.min(ZOOM_MAX, s + ZOOM_STEP)),
      out: () => setScale((s) => Math.max(ZOOM_MIN, s - ZOOM_STEP)),
      fit: () => setScale(1),
      reset: () => setScale(1),
    },
    ...(toc.length > 0 ? {
      toc: {
        items: toc,
        goto: (id: string) => {
          const idx = Number(id.replace('slide-', ''))
          const el = slideRefs.current[idx]
          if (el) el.scrollIntoView({ block: 'start', behavior: 'auto' })
        },
      },
    } : {}),
    download: { url: rawUrl(path), filename },
  }, [slides.length, current, scale, toc, path, filename])

  if (error) return <ErrorMsg msg={error} />
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-sm" style={{ color: 'var(--t3)' }}>
        <Spinner className="h-4 w-4" /> <span>{t('preview.parsing')}</span>
      </div>
    )
  }

  return (
    <div
      ref={setContainer}
      className="ipm-pptx-root"
      style={{
        // Zoom is a CSS variable consumed by .ipm-pptx-slide's width calc().
        // Driving width through CSS (not React inline style) means the
        // browser handles the resize natively the instant zoom changes,
        // without waiting for a React re-render of every slide.
        ['--pptx-zoom' as string]: String(scale),
        // Container dimensions expose to slide CSS for fit-page calc.
        ['--pptx-container-w' as string]: `${containerSize.w}px`,
        ['--pptx-container-h' as string]: `${containerSize.h}px`,
        // Scroll-snap is great in fit-page mode (one slide per viewport,
        // wheel settles on the next slide). But when the user has zoomed in,
        // the slide is bigger than the viewport and mandatory snap locks the
        // view to the slide's top edge — they can't pan around the zoomed
        // content. Disable snap whenever scale != 1.
        scrollSnapType: scale === 1 ? 'y mandatory' : 'none',
      }}
    >
      {slides.map((slide, idx) => (
        <SlideItem
          key={idx}
          slide={slide}
          idx={idx}
          rendered={renderedMap[idx]}
          registerRef={registerSlideRef}
        />
      ))}
    </div>
  )
}
