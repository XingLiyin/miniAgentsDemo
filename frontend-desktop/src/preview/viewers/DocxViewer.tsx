import { useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import './docx/docx.css'

// Markup-Compatibility namespace (OOXML spec §16)
const MC_NS = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
// WordprocessingML namespace
const W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
// Word Processing Shape namespace (wps:wsp, wps:txbx)
const WPS_NS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'

// mc:Choice Requires values that docx-preview cannot render.
// We extract the text content from these text boxes and inject it into the
// document flow so it is visible, rather than silently dropping it.
const UNSUPPORTED_REQUIRES = new Set(['wps', 'wpg', 'wpc'])

/** Walk up the DOM tree looking for the nearest ancestor with the given local name + NS. */
function findAncestor(node: Node, localName: string, ns: string): Element | null {
  let cur: Node | null = node.parentNode
  while (cur) {
    if (cur.nodeType === 1 /* ELEMENT_NODE */) {
      const el = cur as Element
      if (el.localName === localName && el.namespaceURI === ns) return el
    }
    cur = cur.parentNode
  }
  return null
}

/**
 * Pre-processes a DOCX ArrayBuffer so that mc:AlternateContent blocks whose
 * mc:Choice requires wps/wpg/wpc (Word Processing Shapes — floating text
 * boxes, grouped shapes, etc.) are handled gracefully.
 *
 * docx-preview cannot render wps:txbx text boxes AND also cannot render
 * the VML <v:textbox> in the mc:Fallback. So instead of using the fallback,
 * we extract the <w:p> paragraphs from inside wps:txbxContent and inject
 * them as regular document-flow paragraphs right after the host paragraph
 * that anchored the text box. The positioning won't be pixel-perfect, but
 * the text becomes visible rather than disappearing entirely.
 *
 * The containing mc:AlternateContent (and its parent <w:r> run) is then
 * removed from the host paragraph.
 */
async function inlineWpsTextBoxes(buf: ArrayBuffer): Promise<ArrayBuffer> {
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
      if (xmlDoc.querySelector('parsererror')) return // leave untouched on parse error

      // Snapshot before mutation — we process outer ACs; inner ones (inside
      // txbxContent) will also appear here but their host paragraph will be
      // inside the MC tree (already detached after we remove the outer AC),
      // so the `!ac.parentNode` guard below skips them safely.
      const altNodes = Array.from(xmlDoc.getElementsByTagNameNS(MC_NS, 'AlternateContent'))
      let mutated = false

      for (const ac of altNodes) {
        if (!ac.parentNode) continue // already detached

        // Does any Choice require an unsupported feature?
        const wpsChoice = Array.from(ac.getElementsByTagNameNS(MC_NS, 'Choice')).find((ch) => {
          const req = (ch.getAttribute('Requires') ?? '').trim()
          return req.split(/\s+/).some((r) => UNSUPPORTED_REQUIRES.has(r))
        })
        if (!wpsChoice) continue

        // ── Extract <w:p> paragraphs from all wps:txbx elements ──────────
        const extracted: Node[] = []
        for (const txbx of Array.from(wpsChoice.getElementsByTagNameNS(WPS_NS, 'txbx'))) {
          const content = txbx.getElementsByTagNameNS(W_NS, 'txbxContent')[0]
          if (!content) continue
          for (const p of Array.from(content.getElementsByTagNameNS(W_NS, 'p'))) {
            extracted.push(p.cloneNode(true))
          }
        }

        // ── Find the host <w:p> paragraph that anchors this text box ──────
        // The AC lives inside a <w:r> run inside a <w:p> body paragraph.
        const hostPara = findAncestor(ac, 'p', W_NS)

        if (hostPara && hostPara.parentNode && extracted.length > 0) {
          // Insert extracted paragraphs immediately after the host paragraph.
          // (insertBefore with nextSibling = insert after)
          const insertBefore = hostPara.nextSibling
          for (const p of extracted) {
            hostPara.parentNode.insertBefore(p, insertBefore)
          }
        }

        // ── Remove the <w:r> run that contained the mc:AlternateContent ──
        // (The run held only <w:rPr> + the AC, so removing it entirely is safe.)
        const hostRun = findAncestor(ac, 'r', W_NS)
        if (hostRun && hostRun.parentNode) {
          hostRun.parentNode.removeChild(hostRun)
        } else {
          ac.parentNode?.removeChild(ac)
        }

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
        // Pre-process: extract wps text-box paragraphs into the document flow
        // so cover pages / floating text boxes are visible (docx-preview cannot
        // render either wps:txbx DrawingML or v:textbox VML text content).
        const processedBuf = await inlineWpsTextBoxes(buf)
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
