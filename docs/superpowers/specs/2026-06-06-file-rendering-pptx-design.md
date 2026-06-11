# PPTX Viewer — Design (Phase 2, slice 2)

- **Date**: 2026-06-06
- **Target version**: `0.2.10`
- **Status**: design approved, ready for implementation plan
- **Parent**: `2026-06-02-file-rendering-upgrade-design.md` (overall 0.2.x upgrade)
- **Predecessor**: PDF viewer (slice 1) shipped as 0.2.9 via PR #1 (merged into master @ 717469c)

---

## 1. Goal & scope

Add a high-fidelity, read-only PPTX viewer to IPMaster Desktop's file preview, replacing the current "unsupported" fallback for `.pptx`. The strategy is **align with NID's implementation** (`D:\20_code\NetworkIntegrationDesign`'s `pptxViewerPanel.ts`, ~3000 lines) — that file is the project's actual PPTX know-how, accumulated by hitting real-world PPT corner cases. We **1:1 port** its renderer rather than rewriting it in React, so every corner case it has already handled is preserved verbatim. Verification is by side-by-side visual comparison against NID.

In scope:

- Render PPTX slides with NID-level fidelity: text (font/colour/bold/italic/underline/strike/super-sub/highlight/hyperlink), images (with crop), tables (with cell merge + borders + accents), connectors (with arrows + dash styles), preset + parametric + custom shapes (with real outlines), slide backgrounds (image + colour), rotation, shadow, vertical text, vertical anchor, bullets, auto-numbering, master + layout + slide three-layer stacking.
- Continuous vertical scroll of all slides (PDF-style — re-uses the toolbar shell built in Phase 1).
- Toolbar capabilities: **pages** (count / current / jump), **zoom** (fit-width default + manual scale), **toc** (left sidebar; entries derived from each slide's title), **download** (raw .pptx bytes).
- Parsing runs in the existing Phase 1 Web Worker pipeline.
- CJK / mixed-script text rendering.

Out of scope (explicitly deferred — see §9):

- Full-text search (browser Ctrl+F is the fallback).
- Slide animations, transitions, build effects.
- Embedded video / audio.
- Speaker notes.
- Slideshow / presentation mode.
- Editing or export.
- Embedded fonts (themeFonts string + system fallback only).

## 2. Key decisions (settled in brainstorming)

1. **Continuous vertical scroll** (not single-slide nav) — matches the PDF viewer pattern, re-uses the shell.
2. **Full fidelity, by porting NID's renderer verbatim** (not React-rewriting and not a low-fidelity React stub). NID's `_buildShapeParts` + `_presetGeomPaths` + `_parametricPresetBuilders` + `_extractCustomGeomPath` + their helpers are the corner-case payload — we copy them, strip vscode/webview deps, and call them from a React host.
3. **Worker pipeline** — extend `ParseKind` to include `'pptx'`. The parser is already DOM-free (verified by the 2026-06-02 spike on branch `spike/pptx-port`).
4. **Toolbar = pages + zoom + toc + download**; no search this slice (Ctrl+F covers it for now). Slide titles for TOC are extracted by a heuristic (first non-empty run of the first text shape).
5. **DOCX / XLSX are out of scope for this slice.** NID's DOCX viewer just wraps `docx-preview` (a third-party lib) — we plan to install that as a separate slice 3. NID's XLSX viewer doesn't even read cell styles — true high-fidelity XLSX needs new design and is slice 4.

## 3. Architecture & where it runs

```
.pptx in workspace
  ↓ click in WorkspacePanel
FilePreviewModal (host shell from Phase 1)
  ↓ type === 'pptx'
PptxViewer
  ↓ fetchOrThrow(rawUrl)
  ↓ → arrayBuffer
parseInWorker('pptx', buf, { signal })
  ↓ posts to fileParser.worker.ts
  ↓ worker calls parsePptx(buf) [ported from spike]
  ↓ → { slides: SlideData[], themeFonts: Map<string,string> }
  ↓ structured-clone back to main thread
slideToHtml(slide, slideIdx, themeFonts) for each slide
  ↓ delegates to ported _buildShapeParts (master + layout + slide layers)
  ↓ → { css: string, html: string } per slide
PptxViewer renders:
  - One <style> with all per-slide CSS, prefixed with .ipm-pptx-root
  - One <div className="ipm-pptx-slide"> per slide,
    dangerouslySetInnerHTML = that slide's html
Toolbar capability registration:
  - pages: IntersectionObserver tracks the visible slide
  - zoom: --pptx-zoom CSS variable on the root
  - toc: extractTitle(slide) → TocItem[]; goto scrolls to that .ipm-pptx-slide
  - download: rawUrl + filename
```

Backend unchanged — bytes come from `GET /api/v1/workspace/file/raw?path=` (existing 50MB cap).

## 4. Module structure (`frontend-desktop/src/preview/`)

```
worker/
  parsers/pptx.ts                   ← cherry-pick from spike (2240 lines); clean
                                      3 noUnusedLocals (NS_REL,
                                      _resolveSymbolText, _isDark)
  parsers/pptx.test.ts              ← already on spike (6-slide sample asserts)
  parsers/__fixtures__/sample.pptx  ← already on spike
  protocol.ts                       ← MOD: ParseKind |= 'pptx';
                                      ParseResultData.pptx defined
  fileParser.worker.ts              ← MOD: dispatch case 'pptx' → parsePptx

viewers/
  PptxViewer.tsx                    ← NEW: React host. Loads doc, registers
                                      capabilities, owns scroll container,
                                      hosts IntersectionObserver, owns the
                                      --pptx-zoom variable.
  pptx/                             ← NEW directory
    pptx.css                        ← Theme tokens, slide-card shell, zoom var
    slideToHtml.ts                  ← NEW: pure function. For one slide,
                                      stacks master + layout + slide shapes
                                      (mirrors NID _buildHtml's inner loop),
                                      calls ported _buildShapeParts for each,
                                      prefixes the resulting CSS with
                                      `.ipm-pptx-root `, returns { css, html }.
    extractTitle.ts                 ← NEW: pure. First non-empty run text of
                                      first text shape, trimmed to 50 chars.
    extractTitle.test.ts            ← NEW: 3 cases.
    ported/                         ← NEW: 1:1 mirror of NID code
      shapeBuilder.ts               ← _buildShapeParts + escapeHtml + small
                                      helpers (colour parsing, font merging,
                                      bullet resolution, auto-numbering)
      presetGeomPaths.ts            ← _presetGeomPaths table (~200 shapes)
      parametricGeoms.ts            ← _parametricPresetBuilders +
                                      _extractCustomGeomPath
      README.md                     ← One-page note: "this directory is a
                                      verbatim port from NID's
                                      src/webview/pptxViewerPanel.ts.
                                      To debug a fidelity issue, compare
                                      against the same function in NID."

components/
  FilePreviewModal.tsx              ← MOD: type === 'pptx' → <PptxViewer>

electron/package.json               ← MOD: 0.2.9 → 0.2.10
```

The `ported/` directory is deliberately walled off — calling its contents "ours" would mislead future maintainers. Its README anchors the alignment story.

## 5. CSS isolation

NID's class names are `sh-{slideIdx}-{shapeIdx}` — already unique within a deck. To stop these rules from leaking outside the viewer (into toolbar, sidebar, etc.), `slideToHtml` rewrites every selector by prefixing `.ipm-pptx-root `. Regex over the CSS text (preserve leading whitespace / commas in selector lists):

Each selector in the NID-emitted CSS is rewritten:

```
.sh-0-1 { ... }     →    .ipm-pptx-root .sh-0-1 { ... }
```

NID's shape CSS only uses single-class selectors at rule heads separated by `}` or `,` — no `@media`, no pseudo-selectors, no nested combinators. A single regex over the CSS text (preserving whitespace and selector-list commas) is sufficient. Exposed from `slideToHtml.ts` as `prefixSelectors(css: string, prefix: string): string`.

The PptxViewer root `<div>` has `className="ipm-pptx-root"`. All ported CSS is contained beneath it.

Container queries (NID uses `cqi`) require a `container-type: inline-size` on a sized ancestor — we put that on the per-slide `.ipm-pptx-slide` element (NID's `_buildHtml` puts it on the slide wrapper too). This is what makes shape sizes follow container width automatically.

## 6. Zoom

NID gets fit-width for free via container queries + `--pt`. Manual zoom layers on top with a single CSS variable:

- Root: `style={{ ['--pptx-zoom' as string]: scale }}`
- Per-slide width: `width: calc(100% * var(--pptx-zoom, 1))`
- Container query then re-computes shape sizes against the new container width

State and capability:

- `const [scale, setScale] = useState(1)`; `fitModeRef` for "manual zoom vs. fit"
- `zoom.in/out`: `setScale(...)`, `fitModeRef.current = false`
- `zoom.fit`: `setScale(1)`, `fitModeRef.current = true`
- `zoom.reset`: same as `fit`
- `scale` exposed to toolbar for percentage readout

No `ResizeObserver` needed for fit mode — container queries handle that natively. (Contrast with PDF, which used pdfjs's scale and required `ResizeObserver` to re-fit.)

## 7. Page (current slide) tracking

`IntersectionObserver` on every `.ipm-pptx-slide`, threshold around 0.5 with `rootMargin: '-40% 0px -40% 0px'` so the slide whose centre is in the viewport's middle band counts as current. Update React state → register `pages.current`. `pages.goto(n)` → `slides[n - 1].scrollIntoView({ block: 'start' })`.

## 8. TOC title extraction (`extractTitle.ts`)

Pure function, unit-tested:

```ts
function extractTitle(slide: SlideData): string {
  // Find first text shape; return its first non-empty run text, trimmed to
  // 50 chars (CJK-safe — uses Array.from for code-point length, slices).
  // Returns '' if no usable text exists.
}
```

`TocSidebar` (reused from Phase 1) shows:

- `"3. 系统架构总览"` when extractTitle returns non-empty
- `"Slide 3"` (i18n key `preview.slideN`, parameter `n`) when title is empty

Click navigates by calling `pages.goto(n)`. TOC auto-opens for documents with at least one non-empty title (same Acrobat-style behaviour we built for PDF).

PPT OOXML actually marks the title placeholder explicitly (`<ph type="title">`), but the spike's parser doesn't propagate this — NID's renderer didn't need it because it never built a TOC. We accept the heuristic and revisit only if real decks expose mismatches.

## 9. Worker pipeline extension

`protocol.ts`:

```ts
export type ParseKind = 'xlsx' | 'pptx'

export interface ParseResultData {
  xlsx: SheetData[]
  pptx: PptxResult
}

export interface PptxResult {
  slides: SlideData[]
  themeFonts: Record<string, string>
  // ^ Map<string,string> from the parser; converted to a plain object
  //   before posting because structured-clone passes through Maps in
  //   Electron Chromium fine but we want JSON-safe typing in protocol.
}
```

`fileParser.worker.ts` dispatch gains a `'pptx'` arm calling `parsePptx`.

`parseClient.ts` is untouched — the generic `parseInWorker<K extends ParseKind>` already covers the new kind via the index type.

Phase 1 abort caveat applies: the worker keeps parsing if the user closes the modal mid-parse. The parser is async (jszip unzip + image base64-encode), so the wasted CPU is real (a few seconds on a heavy deck) but no data is corrupted. We accept this for slice 2 — worker-side cancellation is a Phase 3 polish item.

## 10. Error / loading

- Loading state: existing shared `<Loading />` spinner until `parseInWorker` resolves and the first slide is in the DOM.
- Parse failure: caught in the effect, surfaced via `<ErrorMsg />`. Likely causes: corrupt zip, non-standard OOXML, > 50 MB (backend 413 already surfaces via `fetchOrThrow`).
- Image data URIs (base64) in `SlideData` are sometimes large — that's the cost of structured-clone from worker. Spike measured 6-slide deck at ~90 KB; real decks with many photos may be several MB. No mitigation this slice (single-document at a time, lives only as long as the modal is open).
- Switching files mid-parse: `AbortController` aborts the fetch and the client-side pending entry. The worker continues to completion but its result is discarded by the client. UI cleanly switches to the new file.

## 11. Testing

**Unit (vitest):**

- `extractTitle.test.ts` — 3 cases: ordinary title, no text shape, all-whitespace runs.
- `parsers/pptx.test.ts` — already on spike. Sample deck parses to 6 slides with non-empty extracted text.
- `parseClient.test.ts` (existing) — add a `'pptx'` fake-worker case to verify generic return typing and structured-clone of the PptxResult shape.

**Build:**

- `cd frontend-desktop && npx tsc -b` clean
- `cd frontend-desktop && npm test` passes — current baseline is 19 tests (8 files); this slice adds at minimum: 3 cases in `extractTitle.test.ts` and 1 generic-typing case in `parseClient.test.ts`, putting the floor at 23 / 9 files
- `cd frontend-desktop && npm run build` clean

**Manual fidelity verification (the actual proof of "align with NID"):**

1. Sample baseline (`sample.pptx` from the spike, originally NID's `NetworkDesigner_PPT.pptx`):
   - Open in NID (VS Code with the network-designer extension)
   - Open in our 0.2.10 IPMaster Desktop
   - Screenshot each slide side-by-side; differences = corner-case gaps
2. Repeat with 1–2 real business decks the user supplies (optional)

Differences found → either:
- A helper from `pptxViewerPanel.ts` wasn't ported (most likely cause) — add it
- The new `slideToHtml` wrapper diverges from NID's `_buildHtml` — adjust
- Or a real PPT feature NID also doesn't handle — log as known limitation

## 12. Versioning

`0.2.10` (master is currently `717469c` at 0.2.9 after PR #1).

## 13. Non-goals (deferred)

| Item | Why deferred |
|---|---|
| Full-text search | Ctrl+F covers basic cases; PPT text is fragmented across shape runs, real impl is a slice on its own. |
| Animations / transitions | Static rendering matches user expectation for a viewer. |
| Embedded video / audio | Media playback is a separate slice. |
| Speaker notes | Available in OOXML but unused for the viewer use case. |
| Slideshow / presentation mode | Zoom + scroll already covers "view bigger". |
| Editing / export | Pure read-only this slice. |
| Embedded fonts (`p:embeddedFont`) | Use `themeFonts` strings + system fallback. Decks with custom fonts will look slightly different. |
| Worker-side parse cancel | Accepted CPU waste on mid-parse close. Phase 3 polish. |

## 14. Dependencies added

None new. The spike already brought `jszip@3.10.1` and `@xmldom/xmldom@0.8.13` in for the parser. The ported renderer is plain TypeScript using only DOM-free language features.

## 15. Known risks

- **The ported renderer's helper dependency chain.** NID's `_buildShapeParts` calls many small helpers (color resolution, font merging, autonum, escape). Some live elsewhere in `pptxViewerPanel.ts` and a few in `htmlUtils.ts`. The implementation plan's first hands-on task is "build the import graph and bring exactly those helpers in" — getting tsc to compile the ported directory is the gate before integration work begins.
- **Real-deck fidelity gaps.** Even with verbatim porting, real PPT decks may expose corner cases NID also doesn't fully handle. Mitigation is the side-by-side comparison protocol in §11; gaps that NID also has become documented known limitations rather than this PR's bugs.
- **Image-data-URI memory pressure.** Multi-MB decks with many photos may slow worker→main structured clone. Not a concern for typical decks but worth measuring against a worst-case sample before merge.

## 16. Sequence after this slice

- **Slice 3 — DOCX high-fidelity**: install `docx-preview`, swap viewer. Small. Independent.
- **Slice 4 — XLSX high-fidelity**: reads `cellStyles`, renders fills/fonts/borders/number formats. New design needed; NID doesn't have this either.
- **Phase 3 polish**: virtual scrolling for huge decks, worker-side cancel, full-text search.
