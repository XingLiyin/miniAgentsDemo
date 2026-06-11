# PDF Viewer — Design (Phase 2, first slice)

- **Date**: 2026-06-02
- **Target version**: `0.2.1` (first per-format release of Phase 2)
- **Status**: design approved, ready for implementation plan
- **Parent**: `2026-06-02-file-rendering-upgrade-design.md` (overall 0.2.x upgrade). Phase 2 is decomposed into independent per-format slices (PDF → PPTX → DOCX); this spec covers PDF only.

---

## 1. Goal & scope

Add a real PDF viewer to IPMaster Desktop's file preview, replacing the current "unsupported" fallback for `.pdf`. It plugs into the Phase 1 preview platform (`src/preview/`): a host viewer that registers toolbar capabilities. In scope for this slice:

- Continuous-scroll, lazily-rendered pages (large PDFs must not freeze the UI).
- Zoom (in / out / fit-to-width / scale readout).
- Page tracking (current / total) + jump-to-page.
- In-document **search** with match highlight + next/prev (via a real text layer).
- Text **selection / copy** (text layer).
- **Outline / TOC** parsing and presentation, with click-to-navigate.
- Download.
- **CJK** support (Chinese PDFs render correctly via cmaps).

Out of scope (deferred): password-protected PDF entry, annotations/forms editing, page thumbnails, rotation, printing, PDF export.

## 2. Key decisions (settled in brainstorming)

1. **Use pdfjs's prebuilt viewer** `pdfjs-dist/web/pdf_viewer` (`PDFViewer` + `PDFFindController` + `PDFLinkService` + `EventBus`) — NOT a hand-rolled low-level renderer. It provides page virtualization, a working find controller, text layer, and link/destination navigation out of the box.
   - **Why not port NID's PDF code:** NID (`D:\20_code\NetworkIntegrationDesign\src\webview\documentReader.ts`) hand-rolls the low-level pdfjs API inside a VS Code webview HTML string. It has **no virtualization** (renders all pages eagerly → chokes on large PDFs) and its **in-PDF search is broken** (a `.text-layer` vs `.textLayer` selector typo matches nothing). It is tangled with vscode-resource URIs, CSP nonces, a CSP-specific blob-URL worker hack, and hardcoded strings. Not a clean reuse.
   - **What we DO borrow from NID:** its outline/TOC resolution logic (`getOutline()` → resolve `dest` via `getDestination`/`getPageIndex` → navigate), which is clean and portable. We reimplement it in React and navigate via `PDFLinkService.goToDestination()`.
2. **PDF bypasses the Phase 1 generic parse Worker.** pdfjs manages its own dedicated worker (`GlobalWorkerOptions.workerSrc`). The `src/preview/worker/` pipeline (xlsx now, pptx later) is unrelated.
3. **Continuous scroll + lazy rendering** is delivered by `PDFViewer`'s built-in virtualization (no custom IntersectionObserver needed).
4. **Full feature set including the text layer** (search + selection), per the approved scope.

## 3. Architecture & where it runs

- `fileType('pdf')` already returns `'pdf'` (added in Phase 1); the host `FilePreviewModal` currently routes pdf/pptx/binary to the "unsupported" message. This slice adds a `PdfViewer` and routes `type === 'pdf'` to it.
- Backend unchanged: bytes come from `GET /api/v1/workspace/file/raw?path=` (50MB cap), fetched with the existing `fetchOrThrow`.
- `PdfViewer` instantiates the pdfjs viewer stack bound to a container `ref`, loads the document, holds React state for live toolbar values, and registers `zoom`/`pages`/`search`/`toc`/`download` capabilities via `usePreviewToolbar`.

## 4. Module structure (`frontend-desktop/src/preview/`)

