import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, ErrorMsg } from './common'
import { Spinner } from '@/components/ui/spinner'
import { parsePptx, type SlideData } from '../worker/parsers/pptx'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { useI18n } from '@/i18n'
import { slideToHtml } from './pptx/slideToHtml'
import { extractTitle } from './pptx/extractTitle'
import './pptx/pptx.css'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.5
const ZOOM_MAX = 4

/**
 * Per-slide lazy renderer. The expensive work — slideToHtml's NID-ported
 * _buildShapeParts string-building over master + layout + slide shapes — runs
 * ONLY when this slide is near the viewport. Without virtualisation the
 * 45-slide test deck took 31s of synchronous main-thread work for
 * slideToHtml after the 1.1s parse, blocking the close button and freezing
 * the UI. With it, the deck's render budget is amortised over actual
 * viewing: every slide costs ~700ms when it scrolls into view, but slides
 * the user never visits cost nothing.
 *
 * Aspect ratio is taken from the parsed slide (width/height) so the page
 * wrapper reserves the right vertical space BEFORE rendering — scroll
 * position is therefore stable when later slides finally render in.
 */
function SlideItem({
  slide,
  idx,
  registerRef,
}: {
  slide: SlideData
  idx: number
  registerRef: (idx: number, el: HTMLDivElement | null) => void
}) {
  const [rendered, setRendered] = useState<{ html: string; css: string } | null>(null)
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (rendered) return
    const el = ref.current
    if (!el) return
    // rootMargin pre-renders slides 500px before/after the visible window so
    // the user almost never sees a blank slide while scrolling at normal speed.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          const out = slideToHtml(slide, idx)
          setRendered(out)
          // One-shot: once rendered, we don't need to keep observing.
          observer.disconnect()
        }
      },
      { rootMargin: '500px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [slide, idx, rendered])

  return (
    // .ipm-pptx-slide-page is a full-viewport "page" container. Its
    // min-height = container height so consecutive page wrappers don't
    // share viewport space — scroll-snap then pages cleanly one at a
    // time, with no leftover of the previous/next slide visible.
    <div
      ref={(el) => { ref.current = el; registerRef(idx, el) }}
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
}

export function PptxViewer({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement>(null)
  const slideRefs = useRef<HTMLDivElement[]>([])
  const [error, setError] = useState<string | null>(null)
  const [slides, setSlides] = useState<SlideData[]>([])
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
  const registerSlideRef = useCallback((idx: number, el: HTMLDivElement | null) => {
    if (el) slideRefs.current[idx] = el
  }, [])

  // Load + parse the PPTX. parsePptx runs ON THE MAIN THREAD using the native
  // DOMParser — see the parser file header for context. The total budget here
  // is fetch (~70ms) + parse (~1.1s) + extractTitle × N (~10ms). slideToHtml
  // is NOT called here — each SlideItem renders itself lazily on viewport
  // entry. So the loading spinner clears in ~1.2s for a 45-slide deck even
  // though full-deck render work is still ~30s amortised.
  useEffect(() => {
    let cancelled = false
    setError(null); setSlides([]); setCurrent(1); setToc([])
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
        // Cheap eager pass: titles for the TOC. extractTitle is ~milliseconds
        // per slide so doing 45 here is well under 100ms total — worth it
        // for an immediately-populated TOC.
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
        console.log(`[pptx-timing] total (parse + titles, slides rendered lazily): ${(performance.now() - tStart).toFixed(0)} ms`)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
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

  // IntersectionObserver: track which slide centre is in the viewport. This is
  // separate from the per-SlideItem rendering observer — this one is about
  // "current slide" indicator in the toolbar, observes ALL slides, never disconnects.
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
          registerRef={registerSlideRef}
        />
      ))}
    </div>
  )
}
