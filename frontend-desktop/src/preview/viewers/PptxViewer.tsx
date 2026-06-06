import { useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import { parseInWorker } from '../worker/parseClient'
import type { PptxResult } from '../worker/protocol'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { useI18n } from '@/i18n'
import { slideToHtml } from './pptx/slideToHtml'
import { extractTitle } from './pptx/extractTitle'
import './pptx/pptx.css'

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
  const [result, setResult] = useState<PptxResult | null>(null)
  const [combinedCss, setCombinedCss] = useState<string>('')
  const [rendered, setRendered] = useState<RenderedSlide[]>([])
  const [scale, setScale] = useState(1)
  const [current, setCurrent] = useState(1)
  const [toc, setToc] = useState<TocItem[]>([])

  // Load + parse the PPTX.
  useEffect(() => {
    const ac = new AbortController()
    setError(null); setResult(null); setRendered([]); setCombinedCss(''); setCurrent(1); setToc([])
    // Drop stale ref entries from the previously-loaded deck — otherwise after
    // 30-slide → 6-slide switches the array keeps 24 detached DOM nodes (the
    // ref callback only ever ASSIGNS, never nulls), which the IntersectionObserver
    // would then try to observe.
    slideRefs.current = []
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => parseInWorker('pptx', buf, { signal: ac.signal }))
      .then((res) => {
        if (ac.signal.aborted) return
        setResult(res)
        // Render all slides + assemble CSS.
        let css = ''
        const html: RenderedSlide[] = []
        for (let i = 0; i < res.slides.length; i++) {
          const slide = res.slides[i]
          const out = slideToHtml(slide, i)
          css += out.css
          html.push({ idx: i, html: out.html, aspect: slide.width / slide.height })
        }
        setCombinedCss(css)
        setRendered(html)
        // Build TOC.
        const tocItems: TocItem[] = res.slides.map((slide, i) => {
          const title = extractTitle(slide)
          const label = title || t('preview.slideN', { n: i + 1 })
          const prefix = title ? `${i + 1}. ` : ''
          return { id: `slide-${i}`, label: `${prefix}${label}` }
        })
        setToc(tocItems)
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
        }
      })
    return () => ac.abort()
  }, [path, t])

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
  if (result === null) return <Loading />

  return (
    <div
      ref={containerRef}
      className="ipm-pptx-root"
      style={{ ['--pptx-zoom' as string]: String(scale) }}
    >
      <style dangerouslySetInnerHTML={{ __html: combinedCss }} />
      {rendered.map((s) => (
        <div
          key={s.idx}
          ref={(el) => { if (el) slideRefs.current[s.idx] = el }}
          className={`ipm-pptx-slide sld-${s.idx}`}
          data-idx={s.idx}
          style={{ aspectRatio: `${s.aspect}` }}
        >
          {/* slide-inner is NID's absolute-positioning container. Shape <div>s
              from _buildShapeParts position relative to it. */}
          <div className="slide-inner" dangerouslySetInnerHTML={{ __html: s.html }} />
        </div>
      ))}
    </div>
  )
}
