/**
 * PPTX rendering worker. Pure-string slideToHtml work — the heavy NID-ported
 * _buildShapeParts string-building that takes ~700ms per slide and ~5s on
 * slides backed by the busiest layouts — moves off the main thread here so
 * the modal stays interactive (close button responsive) while the deck
 * fills in progressively.
 *
 * Parsing stays on the main thread because it needs the native DOMParser
 * (not available in DedicatedWorkerGlobalScope per spec). That parse is
 * ~900ms for a 45-slide deck and isn't worth offloading; the slow part
 * is the post-parse rendering, which is what this worker handles.
 *
 * Protocol:
 *   Main → worker:  { slides: SlideData[] }   — sent once after parse
 *   Worker → main:  { idx, css, html }        — one per slide, as each renders
 *
 * The worker emits results IMMEDIATELY after each slide finishes so the main
 * thread can paint them progressively. No batching; the rendering itself is
 * the limiting factor.
 */
import { slideToHtml } from '../viewers/pptx/slideToHtml'
import type { SlideData } from './parsers/pptx'

interface RenderRequest {
  slide: SlideData
  idx: number
}

interface RenderedSlideMsg {
  idx: number
  css: string
  html: string
}

const ctx = self as unknown as {
  onmessage: ((ev: MessageEvent<RenderRequest>) => void) | null
  postMessage: (m: RenderedSlideMsg) => void
}

// One slide per message. The previous "send all 45 slides in one postMessage"
// approach blocked the main thread for ~8s in structured-clone (each shape's
// nested paragraphs/runs/images all need to be cloned char-by-char on the
// sender side — 45 slides × heavy master shapes amounted to multi-MB
// payloads). Per-slide messages each clone in 10-50ms, distributing the
// cost so the main thread can paint between sends.
ctx.onmessage = (ev) => {
  const { slide, idx } = ev.data
  const out = slideToHtml(slide, idx)
  ctx.postMessage({ idx, css: out.css, html: out.html })
}
