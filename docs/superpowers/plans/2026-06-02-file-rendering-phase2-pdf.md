# PDF Viewer Implementation Plan (Phase 2, slice 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real PDF viewer to IPMaster Desktop's file preview — continuous scroll, zoom, page jump, in-document search, text selection, outline/TOC navigation, CJK support — built on pdfjs's prebuilt `pdf_viewer` and wired into the Phase 1 capability toolbar. Ships as `0.2.1`.

**Architecture:** A new `PdfViewer` React component hosts pdfjs's `PDFViewer` + `PDFLinkService` + `PDFFindController` + `EventBus` (page virtualization, text layer, find, and destination navigation come from pdfjs). It registers `zoom`/`pages`/`search`/`toc`/`download` capabilities with the Phase 1 toolbar context; eventBus events drive React state that flows back into those capabilities. PDF uses pdfjs's own dedicated worker and does NOT touch the Phase 1 generic parse Worker. Outline parsing borrows NID's resolution logic, reimplemented as a pure flattener.

**Tech Stack:** React 19 + TypeScript + Vite 8; `pdfjs-dist` v4 (`pdfjs-dist/web/pdf_viewer`); `vite-plugin-static-copy` (bundles cmaps for CJK); Vitest for the pure outline flattener.

**Spec:** `docs/superpowers/specs/2026-06-02-file-rendering-pdf-design.md`

> **pdfjs API note for the implementer:** the pdfjs `web/pdf_viewer` API is version-sensitive. The code below targets pdfjs-dist v4. After install, confirm the actual installed major version and the exact export/event names against `node_modules/pdfjs-dist/web/pdf_viewer.mjs` and the pdfjs examples; if a name differs (e.g. an event field or a constructor option), adjust to the installed version and note the change. Do NOT sprinkle `as any` to force it — verify the real API. pdfjs rendering cannot run in vitest (needs canvas/worker), so the rendering paths are verified by `vite build` + in-app manual testing; only the pure `flattenOutline` is unit-tested.

---

## File Structure

New (`frontend-desktop/src/preview/`):
- `viewers/pdf/pdfSetup.ts` — pdfjs worker + cmap configuration in one place; re-exports `pdfjsLib`.
- `viewers/pdf/outline.ts` — pure `flattenOutline()` (nested pdfjs outline → flat `TocItem[]` + dest map).
- `viewers/pdf/outline.test.ts` — unit tests for `flattenOutline`.
- `viewers/pdf/pdf.css` — imports pdfjs viewer CSS + theme overrides.
- `viewers/PdfViewer.tsx` — the host viewer.

Modified:
- `frontend-desktop/vite.config.ts` — copy pdfjs cmaps into the build via `vite-plugin-static-copy`.
- `frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx` — render the `toc` capability (Contents button + panel).
- `frontend-desktop/src/preview/toolbar/PreviewToolbar.test.tsx` — new test for the TOC control.
- `frontend-desktop/src/components/FilePreviewModal.tsx` — route `type === 'pdf'` → `PdfViewer`.
- `frontend-desktop/package.json` — add deps; version → `0.2.1`.
- `electron/package.json` — version → `0.2.1`.

---

## Task 1: Install pdfjs + cmaps bundling + setup module

**Files:**
- Modify: `frontend-desktop/package.json`, `frontend-desktop/vite.config.ts`
- Create: `frontend-desktop/src/preview/viewers/pdf/pdfSetup.ts`

- [ ] **Step 1: Install dependencies**

Run (in `frontend-desktop/`):
```bash
npm i pdfjs-dist@^4
npm i -D vite-plugin-static-copy@^2
```
Expected: `pdfjs-dist` in dependencies, `vite-plugin-static-copy` in devDependencies. Note the exact installed pdfjs major/minor.

- [ ] **Step 2: Configure Vite to bundle cmaps (CJK support)**

Edit `frontend-desktop/vite.config.ts` to add the static-copy plugin. The current file has `plugins: [react(), tailwindcss()]` and a `resolve.alias`. Update the imports and plugins:
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { viteStaticCopy } from 'vite-plugin-static-copy'
import path from 'path'

