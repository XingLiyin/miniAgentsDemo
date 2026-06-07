import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, ErrorMsg } from './common'
import { Spinner } from '@/components/ui/spinner'
import { parseInWorker } from '../worker/parseClient'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { useI18n } from '@/i18n'
import { slideToHtml } from './pptx/slideToHtml'
import { extractTitle } from './pptx/extractTitle'
import './pptx/pptx.css'

type LoadStage = 'fetching' | 'parsing' | 'rendering' | 'done'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.5
const ZOOM_MAX = 4

interface RenderedSlide {
  idx: number
  html: string
  // Slide's intrinsic aspect ratio, copied from SlideData. Without this every
  // card would fall back to a fixed 16:9, letterboxing 4:3 / A4 decks.
  aspect: number
}

export function PptxViewer({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement>(null)
  const slideRefs = useRef<HTMLDivElement[]>([])
  const [error, setError] = useState<string | null>(null)
  const [combinedCss, setCombinedCss] = useState<string>('')
  const [rendered, setRendered] = useState<RenderedSlide[]>([])
  const [scale, setScale] = useState(1)
  const [current, setCurrent] = useState(1)
  const [toc, setToc] = useState<TocItem[]>([])
  const [stage, setStage] = useState<LoadStage>('fetching')
  // Visible viewport size of the scroll container, used to compute fit-page
  // dimensions so each slide fits within both width AND height (not just width).
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  // Ref-mirrored "current" + "rendered length" so the keyboard listener can be
  // installed once without re-attaching on every scroll tick.
  const currentRef = useRef(1)
  useEffect(() => { currentRef.current = current }, [current])
  const slideCountRef = useRef(0)
  useEffect(() => { slideCountRef.current = rendered.length }, [rendered.length])

  // Load + parse the PPTX. Streams each slide back via worker progress so the
  // first page becomes visible long before the full deck finishes parsing.
  useEffect(() => {
    const ac = new AbortController()
    setError(null); setRendered([]); setCombinedCss(''); setCurrent(1); setToc([])
    setStage('fetching')
    slideRefs.current = []
    // Timing diagnostics — surfaces step-1 (fetch) / step-2+pre-stream (master
    // + layout + theme parse) / step-3 (per-slide loop) durations to the F12
    // console so we can identify which phase is the actual bottleneck without
    // shipping a UI for it.
    const tStart = performance.now()
    let tFetchDone = 0
    let tFirstSlide = 0
    let tLastSlide = 0
    let tPrev = 0
    const perSlideMs: number[] = []
    console.log('[pptx-timing] load start')
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        if (ac.signal.aborted) throw new DOMException('Aborted', 'AbortError')
        tFetchDone = performance.now()
        console.log(`[pptx-timing] fetch+arrayBuffer done: ${(tFetchDone - tStart).toFixed(0)} ms`)
        setStage('parsing')
        return parseInWorker('pptx', buf, {
          signal: ac.signal,
          onProgress: (p) => {
            // Worker emits one progress message per slide it finishes parsing.
            if (ac.signal.aborted) return
            if (!p.slide || typeof p.slideIdx !== 'number') return
            const now = performance.now()
            if (p.slideIdx === 0) {
              tFirstSlide = now
              tPrev = now
              console.log(`[pptx-timing] worker step1+step2 (before first slide): ${(tFirstSlide - tFetchDone).toFixed(0)} ms`)
            } else {
              perSlideMs.push(now - tPrev)
              tPrev = now
            }
            tLastSlide = now
            const slide = p.slide
            const idx = p.slideIdx
            const out = slideToHtml(slide, idx)
            const title = extractTitle(slide)
            const label = title || t('preview.slideN', { n: idx + 1 })
            const prefix = title ? `${idx + 1}. ` : ''
            setCombinedCss((prev) => prev + out.css)
            setRendered((prev) => [...prev, { idx, html: out.html, aspect: slide.width / slide.height }])
            setToc((prev) => [...prev, { id: `slide-${idx}`, label: `${prefix}${label}` }])
            // The first slide on screen → we can transition out of the
            // spinner so the user sees real content, even while later slides
            // are still arriving from the worker.
            if (idx === 0) setStage('done')
          },
        })
      })
      .then((res) => {
        if (ac.signal.aborted) return
        // Edge case: empty deck → onProgress never fired, so leave the
        // spinner state in place and render "no slides" gracefully.
        if (res && res.slides.length === 0) setStage('done')
        if (res && tFirstSlide > 0) {
          console.log(`[pptx-timing] streaming step3 (${res.slides.length} slides): ${(tLastSlide - tFirstSlide).toFixed(0)} ms`)
          console.log(`[pptx-timing] total: ${(performance.now() - tStart).toFixed(0)} ms`)
          if (perSlideMs.length > 0) {
            const avg = perSlideMs.reduce((a, b) => a + b, 0) / perSlideMs.length
            const slowest = perSlideMs
              .map((ms, i) => ({ slide: i + 2, ms }))  // i=0 corresponds to slide #2 (slide #1 is the first one, no delta)
              .sort((a, b) => b.ms - a.ms)
              .slice(0, 5)
            console.log(`[pptx-timing] per-slide avg: ${avg.toFixed(0)} ms — slowest 5:`, slowest.map(s => `#${s.slide}=${s.ms.toFixed(0)}ms`).join(', '))
          }
        }
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
        }
      })
    return () => ac.abort()
  }, [path, t])

  // ResizeObserver attached via callback ref. We CANNOT do this in a useEffect
  // with deps=[] because the container <div> is only rendered when stage==='done';
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

  // IntersectionObserver: track which slide centre is in the viewport.
  useEffect(() => {
    const container = containerRef.current
    if (!container || rendered.length === 0) return
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
  }, [rendered])

  // Register toolbar capabilities.
  usePreviewToolbar({
    pages: {
      count: rendered.length,
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
  }, [rendered.length, current, scale, toc, path, filename])

  if (error) return <ErrorMsg msg={error} />
  if (stage !== 'done') {
    const label =
      stage === 'fetching' ? t('preview.pptxFetching') :
      stage === 'parsing'  ? t('preview.pptxParsing')  :
                             t('preview.pptxRendering')
    return (
      <div className="flex h-full items-center justify-center gap-3 text-sm" style={{ color: 'var(--t3)' }}>
        <Spinner className="h-4 w-4" /> <span>{label}</span>
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
      <style dangerouslySetInnerHTML={{ __html: combinedCss }} />
      {rendered.map((s) => (
        // .ipm-pptx-slide-page is a full-viewport "page" container. Its
        // min-height = container height so consecutive page wrappers don't
        // share viewport space — scroll-snap then pages cleanly one at a
        // time, with no leftover of the previous/next slide visible.
        <div
          key={s.idx}
          ref={(el) => { if (el) slideRefs.current[s.idx] = el }}
          className="ipm-pptx-slide-page"
          data-idx={s.idx}
        >
          <div
            className={`ipm-pptx-slide sld-${s.idx}`}
            style={{ ['--slide-aspect' as string]: String(s.aspect) }}
          >
            {/* slide-inner is NID's absolute-positioning container. Shape
                <div>s from _buildShapeParts position relative to it. */}
            <div className="slide-inner" dangerouslySetInnerHTML={{ __html: s.html }} />
          </div>
        </div>
      ))}
    </div>
  )
}
