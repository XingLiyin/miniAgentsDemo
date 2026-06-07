// Parse kinds handled by the shared worker. DOCX (docx-preview, DOM-bound) and
// PDF (pdfjs's own worker) do NOT go through here.
export type ParseKind = 'xlsx' | 'pptx'

export interface ParseProgress {
  phase: string
  loaded?: number
  total?: number
  /** For kind='pptx' streaming: a single slide just parsed in the worker.
   * The viewer can render this slide immediately so the first page is
   * visible long before the full deck finishes parsing on a slow doc. */
  slide?: import('./parsers/pptx').SlideData
  slideIdx?: number
  /** Free-form diagnostic message routed through the worker → main thread
   * progress channel so it appears in the renderer's DevTools console
   * (the worker's own console output is filtered out by default). */
  diag?: string
}

export interface ParseRequestMsg {
  type: 'parse'
  id: string
  kind: ParseKind
  buffer: ArrayBuffer
  options?: Record<string, unknown>
}
export interface ParseProgressMsg { type: 'progress'; id: string; progress: ParseProgress }
export interface ParseResultMsg { type: 'result'; id: string; kind: ParseKind; data: unknown }
export interface ParseErrorMsg { type: 'error'; id: string; error: string }

export type WorkerOutMsg = ParseProgressMsg | ParseResultMsg | ParseErrorMsg

// Result shape for kind 'xlsx'
export interface SheetData { name: string; rows: string[][] }

// Result shape for kind 'pptx'. Slides + themeFonts come from parsePptx().
// SlideData is re-exported from the parser module to keep the protocol file
// dependency-free (parsing types live with the parser); the protocol just
// declares the shape under ParseResultData.
export interface PptxResult {
  slides: import('./parsers/pptx').SlideData[]
  themeFonts: Record<string, string>
}

// Maps each ParseKind to its result payload type. The parse client uses this
// to type its return value.
export interface ParseResultData {
  xlsx: SheetData[]
  pptx: PptxResult
}