- `viewers/PdfViewer.tsx` — the host viewer. Creates `EventBus`, `PDFLinkService`, `PDFFindController`, `PDFViewer`; loads the doc; wires eventBus listeners → React state; registers toolbar capabilities; renders the scroll container + (when an outline exists) drives the toolbar TOC control.
- `viewers/pdf/pdfSetup.ts` — one-time pdfjs configuration: `GlobalWorkerOptions.workerSrc` (Vite `?url` import) and the cmaps URL constant. Keeps the worker/cmaps wiring in one place.
- `viewers/pdf/outline.ts` — `flattenOutline(outline): { items: TocItem[]; dests: Map<string, OutlineDest> }`. Pure function (unit-tested): walks the nested pdfjs outline tree into a flat `TocItem[]` with `level`, and a map from each item `id` to its `dest` for navigation.
- `viewers/pdf/pdf.css` — imports `pdfjs-dist/web/pdf_viewer.css` and overrides backgrounds / text-selection color to our `var(--*)` theme.
- **Phase 1 toolbar enhancement** (`toolbar/PreviewToolbar.tsx`): add rendering for the `toc` capability — a "Contents" button that toggles a panel listing `toc.items` (indented by `level`), each invoking `toc.goto(id)`. This was declared in Phase 1 but never rendered (final-review Minor #3). PDF is the first driver; the control is generic so PPTX (slide list) and DOCX (headings) reuse it.

## 5. Toolbar capability bridge

`PdfViewer` registers (and re-registers via `usePreviewToolbar(caps, deps)` when live state changes):

| Capability | Bridge to pdf_viewer |
|---|---|
| `zoom` | `in`/`out` adjust `viewer.currentScale` by a step (clamped); `fit` → `viewer.currentScaleValue = 'page-width'`; `reset` → scale 1.0; `scale` read from the `scalechanging` event into React state |
| `pages` | `count = viewer.pagesCount`; `current` from the `pagechanging` event; `goto(n)` → `viewer.currentPageNumber = n` |
| `search` | `run(q)` → `eventBus.dispatch('find', { type: '', query: q, caseSensitive: false, highlightAll: true })`; `next`/`prev` → `dispatch('find', { type: 'again', findPrevious })`; `clear` → dispatch empty query; `count`/`current` from `updatefindmatchescount` |
| `toc` | `items` = `flattenOutline(...).items`; `goto(id)` → resolve the stored `dest` → `linkService.goToDestination(dest)` |
| `download` | `{ url: rawUrl(path), filename }` |

The toolbar's local search input calls `caps.search.run(q)` on change (same pattern Phase 1 already uses).

## 6. Outline / TOC

After `setDocument`, call `pdfDocument.getOutline()`. If null/empty → do not register `toc` (no Contents button). Otherwise `flattenOutline` produces `TocItem[]` (`{ id, label, level }`) plus a `dests` map. `toc.goto(id)` looks up the dest and calls `linkService.goToDestination(dest)` (pdfjs resolves named string dests internally; array dests pass through). This mirrors NID's `resolveDestPage`/`buildTree` logic but uses the link service instead of manual `scrollIntoView`.

## 7. Search

Delegated entirely to `PDFFindController` via the eventBus (no custom text scanning — avoids NID's broken-selector class of bug). `highlightAll: true` highlights all matches; `findPrevious` toggles direction. Match count + current index come from `updatefindmatchescount`/`updatefindcontrolstate` events into React state, surfaced through `search.count`/`search.current` so the toolbar can show "n matches".

## 8. Zoom & page tracking

Zoom uses `PDFViewer`'s scale system (it re-renders pages at the new scale internally; virtualization keeps this cheap). Default on open = fit-to-width (`currentScaleValue = 'page-width'`), with a `ResizeObserver` re-applying fit on container resize while in fit mode. Current page is driven by the `pagechanging` event (pdf_viewer determines the most-visible page during scroll).

## 9. pdfjs / worker / cmaps bundling (Vite + Electron)

- Dependency: `pdfjs-dist` (v4+; provides `web/pdf_viewer` and the `TextLayer` API).
- Worker: `import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'` then `pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl`. Works directly in the Electron renderer; no blob-URL hack. Worker version matches the main bundle (same npm package), avoiding version-mismatch failures.
- cmaps (CJK): point `getDocument({ data, cMapUrl, cMapPacked: true })` at the bundled `pdfjs-dist/cmaps`. Resolve `cMapUrl` as a bundled asset URL (Vite). Verify Chinese PDFs render in manual testing.
- Document load: `fetchOrThrow(rawUrl(path)) → arrayBuffer() → getDocument({ data, cMapUrl, cMapPacked: true })`.

## 10. Theming

Import `pdfjs-dist/web/pdf_viewer.css` (required structural CSS), then an override stylesheet (`pdf/pdf.css`) mapping viewer backgrounds and the text-layer selection color to `var(--bg2)`, `var(--blue)/blue-dim`, etc., so the PDF surface matches the app.

## 11. Error / loading / large files

- `fetchOrThrow` surfaces 413 (over 50MB) and other HTTP errors as readable messages → `ErrorMsg`.
- `getDocument` rejection (corrupt / encrypted) → caught → `ErrorMsg`. Password-protected PDFs are reported as an error, not prompted (deferred).
- Loading state shows the shared `Loading` spinner until the first page renders.
- Large PDFs: handled by `PDFViewer` virtualization (the core reason for choosing approach A).
- Unmount/file-switch: destroy the pdfjs document (`pdfDocument.destroy()`) and detach eventBus listeners in the effect cleanup to free memory and the worker.

## 12. Testing

- **Unit (vitest):** `flattenOutline` — a fake nested outline tree → assert flat `TocItem[]` with correct `level` ordering and a complete `dests` map; assert empty/null outline → empty items.
- **Build:** `tsc -b && vite build` succeeds and emits the pdfjs worker as its own chunk.
- **Manual (in-app):** open a real PDF (include a **Chinese** PDF for cmaps) — confirm: pages render and scroll; current-page indicator tracks scroll; zoom in/out/fit + percentage; search highlights + next/prev + match count; text selection/copy; the Contents panel lists the outline and clicking an entry jumps to the right page; download works; a >50MB file shows the size error; a corrupt file shows an error not a crash.
- pdfjs is not rendered inside vitest (needs canvas/worker) — manual verification covers the rendering paths.

## 13. Versioning

This slice ships as **`0.2.1`** (`electron/package.json`). Subsequent Phase 2 slices: PPTX = `0.2.2`, DOCX = `0.2.3`; Phase 3 polish = `0.2.4`+.

## 14. Dependencies added

- `pdfjs-dist` (v4+).

No backend changes. No new Python deps.

## 15. Touch list (files created / modified)

- Create: `viewers/PdfViewer.tsx`, `viewers/pdf/pdfSetup.ts`, `viewers/pdf/outline.ts`, `viewers/pdf/outline.test.ts`, `viewers/pdf/pdf.css`.
- Modify: `components/FilePreviewModal.tsx` (route `type === 'pdf'` → `PdfViewer`), `toolbar/PreviewToolbar.tsx` (render the `toc` control), `i18n.tsx` (Contents/search-match strings if missing), `package.json` (pdfjs-dist; version → 0.2.1).
- No change to the worker pipeline, other viewers, or the backend.
