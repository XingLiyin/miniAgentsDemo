import { useEffect, useRef, useState } from 'react'
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

  // Load + parse the PPTX.
  useEffect(() => {
    const ac = new AbortController()
    setError(null); setRendered([]); setCombinedCss(''); setCurrent(1); setToc([])
    setStage('fetching')
    slideRefs.current = []
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        if (ac.signal.aborted) throw new DOMException('Aborted', 'AbortError')
        setStage('parsing')
        return parseInWorker('pptx', buf, { signal: ac.signal })
      })
      .then((res) => {
        if (ac.signal.aborted) return
        setStage('rendering')
        // Defer the heavy slideToHtml + setState a tick so the "rendering"
        // label paints before the main thread blocks. Without this the user
        // sees no feedback between "parsing" and the fully-rendered viewer.
        return new Promise<void>((resolve) => {
          setTimeout(() => {
            if (ac.signal.aborted) { resolve(); return }
            let css = ''
            const html: RenderedSlide[] = []
            for (let i = 0; i < res.slides.length; i++) {
              const slide = res.slides[i]
              const out = slideToHtml(slide, i)
              css += out.css
              html.push({ idx: i, html: out.html, aspect: slide.width / slide.height })
            }
            const tocItems: TocItem[] = res.slides.map((slide, i) => {
              const title = extractTitle(slide)
              const label = title || t('preview.slideN', { n: i + 1 })
              const prefix = title ? `${i + 1}. ` : ''
              return { id: `slide-${i}`, label: `${prefix}${label}` }
            })
            setCombinedCss(css)
            setRendered(html)
            setToc(tocItems)
            setStage('done')
            resolve()
          }, 0)
        })
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
        }
      })
    return () => ac.abort()
  }, [path, t])

  // ResizeObserver: track the scroll container's visible size so the slides
  // can be sized to fit-page (width AND height) rather than just fit-width.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
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
      ref={containerRef}
      className="ipm-pptx-root"
      style={{
        ['--pptx-zoom' as string]: String(scale),
        // Scroll-snap is great in fit-page mode (one slide per viewport, wheel
        // settles on the next slide). But when the user has zoomed in, the
        // slide is bigger than the viewport and mandatory snap locks the view
        // to the slide's top edge — they can't actually pan around the zoomed
        // content. Disable snap whenever scale != 1.
        scrollSnapType: scale === 1 ? 'y mandatory' : 'none',
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: combinedCss }} />
      {rendered.map((s) => {
        // Fit-page: each slide must fit within both container width and height.
        // We compute the slide width as min(width-bound, height-bound) so a
        // 16:9 deck inside a 4:3-ish modal isn't taller than the visible area.
        // SIDE_MARGIN provides the white space the user wanted around each
        // slide; TOP_MARGIN matches the per-slide CSS margin (so a fully-fit
        // slide can scroll-snap cleanly to the next).
        const SIDE_MARGIN = 48
        const TOP_MARGIN = 48
        const fitW = containerSize.w > 0
          ? Math.min(
              containerSize.w - SIDE_MARGIN,
              (containerSize.h - TOP_MARGIN) * s.aspect,
            )
          : 0
        const slideW = fitW > 0 ? fitW * scale : 0
        return (
          <div
            key={s.idx}
            ref={(el) => { if (el) slideRefs.current[s.idx] = el }}
            className={`ipm-pptx-slide sld-${s.idx}`}
            data-idx={s.idx}
            style={{
              aspectRatio: `${s.aspect}`,
              width: slideW > 0 ? `${slideW}px` : undefined,
            }}
          >
            {/* slide-inner is NID's absolute-positioning container. Shape <div>s
                from _buildShapeParts position relative to it. */}
            <div className="slide-inner" dangerouslySetInnerHTML={{ __html: s.html }} />
          </div>
        )
      })}
    </div>
  )
}
