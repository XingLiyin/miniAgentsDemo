// Parse kinds handled by the shared worker. DOCX (docx-preview, DOM-bound) and
// PDF (pdfjs's own worker) do NOT go through here. 'pptx' is added in Phase 2.
export type ParseKind = 'xlsx'

export interface ParseProgress { phase: string; loaded?: number; total?: number }

export interface ParseRequestMsg {
  type: 'parse'
  id: string
  kind: ParseKind
  buffer: ArrayBuffer
  options?: Record<string, unknown>
}
export interface ParseProgressMsg { type: 'progress'; id: string; progress: ParseProgress }
export interface ParseResultMsg { type: 'result'; id: string; data: unknown }
export interface ParseErrorMsg { type: 'error'; id: string; error: string }

export type WorkerOutMsg = ParseProgressMsg | ParseResultMsg | ParseErrorMsg

// Result shape for kind 'xlsx'
export interface SheetData { name: string; rows: string[][] }
