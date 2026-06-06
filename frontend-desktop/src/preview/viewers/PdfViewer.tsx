import { useEffect, useRef, useState } from 'react'
import { EventBus, PDFViewer, PDFLinkService, PDFFindController } from 'pdfjs-dist/web/pdf_viewer.mjs'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { pdfjsLib, CMAP_URL, CMAP_PACKED } from './pdf/pdfSetup'
import { flattenOutline, type OutlineDest } from './pdf/outline'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import './pdf/pdf.css'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.25
const ZOOM_MAX = 5

interface PdfApi {
  viewer: PDFViewer
  linkService: PDFLinkService
  eventBus: EventBus
  dests: Map<string, OutlineDest>
}

export function PdfViewer({ path, filename }: { path: string; filename: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerElRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<PdfApi | null>(null)
  const lastQuery = useRef('')

  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [scale, setScale] = useState(1)
  const [page, setPage] = useState({ current: 1, count: 0 })
  const [matches, setMatches] = useState({ current: 0, total: 0 })
  const [toc, setToc] = useState<TocItem[]>([])

  // Build the pdfjs stack + load the document. Re-runs on path change.
  useEffect(() => {
    const container = containerRef.current!
    const viewerEl = viewerElRef.current!
    let destroyed = false
    let pdfDoc: PDFDocumentProxy | null = null

    setError(null); setReady(false); setToc([]); setMatches({ current: 0, total: 0 })

    const eventBus = new EventBus()
    const linkService = new PDFLinkService({ eventBus })
    const findController = new PDFFindController({ eventBus, linkService })
    const viewer = new PDFViewer({ container, viewer: viewerEl, eventBus, linkService, findController })
    linkService.setViewer(viewer)

    eventBus.on('pagesinit', () => {
      viewer.currentScaleValue = 'page-width'
      setReady(true)
    })
    eventBus.on('scalechanging', (e: { scale: number }) => setScale(e.scale))
    eventBus.on('pagechanging', (e: { pageNumber: number }) => setPage((p) => ({ ...p, current: e.pageNumber })))
    eventBus.on('updatefindmatchescount', (e: { matchesCount: { current: number; total: number } }) =>
      setMatches(e.matchesCount))
    eventBus.on('updatefindcontrolstate', (e: { matchesCount: { current: number; total: number } }) =>
      setMatches(e.matchesCount))

    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((data) => pdfjsLib.getDocument({ data, cMapUrl: CMAP_URL, cMapPacked: CMAP_PACKED }).promise)
      .then(async (doc) => {
        if (destroyed) { doc.destroy(); return }
        pdfDoc = doc
        viewer.setDocument(doc)
        linkService.setDocument(doc, null)
        setPage({ current: 1, count: doc.numPages })
        const outline = await doc.getOutline().catch(() => null)
        const flat = flattenOutline(outline as never)
        apiRef.current = { viewer, linkService, eventBus, dests: flat.dests }
        if (!destroyed) setToc(flat.items)
      })
      .catch((e) => { if (!destroyed) setError(String(e)) })

    return () => {
      destroyed = true
      apiRef.current = null
      try { viewer.setDocument(null as never) } catch { /* ignore */ }
      pdfDoc?.destroy()
    }
  }, [path])

  // pdfjs's find controller replaces its whole state on every 'find' dispatch, so
  // 'again' (next/prev) must re-send the query + flags or it wipes the active search.
  function dispatchFind(again: boolean, findPrevious: boolean) {
    apiRef.current?.eventBus.dispatch('find', {
      source: null, type: again ? 'again' : '', query: lastQuery.current,
      caseSensitive: false, entireWord: false, highlightAll: true, findPrevious,
    })
  }

  // Register toolbar capabilities; re-register when live state changes.
  usePreviewToolbar({
    zoom: {
      in: () => { const v = apiRef.current?.viewer; if (v) v.currentScale = Math.min(ZOOM_MAX, v.currentScale + ZOOM_STEP) },
      out: () => { const v = apiRef.current?.viewer; if (v) v.currentScale = Math.max(ZOOM_MIN, v.currentScale - ZOOM_STEP) },
      reset: () => { const v = apiRef.current?.viewer; if (v) v.currentScale = 1 },
      fit: () => { const v = apiRef.current?.viewer; if (v) v.currentScaleValue = 'page-width' },
      scale,
    },
    pages: {
      count: page.count,
      current: page.current,
      goto: (n: number) => { const v = apiRef.current?.viewer; if (v) v.currentPageNumber = n },
    },
    search: {
      run: (query: string) => { lastQuery.current = query; dispatchFind(false, false) },
      next: () => dispatchFind(true, false),
      prev: () => dispatchFind(true, true),
      clear: () => { lastQuery.current = ''; apiRef.current?.eventBus.dispatch('findbarclose', { source: null }) },
      count: matches.total,
      current: matches.current,
    },
    ...(toc.length > 0 ? {
      toc: {
        items: toc,
        goto: (id: string) => {
          const api = apiRef.current
          const dest = api?.dests.get(id)
          if (api && dest != null) void api.linkService.goToDestination(dest)
        },
      },
    } : {}),
    download: { url: rawUrl(path), filename },
  }, [scale, page.current, page.count, matches.total, matches.current, toc, path, filename])

  if (error) return <ErrorMsg msg={error} />

  return (
    <div className="relative h-full w-full">
      {!ready && <div className="absolute inset-0 z-10"><Loading /></div>}
      <div ref={containerRef} className="ipm-pdf-container">
        <div ref={viewerElRef} className="pdfViewer" />
      </div>
    </div>
  )
}
