import { useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import './docx/docx.css'

const ZOOM_STEP = 0.1
const ZOOM_MIN = 0.5
const ZOOM_MAX = 3

/**
 * High-fidelity DOCX viewer backed by docx-preview, which parses the OOXML and
 * reproduces the Word page layout (fonts, spacing, headers/footers, footnotes,
 * page breaks) — vs the old mammoth path that flattened everything to semantic
 * HTML and lost the layout. docx-preview renders directly into a DOM container
 * (it is DOM-bound, so this runs on the main thread, not a worker).
 *
 * Known limitation: embedded EMF/WMF metafiles render as broken <img> (browser
 * can't display them and docx-preview owns the image extraction, so the
 * Electron GDI+ converter used for PPTX can't be hooked in here).
 */
export function DocxViewer({ path, filename }: { path: string; filename: string }) {
  // The container docx-preview renders into. Always mounted (even while
  // loading) so the ref is available when the async render resolves.
  const containerRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [scale, setScale] = useState(1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then(async (buf) => {
        if (cancelled) return
        const container = containerRef.current
        if (!container) return
        container.innerHTML = ''  // clear any previous document on path change
        const docx = await import('docx-preview')
        if (cancelled) return
        await docx.renderAsync(buf, container, undefined, {
          className: 'docx',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          ignoreFonts: false,
          breakPages: true,
          useBase64URL: true,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
        })
        if (cancelled) return
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(String(e))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [path])

  usePreviewToolbar({
    zoom: {
      scale,
      in: () => setScale((s) => Math.min(ZOOM_MAX, Math.round((s + ZOOM_STEP) * 100) / 100)),
      out: () => setScale((s) => Math.max(ZOOM_MIN, Math.round((s - ZOOM_STEP) * 100) / 100)),
      fit: () => setScale(1),
      reset: () => setScale(1),
    },
    download: { url: rawUrl(path), filename },
  }, [scale, path, filename])

  if (error) return <ErrorMsg msg={error} />

  return (
    <div className="ipm-docx-root">
      {loading && <div className="ipm-docx-loading"><Loading /></div>}
      <div className="ipm-docx-scroll">
        <div
          ref={containerRef}
          className="ipm-docx-container"
          style={{ transform: `scale(${scale})` }}
        />
      </div>
    </div>
  )
}