const backendPort = process.env.BACKEND_PORT ?? '15926'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    viteStaticCopy({
      targets: [
        // pdfjs CMaps — required so CJK (Chinese) PDFs render glyphs correctly.
        { src: 'node_modules/pdfjs-dist/cmaps/*', dest: 'cmaps' },
      ],
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api/v1/sessions': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
            }
          })
        },
      },
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
```
(This copies cmaps to `<dist>/cmaps/` at build and serves them in dev.)

- [ ] **Step 3: Create the pdfjs setup module**

Create `frontend-desktop/src/preview/viewers/pdf/pdfSetup.ts`:
```ts
import * as pdfjsLib from 'pdfjs-dist'
// Vite resolves this to a bundled worker asset URL; the worker version matches
// the main bundle because it's the same npm package (avoids version-mismatch errors).
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

// CMaps are copied to `<base>/cmaps/` by vite-plugin-static-copy (see vite.config.ts).
export const CMAP_URL = `${import.meta.env.BASE_URL}cmaps/`
export const CMAP_PACKED = true

export { pdfjsLib }
```

- [ ] **Step 4: Type-check & build**

Run: `npx tsc -b` (expected: no errors), then `npm run build` (expected: succeeds; build output shows a `cmaps/` directory copied and a `pdf.worker` chunk/asset). If the `?url` worker import or the `pdf.worker.min.mjs` path differs in the installed pdfjs version (e.g. `pdf.worker.mjs` without `.min`), adjust to the actual file under `node_modules/pdfjs-dist/build/` and note it.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/package.json frontend-desktop/package-lock.json frontend-desktop/vite.config.ts frontend-desktop/src/preview/viewers/pdf/pdfSetup.ts
git commit -m "feat(pdf): add pdfjs-dist + cmaps bundling + worker setup"
```

---

## Task 2: Outline flattener (TDD)

Pure function: nested pdfjs outline tree → flat `TocItem[]` (with `level`) + a map from item id to its pdfjs destination. Borrows NID's resolution intent but stays pure (no pdfjs calls — destinations are resolved later via the link service).

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pdf/outline.ts`
- Create: `frontend-desktop/src/preview/viewers/pdf/outline.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend-desktop/src/preview/viewers/pdf/outline.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { flattenOutline } from './outline'

// Mirrors the shape pdfjs getOutline() returns: { title, dest, items }
const sample = [
  { title: 'Chapter 1', dest: 'ch1', items: [
    { title: 'Section 1.1', dest: [/* ref */ { num: 4, gen: 0 }, { name: 'XYZ' }], items: [] },
  ] },
  { title: 'Chapter 2', dest: 'ch2', items: [] },
]

