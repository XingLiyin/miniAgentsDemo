import { useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import './docx/docx.css'

// Markup-Compatibility namespace (OOXML spec §16)
const MC_NS = 'http://schemas.openxmlformats.org/markup-compatibility/2006'

// Features that docx-preview doesn't support but whose mc:Choice it picks up,
// causing content to silently disappear. We strip these Choice blocks so
// docx-preview falls back to the VML path which it CAN render.
const UNSUPPORTED_REQUIRES = new Set(['wps', 'wpg', 'wpc'])

/**
 * Pre-processes a DOCX ArrayBuffer so that mc:AlternateContent blocks whose
 * mc:Choice requires Word-Processing-Shape (wps/wpg/wpc) are stripped,
 * leaving only the mc:Fallback (VML) path for docx-preview to render.
 *
 * Without this, docx-preview picks the DrawingML Choice, fails to render
 * wps:txbx text boxes, and cover pages / floating text boxes go blank.
 */
async function stripUnsupportedChoices(buf: ArrayBuffer): Promise<ArrayBuffer> {
  const JSZip = (await import('jszip')).default
  const zip = await JSZip.loadAsync(buf)

  const xmlEntries = Object.entries(zip.files).filter(
    ([name]) => name.startsWith('word/') && name.endsWith('.xml') && !zip.files[name].dir,
  )

  await Promise.all(
    xmlEntries.map(async ([name, file]) => {
      const src = await file.async('string')
      if (!src.includes('AlternateContent')) return // fast skip

      const parser = new DOMParser()
      const xmlDoc = parser.parseFromString(src, 'application/xml')
      const parseErr = xmlDoc.querySelector('parsererror')
      if (parseErr) return // leave untouched on parse error

      // Snapshot the live NodeList into an array before we mutate the tree.
      const altNodes = Array.from(xmlDoc.getElementsByTagNameNS(MC_NS, 'AlternateContent'))
      let mutated = false

      for (const ac of altNodes) {
        if (!ac.parentNode) continue // already removed (nested inside another AC)

        // Does any Choice require an unsupported feature?
        const choices = Array.from(ac.getElementsByTagNameNS(MC_NS, 'Choice'))
        const hasUnsupported = choices.some((ch) => {
          const req = (ch.getAttribute('Requires') ?? '').trim()
          return req.split(/\s+/).some((r) => UNSUPPORTED_REQUIRES.has(r))
        })
        if (!hasUnsupported) continue

        // Replace the whole AlternateContent with the Fallback's children.
        const fallback = ac.getElementsByTagNameNS(MC_NS, 'Fallback')[0]
        const parent = ac.parentNode
        if (fallback) {
          // Move fallback children into parent, in place of ac.
          const children = Array.from(fallback.childNodes)
          for (const child of children) {
            parent.insertBefore(child, ac)
          }
        }
        parent.removeChild(ac)
        mutated = true
      }

      if (!mutated) return
      zip.file(name, new XMLSerializer().serializeToString(xmlDoc))
    }),
  )

  return zip.generateAsync({ type: 'arraybuffer', compression: 'DEFLATE' })
}

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
        // Pre-process: strip mc:Choice blocks for wps/wpg/wpc so docx-preview
        // falls back to VML rendering of text boxes (cover pages, etc.).
        const processedBuf = await stripUnsupportedChoices(buf)
        if (cancelled) return
        await docx.renderAsync(processedBuf, container, undefined, {
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
