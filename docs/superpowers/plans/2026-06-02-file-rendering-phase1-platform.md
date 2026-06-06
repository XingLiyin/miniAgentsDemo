# File Rendering Upgrade — Phase 1 (Platform) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor IPMaster Desktop's file preview from a monolithic component into a platform — a unified preview shell with a pluggable toolbar and a Web Worker parsing pipeline — and migrate existing viewers onto it, landing version `0.2.0`.

**Architecture:** All parsing stays in the frontend (Electron renderer). Heavy, DOM-free parsing (xlsx now; pptx in Phase 2) is offloaded to a single shared Web Worker via a typed request/response client. `FilePreviewModal` becomes a host that renders a capability-driven toolbar (search/zoom/pages/toc/download/copy) plus the active viewer; viewers declare which toolbar capabilities they support through a React context. The Python backend is unchanged — it keeps serving raw bytes.

**Tech Stack:** React 19 + TypeScript + Vite 8; Vitest + @testing-library/react + jsdom (added here, no test infra exists yet); Web Workers (Vite native `new Worker(new URL(...), { type: 'module' })`); xlsx (existing), highlight.js (new).

**Spec:** `docs/superpowers/specs/2026-06-02-file-rendering-upgrade-design.md`

---

## File Structure

New directory `frontend-desktop/src/preview/` holds the platform:

- `src/preview/fileType.ts` — file-extension → preview-type detection (extracted + expanded from `FilePreviewModal.tsx`).
- `src/preview/worker/protocol.ts` — message types shared by client and worker.
- `src/preview/worker/parsers/xlsx.ts` — pure xlsx/csv → sheet-data parser (testable without a worker).
- `src/preview/worker/fileParser.worker.ts` — the worker entry; dispatches by `kind`.
- `src/preview/worker/parseClient.ts` — main-thread client (`parseInWorker`) with injectable worker factory for tests.
- `src/preview/toolbar/capabilities.ts` — `ViewerCapabilities` interfaces.
- `src/preview/toolbar/PreviewToolbarContext.tsx` — context + `usePreviewToolbar` (viewer registers caps) + `usePreviewToolbarState` (toolbar reads caps).
- `src/preview/toolbar/PreviewToolbar.tsx` — renders only the controls the active viewer declares.
- `src/preview/viewers/` — `ImageViewer.tsx`, `CodeViewer.tsx`, `MarkdownViewer.tsx`, `TextViewer.tsx`, `DocxViewer.tsx`, `ExcelViewer.tsx`, `common.tsx` (Loading/ErrorMsg/useFileText).

Modified:
- `src/components/FilePreviewModal.tsx` — slimmed to the host shell + viewer registry.
- `src/i18n.tsx` — new toolbar/preview i18n keys.
- `frontend-desktop/package.json` — version `0.2.0`, new deps, `test` script.
- `vite.config.ts` — none required for workers; Vitest config lives in `vitest.config.ts`.

Removed after migration:
- `react-syntax-highlighter` + `@types/react-syntax-highlighter` (replaced by highlight.js).

---

## Task 1: Set up Vitest

**Files:**
- Modify: `frontend-desktop/package.json`
- Create: `frontend-desktop/vitest.config.ts`
- Create: `frontend-desktop/src/test/setup.ts`
- Create: `frontend-desktop/src/preview/sanity.test.ts`

- [ ] **Step 1: Install test dependencies**

Run (in `frontend-desktop/`):
```bash
npm i -D vitest@^3 jsdom@^25 @testing-library/react@^16 @testing-library/jest-dom@^6 @testing-library/dom@^10
```
Expected: deps added to `package.json` devDependencies, no errors.

- [ ] **Step 2: Add the `test` script**

In `frontend-desktop/package.json`, add to `"scripts"`:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 3: Create the Vitest config**

Create `frontend-desktop/vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
```

- [ ] **Step 4: Create the test setup file**

Create `frontend-desktop/src/test/setup.ts`:
```ts
import '@testing-library/jest-dom/vitest'
```

- [ ] **Step 5: Write a sanity test**

Create `frontend-desktop/src/preview/sanity.test.ts`:
```ts
import { describe, it, expect } from 'vitest'

describe('vitest setup', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 6: Run the test suite**

Run: `npm test`
Expected: PASS — 1 passed.

- [ ] **Step 7: Commit**

```bash
git add frontend-desktop/package.json frontend-desktop/package-lock.json frontend-desktop/vitest.config.ts frontend-desktop/src/test/setup.ts frontend-desktop/src/preview/sanity.test.ts
git commit -m "test: set up vitest + testing-library for frontend-desktop"
```

---

## Task 2: File-type detection module

Extract the extension→type logic out of `FilePreviewModal.tsx` into a pure, tested module, and add the `pdf` and `pptx` types (their viewers arrive in Phase 2, but the type space is fixed now).

**Files:**
- Create: `frontend-desktop/src/preview/fileType.ts`
- Create: `frontend-desktop/src/preview/fileType.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend-desktop/src/preview/fileType.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { getExt, fileType, CODE_LANGS } from './fileType'

describe('getExt', () => {
  it('lowercases and strips path', () => {
    expect(getExt('C:/x/Report.PDF')).toBe('pdf')
    expect(getExt('a/b/c.tar.gz')).toBe('gz')
    expect(getExt('noext')).toBe('')
  })
})