describe('flattenOutline', () => {
  it('flattens a nested outline with levels and a dest map', () => {
    const { items, dests } = flattenOutline(sample as never)
    expect(items.map((i) => [i.label, i.level])).toEqual([
      ['Chapter 1', 0],
      ['Section 1.1', 1],
      ['Chapter 2', 0],
    ])
    // every item has a unique id and a resolvable dest entry
    expect(new Set(items.map((i) => i.id)).size).toBe(3)
    for (const i of items) expect(dests.has(i.id)).toBe(true)
    expect(dests.get(items[0].id)).toBe('ch1')
  })

  it('returns empty for null/empty outline', () => {
    expect(flattenOutline(null).items).toEqual([])
    expect(flattenOutline([]).items).toEqual([])
  })

  it('uses a fallback label for untitled items', () => {
    const { items } = flattenOutline([{ title: '', dest: 'x', items: [] }] as never)
    expect(items[0].label.length).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- outline`
Expected: FAIL — cannot find module `./outline`.

- [ ] **Step 3: Implement**

Create `frontend-desktop/src/preview/viewers/pdf/outline.ts`:
```ts
import type { TocItem } from '../../toolbar/capabilities'

// A pdfjs outline destination: a named string or an explicit destination array.
export type OutlineDest = string | unknown[]

interface RawOutlineNode {
  title: string
  dest: OutlineDest | null
  items?: RawOutlineNode[]
}

export interface FlatOutline {
  items: TocItem[]
  dests: Map<string, OutlineDest>
}

// Flatten pdfjs's nested getOutline() result into a flat TocItem[] (with level for
// indentation) plus a map from each item id to its destination (resolved later via
// PDFLinkService.goToDestination). Pure — no pdfjs calls.
export function flattenOutline(outline: RawOutlineNode[] | null): FlatOutline {
  const items: TocItem[] = []
  const dests = new Map<string, OutlineDest>()
  let seq = 0

  function walk(nodes: RawOutlineNode[], level: number): void {
    for (const node of nodes) {
      const id = `o${seq++}`
      items.push({ id, label: node.title?.trim() || '(untitled)', level })
      if (node.dest != null) dests.set(id, node.dest)
      if (node.items && node.items.length) walk(node.items, level + 1)
    }
  }

  if (outline && outline.length) walk(outline, 0)
  return { items, dests }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- outline`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/viewers/pdf/outline.ts frontend-desktop/src/preview/viewers/pdf/outline.test.ts
git commit -m "feat(pdf): pure outline flattener for TOC"
```

---

## Task 3: Toolbar TOC control

Render the `toc` capability in the Phase 1 toolbar (declared in Phase 1, never rendered). A "Contents" button toggles a panel listing `toc.items`, indented by `level`; clicking an item calls `toc.goto(id)`. Generic — PPTX/DOCX reuse it later.

**Files:**
- Modify: `frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx`
- Create: `frontend-desktop/src/preview/toolbar/PreviewToolbar.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend-desktop/src/preview/toolbar/PreviewToolbar.test.tsx`:
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { PreviewToolbarProvider, usePreviewToolbar } from './PreviewToolbarContext'
import { PreviewToolbar } from './PreviewToolbar'
import { I18nProvider } from '@/i18n'

const goto = vi.fn()

function Registrar() {
  usePreviewToolbar({
    toc: { items: [{ id: 'a', label: 'Intro', level: 0 }, { id: 'b', label: 'Deep', level: 1 }], goto },
  }, [])
  return null
}

function harness() {
  return render(
    <I18nProvider>
      <PreviewToolbarProvider>
        <PreviewToolbar />
        <Registrar />
      </PreviewToolbarProvider>
    </I18nProvider>,
  )
}

describe('PreviewToolbar TOC control', () => {
  it('shows a Contents button, opens a panel, and navigates on click', () => {
    harness()
    const btn = screen.getByTitle('Contents')
    expect(btn).toBeInTheDocument()
    act(() => { btn.click() })
    const item = screen.getByText('Deep')
    expect(item).toBeInTheDocument()
    act(() => { item.click() })
    expect(goto).toHaveBeenCalledWith('b')
  })
})
```

> Note: the i18n provider export is named `LanguageProvider` in `src/i18n.tsx`, and English `preview.toc` = "Contents". If the provider import name differs, read `src/i18n.tsx` and use the actual exported provider name (it is `LanguageProvider`). Adjust the import accordingly before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- PreviewToolbar`
Expected: FAIL — the Contents button doesn't exist yet (toolbar doesn't render `toc`).

- [ ] **Step 3: Implement the TOC control**

In `frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx`:

(a) Add `ListTreeIcon` to the lucide import (the file already imports several `*Icon` names from `lucide-react`).

(b) Add a local open-state at the top of the component, next to the existing `copied`/`query` state:
```tsx
  const [tocOpen, setTocOpen] = useState(false)
```

(c) Update the `hasAny` guard to include `toc` so the toolbar shows when only a TOC exists:
```tsx
  const hasAny = caps.zoom || caps.pages || caps.search || caps.download || caps.copy || caps.toc
```

(d) Render the TOC control. Place this block just before the `{caps.pages && ...}` block (left side of the toolbar):
```tsx
      {caps.toc && caps.toc.items.length > 0 && (
        <div className="relative">
          <Button variant="ghost" size="icon" title={t('preview.toc')} onClick={() => setTocOpen((o) => !o)}>
            <ListTreeIcon size={15} />
          </Button>
          {tocOpen && (
            <div className="absolute left-0 top-full mt-1 z-20 max-h-80 overflow-auto rounded py-1"
              style={{ background: 'var(--bg1)', border: '1px solid var(--border)', minWidth: 220, boxShadow: '0 8px 24px rgba(15,31,61,.18)' }}>
              {caps.toc.items.map((item) => (
                <button
                  key={item.id}
                  onClick={() => { caps.toc!.goto(item.id); setTocOpen(false) }}
                  className="block w-full text-left text-xs py-1 truncate"
                  style={{ paddingLeft: 8 + (item.level ?? 0) * 12, paddingRight: 8, color: 'var(--t2)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- PreviewToolbar`
Expected: PASS. If lucide `ListTreeIcon` is absent in the installed version, substitute the closest existing list/outline icon (e.g. `ListIcon`) and note it. (lucide-react@1.16.0 exports both bare and `*Icon` names.)

- [ ] **Step 5: Full test + type-check**

Run: `npm test` (all pass) and `npx tsc -b` (no errors).

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/preview/toolbar/PreviewToolbar.tsx frontend-desktop/src/preview/toolbar/PreviewToolbar.test.tsx
git commit -m "feat(preview): render toc capability as a Contents panel in the toolbar"
```

---

## Task 4: PdfViewer component + theming

The host viewer. Builds the pdfjs viewer stack, loads the document, wires events → React state, registers capabilities, and renders the scroll container. Verified by tsc + build (rendering is verified manually in Task 5).

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pdf/pdf.css`
- Create: `frontend-desktop/src/preview/viewers/PdfViewer.tsx`

- [ ] **Step 1: Create the theming CSS**

Create `frontend-desktop/src/preview/viewers/pdf/pdf.css`:
```css
/* Required structural CSS from pdfjs's prebuilt viewer. */
@import 'pdfjs-dist/web/pdf_viewer.css';

/* Theme overrides to match the app. */
.ipm-pdf-container {
  position: absolute;
  inset: 0;
  overflow: auto;
  background: var(--bg2);
}
.ipm-pdf-container .pdfViewer .page {
  margin: 12px auto;
  border: 1px solid var(--border);
}
/* Text selection / find highlight colors. */
.ipm-pdf-container .textLayer ::selection { background: var(--blue-dim); }
.ipm-pdf-container .textLayer .highlight { background: var(--blue-dim); }
.ipm-pdf-container .textLayer .highlight.selected { background: var(--blue); }
```

- [ ] **Step 2: Implement PdfViewer**

Create `frontend-desktop/src/preview/viewers/PdfViewer.tsx`:
```tsx
import { useEffect, useRef, useState } from 'react'
import { EventBus, PDFViewer, PDFLinkService, PDFFindController } from 'pdfjs-dist/web/pdf_viewer.mjs'
import { pdfjsLib, CMAP_URL, CMAP_PACKED } from './pdf/pdfSetup'
import { flattenOutline, type OutlineDest } from './pdf/outline'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import './pdf/pdf.css'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.25
const ZOOM_MAX = 5

export function PdfViewer({ path, filename }: { path: string; filename: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerElRef = useRef<HTMLDivElement>(null)
  const apiRef = useRef<{
    viewer: PDFViewer
    linkService: PDFLinkService
    eventBus: EventBus
    dests: Map<string, OutlineDest>
  } | null>(null)

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
    let pdfDoc: import('pdfjs-dist').PDFDocumentProxy | null = null

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
      run: (query: string) => apiRef.current?.eventBus.dispatch('find', {
        source: null, type: '', query, caseSensitive: false, highlightAll: true, findPrevious: false,
      }),
      next: () => apiRef.current?.eventBus.dispatch('find', { source: null, type: 'again', findPrevious: false }),
      prev: () => apiRef.current?.eventBus.dispatch('find', { source: null, type: 'again', findPrevious: true }),
      clear: () => apiRef.current?.eventBus.dispatch('find', { source: null, type: '', query: '' }),
      count: matches.total,
      current: matches.current,
    },
    ...(toc.length > 0 ? {
      toc: {
        items: toc,
        goto: (id: string) => {
          const api = apiRef.current
          const dest = api?.dests.get(id)
          if (api && dest != null) api.linkService.goToDestination(dest)
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
```

- [ ] **Step 3: Type-check & build**

Run `npx tsc -b` then `npm run build`. Expected: both succeed.
**pdfjs API verification (do this carefully):** confirm against `node_modules/pdfjs-dist/web/pdf_viewer.mjs` that `EventBus`, `PDFViewer`, `PDFLinkService`, `PDFFindController` are exported with these constructor options, and that the event names (`pagesinit`, `scalechanging`, `pagechanging`, `updatefindmatchescount`, `updatefindcontrolstate`) and the `find` dispatch payload match the installed version. If pdfjs ships its own TypeScript types that conflict with the inline event-arg types above, prefer the shipped types. Adjust mismatches to the real API and note each change. If `pdf_viewer.mjs` is not the correct subpath, find the actual viewer entry (e.g. `pdfjs-dist/web/pdf_viewer`).

- [ ] **Step 4: Run full test suite**

Run `npm test`. Expected: all existing + new tests pass (PdfViewer has no unit test; it's covered by build + manual).

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/viewers/PdfViewer.tsx frontend-desktop/src/preview/viewers/pdf/pdf.css
git commit -m "feat(pdf): PdfViewer on pdfjs prebuilt viewer + capability wiring"
```

---

## Task 5: Route PDF in the host, bump version, manual verification

**Files:**
- Modify: `frontend-desktop/src/components/FilePreviewModal.tsx`, `frontend-desktop/package.json`, `electron/package.json`

- [ ] **Step 1: Route `pdf` to PdfViewer**

In `frontend-desktop/src/components/FilePreviewModal.tsx`:

(a) Add the import next to the other viewer imports:
```tsx
import { PdfViewer } from '@/preview/viewers/PdfViewer'
```

(b) In the content area, add a `pdf` branch and REMOVE `'pdf'` from the unsupported fallback. Change:
```tsx
            {type === 'text' && <TextViewer path={path} filename={name} />}
            {(type === 'pdf' || type === 'pptx' || type === 'binary') && (
```
to:
```tsx
            {type === 'text' && <TextViewer path={path} filename={name} />}
            {type === 'pdf' && <PdfViewer path={path} filename={name} />}
            {(type === 'pptx' || type === 'binary') && (
```

- [ ] **Step 2: Bump version to 0.2.1**

- `frontend-desktop/package.json`: this is private `0.0.0` — leave as-is.
- `electron/package.json`: change `"version"` to `"0.2.1"`.

- [ ] **Step 3: Type-check, build, test**

Run `npx tsc -b && npm run build` (succeeds, cmaps copied, pdf worker emitted) and `npm test` (all pass).

- [ ] **Step 4: Manual verification in the running app**

Launch dev (backend + `cd frontend-desktop && npm run dev` + Electron). In the Workspace panel open PDFs and confirm:
- A normal multi-page PDF: pages render and scroll smoothly; the toolbar shows the current page / total and it updates while scrolling; jump-to-page works.
- **A Chinese (CJK) PDF**: Chinese glyphs render correctly (proves cmaps bundling). If they show as blank boxes, the cmap URL/bundling needs fixing.
- Zoom in/out/fit + the percentage readout updates.
- Search: type a query → matches highlight; next/prev cycles; the match count shows.
- Select text with the mouse and copy.
- A PDF **with an outline**: the Contents button appears; the panel lists the outline (indented); clicking an entry jumps to the right page. A PDF **without** an outline shows no Contents button.
- Download works.
- A >50MB PDF shows the size error (not a crash); a corrupt/encrypted PDF shows an error message.
- Switching to another file and back, and closing the modal, leaves no console errors (document destroyed cleanly).

Record results. Fix any failure before committing.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/components/FilePreviewModal.tsx electron/package.json
git commit -m "feat(pdf): route .pdf to PdfViewer; bump desktop version to 0.2.1"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Continuous scroll + lazy/virtualized pages → pdfjs `PDFViewer` (Task 4). ✓
- Zoom (in/out/fit/scale) → Task 4 zoom capability. ✓
- Page tracking + jump → Task 4 pages capability. ✓
- Search (highlight + next/prev + count) → Task 4 search capability via PDFFindController. ✓
- Text selection/copy → pdfjs text layer (Task 4) + theming (Task 4 css). ✓
- Outline/TOC parse + present + navigate → flattenOutline (Task 2) + toolbar control (Task 3) + linkService.goToDestination (Task 4). ✓
- Download → Task 4. ✓
- CJK cmaps → Task 1 (bundling) + Task 4 (cMapUrl) + Task 5 manual CJK check. ✓
- pdfjs own worker, bypasses generic pipeline → Task 1 setup. ✓
- Error/large-file handling → fetchOrThrow + getDocument catch + virtualization (Task 4). ✓
- Routing + version 0.2.1 → Task 5. ✓
- Out-of-scope items (password entry, annotations, thumbnails, rotation, print) → not implemented. ✓

**Placeholder scan:** No TBD/TODO/vague steps; every code step has complete code; pdfjs API uncertainty is handled by explicit verify-against-installed-version instructions, not placeholders.

**Type consistency:** `TocItem` (`{id,label,level?}`) from Phase 1 `capabilities.ts` used consistently in `outline.ts`, the toolbar control, and PdfViewer. `OutlineDest` defined in `outline.ts` and reused in PdfViewer. Capability shapes (`zoom`/`pages`/`search`/`toc`/`download`) match Phase 1 `ViewerCapabilities`. `usePreviewToolbar(caps, deps)` signature matches Phase 1.

**Known risk (flagged, not a placeholder):** the pdfjs `web/pdf_viewer` API is version-sensitive and cannot be unit-tested in vitest; Tasks 1 & 4 instruct the implementer to verify exact exports/events/worker-path against the installed pdfjs-dist and adjust. Manual verification (Task 5) covers all rendering paths.
