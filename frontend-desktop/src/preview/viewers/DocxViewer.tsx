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
// WordprocessingDrawing namespace (wp:anchor, wp:positionV, wp:posOffset)
const WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'

// mc:Choice Requires values that docx-preview cannot render.
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
 * docx-preview cannot render wps:txbx text boxes OR the VML <v:textbox> in
 * mc:Fallback. So we extract the <w:p> paragraphs from wps:txbxContent and
 * inject them into the document flow so they are visible.
 *
 * Sorting: floating text boxes are positioned relative to their anchor
 * paragraph via wp:positionV/wp:posOffset. We estimate each box's visual
 * Y by (anchorParaIndex × EMU_per_para + posOffset) and insert all boxes in
 * that order just before the first section break — so the content appears in
 * approximately the correct visual sequence even though positioning is
 * approximate (document-flow, not absolute).
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
      if (xmlDoc.querySelector('parsererror')) return

      const altNodes = Array.from(xmlDoc.getElementsByTagNameNS(MC_NS, 'AlternateContent'))

      type TBEntry = {
        hostRun: Element | null
        ac: Element
        extracted: Node[]
        sortKey: number  // estimated visual Y in EMU
      }
      const entries: TBEntry[] = []

      for (const ac of altNodes) {
        if (!ac.parentNode) continue // already detached

        const wpsChoice = Array.from(ac.getElementsByTagNameNS(MC_NS, 'Choice')).find((ch) => {
          const req = (ch.getAttribute('Requires') ?? '').trim()
          return req.split(/\s+/).some((r) => UNSUPPORTED_REQUIRES.has(r))
        })
        if (!wpsChoice) continue

        // Anchor paragraph + its position among siblings
        const hostPara = findAncestor(ac, 'p', W_NS)
        const hostRun  = findAncestor(ac, 'r', W_NS)
        const paraIndex = hostPara?.parentNode
          ? Array.from(hostPara.parentNode.childNodes).indexOf(hostPara)
          : 0

        // Vertical posOffset in EMU (wp:positionV → wp:posOffset)
        let posOffsetV = 0
        const posVList = wpsChoice.getElementsByTagNameNS(WP_NS, 'positionV')
        if (posVList.length > 0) {
          const offEl = posVList[0].getElementsByTagNameNS(WP_NS, 'posOffset')[0]
          posOffsetV = parseInt(offEl?.textContent ?? '0', 10) || 0
        }
        // 228 600 EMU ≈ 18 pt — estimated height of one empty body paragraph
        const sortKey = paraIndex * 228_600 + posOffsetV

        // Extract <w:p> elements from wps:txbxContent
        const extracted: Node[] = []
        for (const txbx of Array.from(wpsChoice.getElementsByTagNameNS(WPS_NS, 'txbx'))) {
          const content = txbx.getElementsByTagNameNS(W_NS, 'txbxContent')[0]
          if (!content) continue
          for (const p of Array.from(content.getElementsByTagNameNS(W_NS, 'p'))) {
            extracted.push(p.cloneNode(true))
          }
        }

        entries.push({ hostRun, ac, extracted, sortKey })
      }

      if (entries.length === 0) return

      // Sort by estimated visual Y (ascending) so we process top-to-bottom.
      entries.sort((a, b) => a.sortKey - b.sortKey)

      // ── Map each text box to a body paragraph that acts as its spacer ──
      // The body paragraphs are (mostly) empty; their cumulative heights
      // approximate the vertical positions on the page. We insert each text
      // box's paragraphs after whichever body paragraph corresponds to its
      // estimated Y position, so the empty paragraphs provide natural spacing.
      const body = xmlDoc.getElementsByTagNameNS(W_NS, 'body')[0]
      if (!body) return

      // Snapshot direct-child <w:p> elements BEFORE any mutation.
      const bodyParas = Array.from(body.childNodes).filter(
        (n): n is Element =>
          n.nodeType === 1 &&
          (n as Element).localName === 'p' &&
          (n as Element).namespaceURI === W_NS,
      )
      if (bodyParas.length === 0) return

      // Estimated height of one empty body paragraph in EMU (≈18 pt).
      const PARA_H = 228_600

      // Pre-compute insertion points ONCE (snapshot nextSibling before
      // any insertions, so that multiple boxes at the same target paragraph
      // are stacked in sorted order rather than interleaved incorrectly).
      const targetIdxOf = new Map<TBEntry, number>()
      const insertPtOf  = new Map<number, Node | null>()
      for (const entry of entries) {
        const idx = Math.min(Math.floor(entry.sortKey / PARA_H), bodyParas.length - 1)
        targetIdxOf.set(entry, idx)
        if (!insertPtOf.has(idx)) {
          insertPtOf.set(idx, bodyParas[idx].nextSibling)
        }
      }

      // Insert in sorted order (top → bottom of page).
      for (const entry of entries) {
        const insertPt = insertPtOf.get(targetIdxOf.get(entry)!) ?? null
        for (const p of entry.extracted) {
          if (insertPt) body.insertBefore(p, insertPt)
          else body.appendChild(p)
        }
      }

      // Remove anchor runs (and by extension the mc:AlternateContent).
      for (const { hostRun, ac } of entries) {
        if (hostRun?.parentNode) {
          hostRun.parentNode.removeChild(hostRun)
        } else if (ac.parentNode) {
          ac.parentNode.removeChild(ac)
        }
      }

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