describe('fileType', () => {
  it('classifies known types', () => {
    expect(fileType('png')).toBe('image')
    expect(fileType('md')).toBe('markdown')
    expect(fileType('docx')).toBe('docx')
    expect(fileType('xlsx')).toBe('excel')
    expect(fileType('csv')).toBe('excel')
    expect(fileType('py')).toBe('code')
    expect(fileType('txt')).toBe('text')
    expect(fileType('pdf')).toBe('pdf')
    expect(fileType('pptx')).toBe('pptx')
    expect(fileType('bin')).toBe('binary')
  })
  it('maps code extensions to highlight.js language ids', () => {
    expect(CODE_LANGS['py']).toBe('python')
    expect(CODE_LANGS['rs']).toBe('rust')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- fileType`
Expected: FAIL — cannot find module `./fileType`.

- [ ] **Step 3: Implement the module**

Create `frontend-desktop/src/preview/fileType.ts`:
```ts
export type PreviewType =
  | 'image' | 'markdown' | 'docx' | 'excel'
  | 'code' | 'text' | 'pdf' | 'pptx' | 'binary'

const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'svg'])
const MD_EXTS = new Set(['md', 'markdown'])
const DOCX_EXTS = new Set(['docx'])
const EXCEL_EXTS = new Set(['xlsx', 'xls', 'csv'])
const PDF_EXTS = new Set(['pdf'])
const PPTX_EXTS = new Set(['pptx'])
const TEXT_EXTS = new Set(['txt', 'log', 'rst', 'xml', 'html', 'htm', 'less', 'vue', 'php', 'rb', 'swift'])

// Maps file extension -> highlight.js language id.
export const CODE_LANGS: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript',
  jsx: 'javascript', tsx: 'typescript', sh: 'bash', bash: 'bash',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'ini',
  css: 'css', scss: 'scss', go: 'go', rs: 'rust',
  java: 'java', cpp: 'cpp', c: 'c', kt: 'kotlin',
}

export function getExt(p: string): string {
  const name = p.split(/[/\\]/).pop() ?? p
  return name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : ''
}

export function fileType(ext: string): PreviewType {
  if (IMAGE_EXTS.has(ext)) return 'image'
  if (MD_EXTS.has(ext)) return 'markdown'
  if (DOCX_EXTS.has(ext)) return 'docx'
  if (EXCEL_EXTS.has(ext)) return 'excel'
  if (PDF_EXTS.has(ext)) return 'pdf'
  if (PPTX_EXTS.has(ext)) return 'pptx'
  if (ext in CODE_LANGS) return 'code'
  if (TEXT_EXTS.has(ext)) return 'text'
  return 'binary'
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- fileType`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/fileType.ts frontend-desktop/src/preview/fileType.test.ts
git commit -m "feat(preview): extract + expand file-type detection (adds pdf/pptx)"
```

---

## Task 3: Worker protocol + pure xlsx parser

Define the message contract and the pure parser. The parser is in its own module so it is tested directly (no worker needed).

**Files:**
- Create: `frontend-desktop/src/preview/worker/protocol.ts`
- Create: `frontend-desktop/src/preview/worker/parsers/xlsx.ts`
- Create: `frontend-desktop/src/preview/worker/parsers/xlsx.test.ts`

- [ ] **Step 1: Create the protocol module**

Create `frontend-desktop/src/preview/worker/protocol.ts`:
```ts
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
```

- [ ] **Step 2: Write the failing test for the parser**

Create `frontend-desktop/src/preview/worker/parsers/xlsx.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import * as XLSX from 'xlsx'
import { parseXlsx } from './xlsx'

function makeWorkbookBytes(): ArrayBuffer {
  const wb = XLSX.utils.book_new()
  const ws = XLSX.utils.aoa_to_sheet([['a', 'b'], ['1', '2']])
  XLSX.utils.book_append_sheet(wb, ws, 'Sheet1')
  const out = XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as Uint8Array
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength)
}

describe('parseXlsx', () => {
  it('parses a binary workbook into sheet rows', () => {
    const sheets = parseXlsx(makeWorkbookBytes(), {})
    expect(sheets).toHaveLength(1)
    expect(sheets[0].name).toBe('Sheet1')
    expect(sheets[0].rows).toEqual([['a', 'b'], ['1', '2']])
  })

  it('parses csv text via the csv option', () => {
    const csv = 'x,y\n3,4'
    const buf = new TextEncoder().encode(csv).buffer
    const sheets = parseXlsx(buf, { csv: true })
    expect(sheets[0].rows).toEqual([['x', 'y'], ['3', '4']])
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- xlsx`
Expected: FAIL — cannot find module `./xlsx`.

- [ ] **Step 4: Implement the pure parser**

Create `frontend-desktop/src/preview/worker/parsers/xlsx.ts`:
```ts
import * as XLSX from 'xlsx'
import type { SheetData } from '../protocol'

export function parseXlsx(buffer: ArrayBuffer, options: Record<string, unknown>): SheetData[] {
  const isCsv = options?.csv === true
  const wb = isCsv
    ? XLSX.read(new TextDecoder().decode(buffer), { type: 'string' })
    : XLSX.read(new Uint8Array(buffer), { type: 'array' })
  return wb.SheetNames.map((name) => {
    const ws = wb.Sheets[name]
    const rows = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' })
    return { name, rows: rows as string[][] }
  })
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- xlsx`
Expected: PASS — 2 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/preview/worker/protocol.ts frontend-desktop/src/preview/worker/parsers/
git commit -m "feat(preview): worker protocol + pure xlsx parser"
```

---

## Task 4: Worker entry + parse client

The worker dispatches by kind. The client hides postMessage plumbing, supports progress + cancellation, and accepts an injectable worker factory so it is testable without a real Worker.

**Files:**
- Create: `frontend-desktop/src/preview/worker/fileParser.worker.ts`
- Create: `frontend-desktop/src/preview/worker/parseClient.ts`
- Create: `frontend-desktop/src/preview/worker/parseClient.test.ts`

- [ ] **Step 1: Create the worker entry**

Create `frontend-desktop/src/preview/worker/fileParser.worker.ts`:
```ts
/// <reference lib="webworker" />
import type { ParseRequestMsg, WorkerOutMsg } from './protocol'
import { parseXlsx } from './parsers/xlsx'

const ctx = self as unknown as DedicatedWorkerGlobalScope

ctx.onmessage = (ev: MessageEvent<ParseRequestMsg>) => {
  const msg = ev.data
  if (msg?.type !== 'parse') return
  try {
    post({ type: 'progress', id: msg.id, progress: { phase: 'parsing' } })
    const data = dispatch(msg)
    post({ type: 'result', id: msg.id, data })
  } catch (e) {
    post({ type: 'error', id: msg.id, error: e instanceof Error ? e.message : String(e) })
  }
}

function post(m: WorkerOutMsg) { ctx.postMessage(m) }

function dispatch(msg: ParseRequestMsg): unknown {
  switch (msg.kind) {
    case 'xlsx': return parseXlsx(msg.buffer, msg.options ?? {})
    default: throw new Error(`Unsupported parse kind: ${msg.kind}`)
  }
}
```

- [ ] **Step 2: Write the failing test for the client**

Create `frontend-desktop/src/preview/worker/parseClient.test.ts`:
```ts
import { describe, it, expect, afterEach, vi } from 'vitest'
import { parseInWorker, __setWorkerFactory, type WorkerLike } from './parseClient'
import type { WorkerOutMsg, ParseRequestMsg } from './protocol'

interface FakeWorker extends WorkerLike { sent: ParseRequestMsg[]; emit(m: WorkerOutMsg): void }

function makeFake(): FakeWorker {
  return {
    onmessage: null,
    sent: [],
    postMessage(m: unknown) { this.sent.push(m as ParseRequestMsg) },
    terminate() {},
    emit(m: WorkerOutMsg) { this.onmessage?.({ data: m } as MessageEvent<WorkerOutMsg>) },
  }
}

afterEach(() => __setWorkerFactory(null))

describe('parseInWorker', () => {
  it('resolves with the result data', async () => {
    let fake!: FakeWorker
    __setWorkerFactory(() => (fake = makeFake()))
    const p = parseInWorker('xlsx', new ArrayBuffer(8))
    const id = fake.sent[0].id
    fake.emit({ type: 'result', id, data: [{ name: 'S', rows: [] }] })
    await expect(p).resolves.toEqual([{ name: 'S', rows: [] }])
  })

  it('reports progress then resolves', async () => {
    let fake!: FakeWorker
    __setWorkerFactory(() => (fake = makeFake()))
    const onProgress = vi.fn()
    const p = parseInWorker('xlsx', new ArrayBuffer(8), { onProgress })
    const id = fake.sent[0].id
    fake.emit({ type: 'progress', id, progress: { phase: 'parsing' } })
    fake.emit({ type: 'result', id, data: [] })
    await p
    expect(onProgress).toHaveBeenCalledWith({ phase: 'parsing' })
  })

  it('rejects on error message', async () => {
    let fake!: FakeWorker
    __setWorkerFactory(() => (fake = makeFake()))
    const p = parseInWorker('xlsx', new ArrayBuffer(8))
    const id = fake.sent[0].id
    fake.emit({ type: 'error', id, error: 'boom' })
    await expect(p).rejects.toThrow('boom')
  })

  it('rejects immediately when the signal is already aborted', async () => {
    __setWorkerFactory(() => makeFake())
    const ac = new AbortController()
    ac.abort()
    await expect(parseInWorker('xlsx', new ArrayBuffer(8), { signal: ac.signal }))
      .rejects.toThrow(/abort/i)
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- parseClient`
Expected: FAIL — cannot find module `./parseClient`.

- [ ] **Step 4: Implement the client**

Create `frontend-desktop/src/preview/worker/parseClient.ts`:
```ts
import type { ParseKind, ParseProgress, ParseRequestMsg, WorkerOutMsg } from './protocol'

export interface WorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void
  onmessage: ((ev: MessageEvent<WorkerOutMsg>) => void) | null
  terminate(): void
}

interface Pending {
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  onProgress?: (p: ParseProgress) => void
}

function defaultFactory(): WorkerLike {
  return new Worker(new URL('./fileParser.worker.ts', import.meta.url), { type: 'module' }) as unknown as WorkerLike
}

let _factory: () => WorkerLike = defaultFactory
let _worker: WorkerLike | null = null
let _seq = 0
const _pending = new Map<string, Pending>()

// Test seam: swap the worker factory (pass null to restore the default and drop the worker).
export function __setWorkerFactory(f: (() => WorkerLike) | null): void {
  _factory = f ?? defaultFactory
  if (_worker) { _worker.terminate(); _worker = null }
  _pending.clear()
}

function ensureWorker(): WorkerLike {
  if (_worker) return _worker
  const w = _factory()
  w.onmessage = (ev) => {
    const msg = ev.data
    const p = _pending.get(msg.id)
    if (!p) return
    if (msg.type === 'progress') { p.onProgress?.(msg.progress); return }
    _pending.delete(msg.id)
    if (msg.type === 'result') p.resolve(msg.data)
    else if (msg.type === 'error') p.reject(new Error(msg.error))
  }
  _worker = w
  return w
}

export function parseInWorker(
  kind: ParseKind,
  buffer: ArrayBuffer,
  opts?: { onProgress?: (p: ParseProgress) => void; signal?: AbortSignal },
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (opts?.signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const id = `p${++_seq}`
    const w = ensureWorker()
    _pending.set(id, { resolve, reject, onProgress: opts?.onProgress })
    opts?.signal?.addEventListener('abort', () => {
      if (_pending.delete(id)) reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
    const req: ParseRequestMsg = { type: 'parse', id, kind, buffer, options: {} }
    w.postMessage(req, [buffer])
  })
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- parseClient`
Expected: PASS — 4 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/preview/worker/fileParser.worker.ts frontend-desktop/src/preview/worker/parseClient.ts frontend-desktop/src/preview/worker/parseClient.test.ts
git commit -m "feat(preview): web worker entry + parse client with progress/cancel"
```

---

## Task 5: Toolbar capability types + context

Viewers register what toolbar controls they support; the shell reads them. The hook clears caps on unmount so a switched viewer never inherits stale controls.

**Files:**
- Create: `frontend-desktop/src/preview/toolbar/capabilities.ts`
- Create: `frontend-desktop/src/preview/toolbar/PreviewToolbarContext.tsx`
- Create: `frontend-desktop/src/preview/toolbar/PreviewToolbarContext.test.tsx`

- [ ] **Step 1: Create the capability types**

Create `frontend-desktop/src/preview/toolbar/capabilities.ts`:
```ts
export interface SearchCapability {
  run(query: string): void
  next(): void
  prev(): void
  clear(): void
  count?: number
  current?: number
}
export interface ZoomCapability {
  in(): void
  out(): void
  reset(): void
  fit(): void
  scale: number
}
export interface PagesCapability { count: number; current: number; goto(n: number): void }
export interface TocItem { id: string; label: string; level?: number }
export interface TocCapability { items: TocItem[]; goto(id: string): void }
export interface DownloadCapability { url: string; filename: string }

export interface ViewerCapabilities {
  search?: SearchCapability
  zoom?: ZoomCapability
  pages?: PagesCapability
  toc?: TocCapability
  download?: DownloadCapability
  copy?: () => string
}
```

- [ ] **Step 2: Write the failing test for the context**

Create `frontend-desktop/src/preview/toolbar/PreviewToolbarContext.test.tsx`:
```tsx
import { describe, it, expect } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useState } from 'react'
import {
  PreviewToolbarProvider, usePreviewToolbar, usePreviewToolbarState,
} from './PreviewToolbarContext'

function Reader() {
  const caps = usePreviewToolbarState()
  return <div data-testid="keys">{Object.keys(caps).sort().join(',')}</div>
}

function Viewer({ scale }: { scale: number }) {
  usePreviewToolbar({ zoom: { in() {}, out() {}, reset() {}, fit() {}, scale } }, [scale])
  return <div>viewer</div>
}

function Harness() {
  const [mounted, setMounted] = useState(true)
  return (
    <PreviewToolbarProvider>
      <Reader />
      {mounted && <Viewer scale={1} />}
      <button onClick={() => setMounted(false)}>unmount</button>
    </PreviewToolbarProvider>
  )
}

describe('PreviewToolbarContext', () => {
  it('exposes registered caps and clears them on viewer unmount', () => {
    render(<Harness />)
    expect(screen.getByTestId('keys').textContent).toBe('zoom')
    act(() => { screen.getByText('unmount').click() })
    expect(screen.getByTestId('keys').textContent).toBe('')
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- PreviewToolbarContext`
Expected: FAIL — cannot find module `./PreviewToolbarContext`.

- [ ] **Step 4: Implement the context**

Create `frontend-desktop/src/preview/toolbar/PreviewToolbarContext.tsx`:
```tsx
import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import type { ViewerCapabilities } from './capabilities'

interface Ctx {
  caps: ViewerCapabilities
  setCapabilities: (c: ViewerCapabilities) => void
}
const PreviewToolbarContext = createContext<Ctx | null>(null)

export function PreviewToolbarProvider({ children }: { children: ReactNode }) {
  const [caps, setCaps] = useState<ViewerCapabilities>({})
  const setCapabilities = useCallback((c: ViewerCapabilities) => setCaps(c), [])
  return (
    <PreviewToolbarContext.Provider value={{ caps, setCapabilities }}>
      {children}
    </PreviewToolbarContext.Provider>
  )
}

// Read current capabilities (used by the toolbar).
export function usePreviewToolbarState(): ViewerCapabilities {
  const ctx = useContext(PreviewToolbarContext)
  if (!ctx) throw new Error('usePreviewToolbarState must be used within PreviewToolbarProvider')
  return ctx.caps
}

// Register capabilities (used by a viewer). Re-registers when `deps` change,
// and clears on unmount so a switched viewer never inherits stale controls.
export function usePreviewToolbar(caps: ViewerCapabilities, deps: unknown[]): void {
  const ctx = useContext(PreviewToolbarContext)
  if (!ctx) throw new Error('usePreviewToolbar must be used within PreviewToolbarProvider')
  const { setCapabilities } = ctx
  useEffect(() => {
    setCapabilities(caps)
    return () => setCapabilities({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- PreviewToolbarContext`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/preview/toolbar/capabilities.ts frontend-desktop/src/preview/toolbar/PreviewToolbarContext.tsx frontend-desktop/src/preview/toolbar/PreviewToolbarContext.test.tsx
git commit -m "feat(preview): toolbar capability context"
```

---

## Task 6: i18n keys for the toolbar

**Files:**
- Modify: `frontend-desktop/src/i18n.tsx`

- [ ] **Step 1: Add zh keys**

In `frontend-desktop/src/i18n.tsx`, inside the `zh` dict after the existing `'filePreview.empty'` line, add:
```ts
  'preview.download': '下载',
  'preview.copy': '复制',
  'preview.copied': '已复制',
  'preview.zoomIn': '放大',
  'preview.zoomOut': '缩小',
  'preview.zoomReset': '实际大小',
  'preview.zoomFit': '适应窗口',
  'preview.search': '搜索',
  'preview.searchPlaceholder': '在文件中搜索…',
  'preview.prevMatch': '上一个',
  'preview.nextMatch': '下一个',
  'preview.page': '页',
  'preview.toc': '目录',
  'preview.parsing': '解析中…',
```

- [ ] **Step 2: Add en keys**

In the `en` dict after the existing `'filePreview.empty'` line, add:
```ts
  'preview.download': 'Download',
  'preview.copy': 'Copy',
  'preview.copied': 'Copied',
  'preview.zoomIn': 'Zoom in',
  'preview.zoomOut': 'Zoom out',
  'preview.zoomReset': 'Actual size',
  'preview.zoomFit': 'Fit to window',
  'preview.search': 'Search',
  'preview.searchPlaceholder': 'Search in file…',
  'preview.prevMatch': 'Previous',
  'preview.nextMatch': 'Next',
  'preview.page': 'Page',
  'preview.toc': 'Contents',
  'preview.parsing': 'Parsing…',
```

- [ ] **Step 3: Type-check**

Run (in `frontend-desktop/`): `npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/src/i18n.tsx
git commit -m "i18n: preview toolbar strings"
```

---

## Task 7: Toolbar component

Renders only the controls present in the active viewer's capabilities. `download` (when present) and `copy` (when present) are always-eligible; zoom/pages/search/toc render only when declared.

**Files:**
- Create: `frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx`

- [ ] **Step 1: Implement the toolbar**

Create `frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx`:
```tsx
import { useState } from 'react'
import {
  ZoomInIcon, ZoomOutIcon, MaximizeIcon, DownloadIcon, CopyIcon,
  CheckIcon, SearchIcon, ChevronUpIcon, ChevronDownIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { usePreviewToolbarState } from './PreviewToolbarContext'

export function PreviewToolbar() {
  const { t } = useI18n()
  const caps = usePreviewToolbarState()
  const [copied, setCopied] = useState(false)
  const [query, setQuery] = useState('')

  const hasAny = caps.zoom || caps.pages || caps.search || caps.download || caps.copy
  if (!hasAny) return null

  function doCopy() {
    if (!caps.copy) return
    void navigator.clipboard.writeText(caps.copy()).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    })
  }

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 flex-shrink-0"
      style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
      {caps.zoom && (
        <>
          <Button variant="ghost" size="icon" title={t('preview.zoomOut')} onClick={() => caps.zoom!.out()}>
            <ZoomOutIcon size={15} />
          </Button>
          <span className="text-xs tabular-nums w-10 text-center" style={{ color: 'var(--t2)' }}>
            {Math.round(caps.zoom.scale * 100)}%
          </span>
          <Button variant="ghost" size="icon" title={t('preview.zoomIn')} onClick={() => caps.zoom!.in()}>
            <ZoomInIcon size={15} />
          </Button>
          <Button variant="ghost" size="icon" title={t('preview.zoomFit')} onClick={() => caps.zoom!.fit()}>
            <MaximizeIcon size={15} />
          </Button>
        </>
      )}

      {caps.pages && (
        <span className="text-xs px-2" style={{ color: 'var(--t2)' }}>
          {t('preview.page')} {caps.pages.current} / {caps.pages.count}
        </span>
      )}

      {caps.search && (
        <div className="flex items-center gap-1 ml-1">
          <SearchIcon size={14} style={{ color: 'var(--t3)' }} />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); caps.search!.run(e.target.value) }}
            placeholder={t('preview.searchPlaceholder')}
            className="text-xs px-2 py-1 rounded outline-none"
            style={{ background: 'var(--bg1)', border: '1px solid var(--border)', color: 'var(--t1)', width: 160 }}
          />
          {typeof caps.search.count === 'number' && (
            <span className="text-xs tabular-nums" style={{ color: 'var(--t3)' }}>
              {caps.search.count}
            </span>
          )}
          <Button variant="ghost" size="icon" title={t('preview.prevMatch')} onClick={() => caps.search!.prev()}>
            <ChevronUpIcon size={15} />
          </Button>
          <Button variant="ghost" size="icon" title={t('preview.nextMatch')} onClick={() => caps.search!.next()}>
            <ChevronDownIcon size={15} />
          </Button>
        </div>
      )}

      <div className="flex-1" />

      {caps.copy && (
        <Button variant="ghost" size="icon" title={copied ? t('preview.copied') : t('preview.copy')} onClick={doCopy}>
          {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
        </Button>
      )}
      {caps.download && (
        <a href={caps.download.url} download={caps.download.filename} title={t('preview.download')}>
          <Button variant="ghost" size="icon">
            <DownloadIcon size={15} />
          </Button>
        </a>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run (in `frontend-desktop/`): `npx tsc -b`
Expected: no errors. (If any `lucide-react` icon name is missing in the installed version, replace it with the closest available icon — verify against `node_modules/lucide-react`.)

- [ ] **Step 3: Commit**

```bash
git add frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx
git commit -m "feat(preview): capability-driven toolbar component"
```

---

## Task 8: Shared viewer helpers

Pull the small shared pieces (loading, error, text fetch, raw URL) out of the old modal into one module the migrated viewers import.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/common.tsx`

- [ ] **Step 1: Implement the helpers**

Create `frontend-desktop/src/preview/viewers/common.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { Spinner } from '@/components/ui/spinner'
import { useI18n } from '@/i18n'

export function rawUrl(path: string): string {
  return `/api/v1/workspace/file/raw?path=${encodeURIComponent(path)}`
}

export function textUrl(path: string): string {
  return `/api/v1/workspace/file?path=${encodeURIComponent(path)}`
}

// fetch that throws on !ok with FastAPI's `detail` extracted into the message,
// so callers (mammoth/xlsx) don't get HTML/JSON error bodies dressed up as
// their expected binary format.
export async function fetchOrThrow(url: string): Promise<Response> {
  const r = await fetch(url)
  if (!r.ok) {
    let detail = ''
    try {
      const body = await r.text()
      try { detail = (JSON.parse(body) as { detail?: string })?.detail || body }
      catch { detail = body }
    } catch { /* ignore body read failures */ }
    throw new Error(`HTTP ${r.status}${detail ? ': ' + detail.slice(0, 200) : ''}`)
  }
  return r
}

export function useFileText(path: string) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setContent(null); setError(null)
    fetchOrThrow(textUrl(path))
      .then((r) => r.json())
      .then((d) => setContent(d.content))
      .catch((e) => setError(String(e)))
  }, [path])
  return { content, error }
}

export function Loading() {
  const { t } = useI18n()
  return (
    <div className="flex h-full items-center justify-center gap-2" style={{ color: 'var(--t3)' }}>
      <Spinner className="h-4 w-4" /> <span className="text-sm">{t('common.loading')}</span>
    </div>
  )
}

export function ErrorMsg({ msg }: { msg: string }) {
  return <div className="flex h-full items-center justify-center p-6 text-red-500 text-sm">{msg}</div>
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc -b`
Expected: no errors (module is not yet imported anywhere — that is fine).

- [ ] **Step 3: Commit**

```bash
git add frontend-desktop/src/preview/viewers/common.tsx
git commit -m "feat(preview): shared viewer helpers"
```

---

## Task 9: Image viewer with zoom/pan

**Files:**
- Create: `frontend-desktop/src/preview/viewers/ImageViewer.tsx`

- [ ] **Step 1: Implement the viewer**

Create `frontend-desktop/src/preview/viewers/ImageViewer.tsx`:
```tsx
import { useRef, useState } from 'react'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { rawUrl } from './common'

const STEP = 1.25
const MIN = 0.1
const MAX = 8

export function ImageViewer({ path, filename }: { path: string; filename: string }) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)

  const clamp = (s: number) => Math.min(MAX, Math.max(MIN, s))
  const reset = () => { setScale(1); setOffset({ x: 0, y: 0 }) }

  usePreviewToolbar({
    zoom: {
      in: () => setScale((s) => clamp(s * STEP)),
      out: () => setScale((s) => clamp(s / STEP)),
      reset,
      fit: reset,
      scale,
    },
    download: { url: rawUrl(path), filename },
  }, [scale, path, filename])

  function onWheel(e: React.WheelEvent) {
    e.preventDefault()
    setScale((s) => clamp(s * (e.deltaY < 0 ? STEP : 1 / STEP)))
  }
  function onDown(e: React.MouseEvent) {
    drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y }
  }
  function onMove(e: React.MouseEvent) {
    if (!drag.current) return
    setOffset({ x: drag.current.ox + (e.clientX - drag.current.x), y: drag.current.oy + (e.clientY - drag.current.y) })
  }
  function onUp() { drag.current = null }

  return (
    <div
      className="flex h-full items-center justify-center overflow-hidden p-4"
      style={{ background: 'var(--bg2)', cursor: scale > 1 ? 'grab' : 'default' }}
      onWheel={onWheel}
      onMouseDown={onDown}
      onMouseMove={onMove}
      onMouseUp={onUp}
      onMouseLeave={onUp}
    >
      <img
        src={rawUrl(path)}
        alt={path}
        draggable={false}
        className="max-h-full max-w-full object-contain rounded shadow select-none"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`, transformOrigin: 'center' }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend-desktop/src/preview/viewers/ImageViewer.tsx
git commit -m "feat(preview): image viewer with zoom/pan + download"
```

---

## Task 10: Swap code highlighting to highlight.js

Replace `react-syntax-highlighter` with `highlight.js` in the code viewer. Declares `copy` + `download` capabilities.

**Files:**
- Modify: `frontend-desktop/package.json` (add `highlight.js`)
- Create: `frontend-desktop/src/preview/viewers/CodeViewer.tsx`
- Create: `frontend-desktop/src/preview/viewers/TextViewer.tsx`

- [ ] **Step 1: Install highlight.js**

Run (in `frontend-desktop/`): `npm i highlight.js@^11`
Expected: added to dependencies.

- [ ] **Step 2: Implement the code viewer**

Create `frontend-desktop/src/preview/viewers/CodeViewer.tsx`:
```tsx
import { useMemo } from 'react'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github.css'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { useFileText, rawUrl, Loading, ErrorMsg } from './common'

export function CodeViewer({ path, lang, filename }: { path: string; lang: string; filename: string }) {
  const { content, error } = useFileText(path)

  const html = useMemo(() => {
    if (content === null) return ''
    try {
      return lang && hljs.getLanguage(lang)
        ? hljs.highlight(content, { language: lang }).value
        : hljs.highlightAuto(content).value
    } catch {
      return null
    }
  }, [content, lang])

  usePreviewToolbar({
    copy: () => content ?? '',
    download: { url: rawUrl(path), filename },
  }, [content, path, filename])

  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />

  return (
    <pre className="hljs text-xs leading-relaxed m-0 p-4 overflow-auto h-full" style={{ background: 'transparent' }}>
      {html === null
        ? <code>{content}</code>
        : <code dangerouslySetInnerHTML={{ __html: html }} />}
    </pre>
  )
}
```

- [ ] **Step 3: Implement the text viewer**

Create `frontend-desktop/src/preview/viewers/TextViewer.tsx`:
```tsx
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { useFileText, rawUrl, Loading, ErrorMsg } from './common'

export function TextViewer({ path, filename }: { path: string; filename: string }) {
  const { content, error } = useFileText(path)
  usePreviewToolbar({
    copy: () => content ?? '',
    download: { url: rawUrl(path), filename },
  }, [content, path, filename])
  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />
  return (
    <pre className="px-6 py-4 text-xs leading-relaxed whitespace-pre font-mono" style={{ color: 'var(--t1)' }}>
      {content}
    </pre>
  )
}
```

- [ ] **Step 4: Type-check**

Run: `npx tsc -b`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/package.json frontend-desktop/package-lock.json frontend-desktop/src/preview/viewers/CodeViewer.tsx frontend-desktop/src/preview/viewers/TextViewer.tsx
git commit -m "feat(preview): code/text viewers on highlight.js + copy/download"
```

---

## Task 11: Markdown + DOCX viewers

Move these onto the shared helpers and the toolbar (download capability). Markdown rendering itself is unchanged in Phase 1 (code-block highlighting + KaTeX are Phase 3 / deferred). DOCX stays on mammoth in Phase 1; docx-preview high-fidelity is Phase 2.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/MarkdownViewer.tsx`
- Create: `frontend-desktop/src/preview/viewers/DocxViewer.tsx`

- [ ] **Step 1: Implement the markdown viewer**

Create `frontend-desktop/src/preview/viewers/MarkdownViewer.tsx`:
```tsx
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { useFileText, rawUrl, Loading, ErrorMsg } from './common'

function resolveImgSrc(src: string, mdPath: string): string {
  if (!src || /^(https?:|data:|\/\/)/i.test(src)) return src
  if (src.startsWith('/')) return rawUrl(src)
  const dir = mdPath.replace(/\\/g, '/').split('/').slice(0, -1).join('/')
  return rawUrl(dir ? `${dir}/${src}` : src)
}

export function MarkdownViewer({ path, filename }: { path: string; filename: string }) {
  const { content, error } = useFileText(path)
  usePreviewToolbar({
    copy: () => content ?? '',
    download: { url: rawUrl(path), filename },
  }, [content, path, filename])

  if (error) return <ErrorMsg msg={error} />
  if (content === null) return <Loading />

  return (
    <div className="prose prose-sm max-w-none px-8 py-6">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img({ src, alt, ...rest }) {
            return <img src={resolveImgSrc(src ?? '', path)} alt={alt ?? ''} {...rest} className="max-w-full rounded" />
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
```

- [ ] **Step 2: Implement the docx viewer**

Create `frontend-desktop/src/preview/viewers/DocxViewer.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'

export function DocxViewer({ path, filename }: { path: string; filename: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  usePreviewToolbar({ download: { url: rawUrl(path), filename } }, [path, filename])

  useEffect(() => {
    setHtml(null); setError(null)
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => import('mammoth').then((m) => m.convertToHtml({ arrayBuffer: buf })))
      .then((result) => setHtml(result.value))
      .catch((e) => setError(String(e)))
  }, [path])

  if (error) return <ErrorMsg msg={error} />
  if (html === null) return <Loading />
  return (
    <div
      className="prose prose-sm max-w-none px-10 py-6 [&_p:empty]:hidden"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
```

- [ ] **Step 3: Type-check**

Run: `npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/src/preview/viewers/MarkdownViewer.tsx frontend-desktop/src/preview/viewers/DocxViewer.tsx
git commit -m "feat(preview): markdown/docx viewers on shared helpers + toolbar"
```

---

## Task 12: Excel viewer on the worker pipeline

Moves xlsx/csv parsing off the main thread via `parseInWorker`. Declares `download`.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/ExcelViewer.tsx`

- [ ] **Step 1: Implement the viewer**

Create `frontend-desktop/src/preview/viewers/ExcelViewer.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { useI18n } from '@/i18n'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import { parseInWorker } from '../worker/parseClient'
import type { SheetData } from '../worker/protocol'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'

export function ExcelViewer({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n()
  const [tables, setTables] = useState<SheetData[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeSheet, setActiveSheet] = useState(0)

  usePreviewToolbar({ download: { url: rawUrl(path), filename } }, [path, filename])

  useEffect(() => {
    const ac = new AbortController()
    setTables(null); setError(null); setActiveSheet(0)
    const isCsv = path.toLowerCase().endsWith('.csv')
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => parseInWorker('xlsx', buf, { signal: ac.signal }) as Promise<SheetData[]>)
      .then((sheets) => { if (!ac.signal.aborted) setTables(sheets) })
      .catch((e) => { if (e?.name !== 'AbortError') setError(String(e)) })
    // isCsv is read inside the worker via options in a later iteration; CSV is
    // valid input to XLSX.read in array mode, so no special-casing needed here.
    void isCsv
    return () => ac.abort()
  }, [path])

  if (error) return <ErrorMsg msg={error} />
  if (tables === null) return <Loading />
  if (tables.length === 0) return <div className="p-6 text-sm" style={{ color: 'var(--t3)' }}>{t('filePreview.empty')}</div>

  const sheet = tables[activeSheet]
  return (
    <div className="flex flex-col h-full">
      {tables.length > 1 && (
        <div className="flex gap-1 px-4 pt-2 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          {tables.map((tbl, i) => (
            <button
              key={tbl.name}
              onClick={() => setActiveSheet(i)}
              className="px-3 py-1.5 text-xs rounded-t transition-colors"
              style={{
                color: i === activeSheet ? 'var(--blue)' : 'var(--t2)',
                background: i === activeSheet ? 'var(--blue-dim)' : 'transparent',
                border: 'none',
                borderBottom: `2px solid ${i === activeSheet ? 'var(--blue)' : 'transparent'}`,
                cursor: 'pointer',
              }}
            >
              {tbl.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs border-collapse">
          <tbody>
            {sheet.rows.map((row, ri) => (
              <tr key={ri} style={ri === 0 ? { background: 'var(--bg2)', fontWeight: 600 } : undefined}>
                {row.map((cell, ci) => (
                  <td key={ci} className="px-2 py-1 whitespace-nowrap max-w-[200px] truncate"
                    style={{ border: '1px solid var(--border)', color: 'var(--t1)' }}>
                    {String(cell ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

> Note: the worker's `parseXlsx` accepts a `csv` option for true CSV-as-text parsing. In Phase 1 we feed raw bytes for all spreadsheet types because `XLSX.read(Uint8Array, { type: 'array' })` already handles `.csv` correctly; the `csv` option path stays available for Phase 3's Excel enhancements. Do not delete it.

- [ ] **Step 2: Type-check**

Run: `npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend-desktop/src/preview/viewers/ExcelViewer.tsx
git commit -m "feat(preview): excel viewer parses via web worker (off main thread)"
```

---

## Task 13: Rebuild FilePreviewModal as the host shell

Replace the monolithic modal with a thin host: header → toolbar → viewer registry → content. Wrap everything in `PreviewToolbarProvider`. PDF and PPTX show a "coming soon"-style binary fallback until Phase 2 (their types exist but have no viewer yet).

**Files:**
- Modify (full rewrite): `frontend-desktop/src/components/FilePreviewModal.tsx`

- [ ] **Step 1: Rewrite the modal**

Replace the entire contents of `frontend-desktop/src/components/FilePreviewModal.tsx` with:
```tsx
import { useEffect } from 'react'
import { XIcon, FileIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { getExt, fileType, CODE_LANGS } from '@/preview/fileType'
import { PreviewToolbarProvider } from '@/preview/toolbar/PreviewToolbarContext'
import { PreviewToolbar } from '@/preview/toolbar/PreviewToolbar'
import { ImageViewer } from '@/preview/viewers/ImageViewer'
import { MarkdownViewer } from '@/preview/viewers/MarkdownViewer'
import { CodeViewer } from '@/preview/viewers/CodeViewer'
import { TextViewer } from '@/preview/viewers/TextViewer'
import { DocxViewer } from '@/preview/viewers/DocxViewer'
import { ExcelViewer } from '@/preview/viewers/ExcelViewer'

interface Props {
  path: string
  onClose: () => void
}

export function FilePreviewModal({ path, onClose }: Props) {
  const { t } = useI18n()
  const ext = getExt(path)
  const type = fileType(ext)
  const name = path.split(/[/\\]/).pop() ?? path

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm"
      style={{ background: 'rgba(15,31,61,.35)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="relative flex flex-col rounded-xl"
        style={{ width: '82vw', height: '85vh', maxWidth: 1200, background: 'var(--bg1)', boxShadow: '0 24px 80px rgba(15,31,61,.22)' }}>
        <PreviewToolbarProvider>
          {/* Header */}
          <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
            <FileIcon size={15} className="flex-shrink-0" style={{ color: 'var(--t3)' }} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium" style={{ color: 'var(--t2)' }}>{name}</span>
            <Button variant="ghost" size="icon" onClick={onClose}><XIcon size={16} /></Button>
          </div>

          {/* Toolbar (renders nothing if the active viewer declares no capabilities) */}
          <PreviewToolbar />

          {/* Content */}
          <div className="flex-1 overflow-auto">
            {type === 'image' && <ImageViewer path={path} filename={name} />}
            {type === 'markdown' && <MarkdownViewer path={path} filename={name} />}
            {type === 'docx' && <DocxViewer path={path} filename={name} />}
            {type === 'excel' && <ExcelViewer path={path} filename={name} />}
            {type === 'code' && <CodeViewer path={path} lang={CODE_LANGS[ext]} filename={name} />}
            {type === 'text' && <TextViewer path={path} filename={name} />}
            {(type === 'pdf' || type === 'pptx' || type === 'binary') && (
              <div className="flex h-full items-center justify-center text-sm" style={{ color: 'var(--t3)' }}>
                {t('filePreview.unsupported', { ext: ext || t('filePreview.unknownExt') })}
              </div>
            )}
          </div>
        </PreviewToolbarProvider>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check and build**

Run (in `frontend-desktop/`): `npx tsc -b && npm run build`
Expected: type-check passes; Vite build succeeds and emits a separate worker chunk for `fileParser.worker`.

- [ ] **Step 3: Run the full test suite**

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/src/components/FilePreviewModal.tsx
git commit -m "feat(preview): FilePreviewModal becomes host shell + toolbar provider"
```

---

## Task 14: Remove react-syntax-highlighter

Nothing imports it after Task 10/13. Remove the dependency.

**Files:**
- Modify: `frontend-desktop/package.json`

- [ ] **Step 1: Verify no remaining imports**

Run (in `frontend-desktop/`): `grep -rn "react-syntax-highlighter" src/`
Expected: no matches.

- [ ] **Step 2: Uninstall**

Run: `npm uninstall react-syntax-highlighter @types/react-syntax-highlighter`
Expected: both removed from `package.json`.

- [ ] **Step 3: Re-type-check and build**

Run: `npx tsc -b && npm run build`
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/package.json frontend-desktop/package-lock.json
git commit -m "chore(preview): drop react-syntax-highlighter (replaced by highlight.js)"
```

---

## Task 15: Bump version to 0.2.0 + manual verification

**Files:**
- Modify: `frontend-desktop/package.json` (only if it carries the user-facing version; otherwise the Electron app's `electron/package.json`)

- [ ] **Step 1: Confirm which package.json holds the release version**

Run: `grep -n '"version"' frontend-desktop/package.json electron/package.json`
The user-facing desktop version lives in `electron/package.json` (currently 0.1.11). Set it to `0.2.0`. Leave `frontend-desktop/package.json` (`0.0.0`, private) as-is.

- [ ] **Step 2: Set the version**

Edit `electron/package.json`: change `"version": "0.1.11"` to `"version": "0.2.0"`.

- [ ] **Step 3: Manual verification in the running app**

Start dev (project root): launch the desktop app per the repo's normal dev flow (backend + `npm run dev` in `frontend-desktop/`, Electron pointing at the dev server). Then in the Workspace panel open one file of each type and confirm:
  - PNG/JPG: opens; toolbar shows zoom; wheel zooms; drag pans; download works.
  - `.py`/`.ts`: highlighted via highlight.js; copy button copies; download works.
  - `.md`: renders; relative images resolve; download works.
  - `.docx`: renders via mammoth; download works.
  - `.xlsx` (multi-sheet) and `.csv`: render as tables; sheet tabs switch; **UI stays responsive while a large workbook parses** (parsing is in the worker).
  - `.txt`: renders; copy works.
  - `.pdf` / `.pptx`: show the "not supported" fallback (Phase 2).
  - A `.bin`/unknown file: shows the fallback.
  - Esc and backdrop click close the modal.

Record the result. If any check fails, fix before committing.

- [ ] **Step 4: Commit**

```bash
git add electron/package.json
git commit -m "chore: bump desktop version to 0.2.0 (file rendering platform)"
```

---

## Self-Review (completed during planning)

**Spec coverage (Phase 1 scope):**
- Worker parsing pipeline + progress + cancel → Tasks 3, 4, 12. ✓
- Unified preview shell + capability toolbar → Tasks 5, 7, 13. ✓
- Migrate existing viewers (image/text/code/markdown/docx/excel) → Tasks 9–13. ✓
- highlight.js code-highlight swap → Tasks 10, 14. ✓
- Image zoom/pan → Task 9. ✓
- 0.2.0 version → Task 15. ✓
- Type space fixed to include pdf/pptx (viewers deferred to Phase 2) → Task 2. ✓
- Backend unchanged → no backend tasks. ✓

**Type consistency:** `ViewerCapabilities` keys (`search/zoom/pages/toc/download/copy`) are identical across `capabilities.ts`, `PreviewToolbar.tsx`, and every viewer's `usePreviewToolbar` call. `SheetData` is defined once in `protocol.ts` and reused by `parseXlsx`, `parseClient` consumers, and `ExcelViewer`. `parseInWorker(kind, buffer, opts)` signature matches its callers. `usePreviewToolbar(caps, deps)` matches all viewer call sites.

**Placeholder scan:** No TBD/TODO/"handle edge cases" steps; every code step contains complete code; every command has an expected result.

**Out of scope (correctly deferred):** PDF/PPTX viewers, docx-preview high-fidelity (Phase 2); markdown code highlighting, KaTeX, Mermaid, Excel sort/filter/edit, virtual scrolling (Phase 3 / deferred per spec §9).
