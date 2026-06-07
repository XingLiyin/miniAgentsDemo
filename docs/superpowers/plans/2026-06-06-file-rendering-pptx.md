# PPTX Viewer Implementation Plan (Phase 2, slice 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** High-fidelity, read-only PPTX viewer in IPMaster Desktop — slides rendered with NID-level fidelity by **1:1 porting** NID's `_buildShapeParts` + helpers, hosted in a React component that uses the Phase 1 toolbar platform.

**Architecture:** Worker pipeline (extended with `'pptx'` kind) parses bytes via the existing spike-validated `parsePptx`. Main-thread `slideToHtml(slide, idx)` calls a verbatim port of NID's `_buildShapeParts`, prefixes selectors with `.ipm-pptx-root` to scope CSS, and returns `{ css, html }`. `PptxViewer` renders one `<style>` + N `<div className="ipm-pptx-slide" dangerouslySetInnerHTML={...}>`, registers `pages`/`zoom`/`toc`/`download` toolbar capabilities, and tracks current slide via `IntersectionObserver`. Zoom is a single CSS variable `--pptx-zoom`; container queries from NID's CSS handle width-based shape scaling automatically.

**Tech Stack:** React 19 + TypeScript + Vite 8; spike's `parsers/pptx.ts` (jszip 3.10.1 + @xmldom/xmldom 0.8.13); verbatim port of selected NID functions; vitest for pure-function tests.

**Spec:** `docs/superpowers/specs/2026-06-06-file-rendering-pptx-design.md`

**NID source root for porting:** `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts` (3064 lines; the "ported" directory mirrors selected ranges).

> **Implementer note — "porting from NID":** Several tasks instruct you to copy verbatim line ranges from NID. Read those lines from `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts` (open in a viewer or `cat -n`). Preserve the original comments — they describe the corner cases. The only modifications allowed are: (a) drop `vscode`/webview imports; (b) replace external `escapeHtml` import with the local copy this plan adds; (c) `export` whatever the renderer module needs to re-export. Do not "improve" the code — the alignment story is broken if you do.

---

## File Structure

New in `frontend-desktop/src/preview/`:

- `worker/parsers/pptx.ts` — checked out from `spike/pptx-port` (2240 lines, parser only, DOM-free).
- `worker/parsers/pptx.test.ts` — renamed from spike's `pptx.spike.test.ts`.
- `worker/parsers/__fixtures__/sample.pptx` — checked out from spike.
- `viewers/PptxViewer.tsx` — React host (~120 lines).
- `viewers/pptx/pptx.css` — root scoping + zoom variable + slide card.
- `viewers/pptx/slideToHtml.ts` — pure function: `slideToHtml(slide, slideIdx, themeFonts) → { css, html }`.
- `viewers/pptx/slideToHtml.test.ts` — unit tests for `prefixSelectors` only.
- `viewers/pptx/extractTitle.ts` — pure function: `extractTitle(slide) → string`.
- `viewers/pptx/extractTitle.test.ts` — 3 cases.
- `viewers/pptx/ported/README.md` — alignment note.
- `viewers/pptx/ported/presetGeomPaths.ts` — `_presetGeomPaths` data table verbatim from NID.
- `viewers/pptx/ported/shapeBuilder.ts` — `_buildShapeParts` + the small helpers it calls (`escapeHtml`, `_isDark`, `_resolveSymbolText`, `_formatAutoNum`, `_toRoman`, `_toAlpha`), all verbatim from NID.

Modified:

- `worker/protocol.ts` — `ParseKind |= 'pptx'`; `ParseResultData.pptx` typing.
- `worker/fileParser.worker.ts` — `case 'pptx'` in dispatch.
- `worker/parseClient.test.ts` — one new case verifying `parseInWorker<'pptx'>` typing.
- `components/FilePreviewModal.tsx` — route `type === 'pptx'` to `PptxViewer`; remove `'pptx'` from the binary fallback predicate.
- `i18n.tsx` — `preview.slideN` translation pair.
- `electron/package.json` — `0.2.9` → `0.2.10`.
- `frontend-desktop/package.json` / `package-lock.json` — adds `jszip@^3.10` + `@xmldom/xmldom@^0.8` (already on spike; brought across in Task 2).

---

## Task 1: Extend worker protocol for `'pptx'`

Types-only change in `protocol.ts` so subsequent tasks can compile against the new kind. The parser is not wired yet (Task 3).

**Files:**
- Modify: `frontend-desktop/src/preview/worker/protocol.ts`

- [ ] **Step 1: Edit the protocol**

In `frontend-desktop/src/preview/worker/protocol.ts`, change:

```ts
export type ParseKind = 'xlsx'
```

to:

```ts
export type ParseKind = 'xlsx' | 'pptx'
```

Then, **above** the existing `ParseResultData` interface, add the placeholder types and extend the map. The full result section should read:

```ts
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

// Maps each ParseKind to its result payload type. Extend when adding kinds.
export interface ParseResultData {
  xlsx: SheetData[]
  pptx: PptxResult
}
```

Note the type-only import of `SlideData` — it does **not** pull the parser code into protocol.ts; it just borrows the type.

- [ ] **Step 2: Type-check (still expected to fail — parser file doesn't exist yet)**

Run (in `frontend-desktop/`): `npx tsc -b`
Expected: type error about `./parsers/pptx` module not found.

This is expected; Task 2 will add the file. Do NOT commit yet.

> Aside: this task is intentionally not committed standalone. It compiles only after Task 2 brings the parser file. Task 2's commit covers both.

---

## Task 2: Bring the spike's parser + fixtures + deps into master

The spike branch `spike/pptx-port` already holds the validated parser and sample. We selectively check out those files (no merge) and install the two new npm deps. The spike's test file is renamed to drop the `.spike` infix so vitest's normal glob picks it up cleanly.

**Files:**
- Create (from spike): `frontend-desktop/src/preview/worker/parsers/pptx.ts`
- Create (from spike): `frontend-desktop/src/preview/worker/parsers/__fixtures__/sample.pptx`
- Create (from spike, renamed): `frontend-desktop/src/preview/worker/parsers/pptx.test.ts`
- Modify: `frontend-desktop/package.json`, `frontend-desktop/package-lock.json`

- [ ] **Step 1: Check out parser + fixtures from the spike branch**

Run (from repo root):

```bash
git checkout spike/pptx-port -- \
  frontend-desktop/src/preview/worker/parsers/pptx.ts \
  frontend-desktop/src/preview/worker/parsers/__fixtures__/sample.pptx
```

Expected: two files staged for commit (parser ~2240 lines + binary fixture).

- [ ] **Step 2: Get the spike test and rename it**

Run:

```bash
git show spike/pptx-port:frontend-desktop/src/preview/worker/parsers/pptx.spike.test.ts \
  > frontend-desktop/src/preview/worker/parsers/pptx.test.ts
```

- [ ] **Step 3: Install the parser's runtime deps**

Run (in `frontend-desktop/`):

```bash
npm i jszip@^3.10 @xmldom/xmldom@^0.8
```

Expected: both added to `dependencies`; lockfile updated. (These are the same versions the spike used.)

- [ ] **Step 4: Verify type-check and tests pass**

Run (in `frontend-desktop/`):

```bash
npx tsc -b
npm test -- pptx
```

Expected: `tsc -b` clean (Task 1's protocol change now compiles because `parsers/pptx` exists). `npm test -- pptx` runs `pptx.test.ts` and passes (the spike test asserts the 6-slide sample parses with non-empty text).

If TypeScript complains about 3 leftover `noUnusedLocals` warnings (`NS_REL`, `_resolveSymbolText`, `_isDark` — the spike's known leftovers), suppress just those by deleting their declarations from `pptx.ts` IF they're truly unused. If they are referenced anywhere by the parser, leave them. Verify by `grep -n NS_REL frontend-desktop/src/preview/worker/parsers/pptx.ts` etc. (Note: `_isDark` and `_resolveSymbolText` are also used by the renderer in Task 6 — but the renderer has its own copies in `shapeBuilder.ts`. If they're unused in `pptx.ts`, delete them there; they survive in the renderer.)

- [ ] **Step 5: Commit both protocol + parser changes**

```bash
git add frontend-desktop/src/preview/worker/protocol.ts \
        frontend-desktop/src/preview/worker/parsers/pptx.ts \
        frontend-desktop/src/preview/worker/parsers/__fixtures__/sample.pptx \
        frontend-desktop/src/preview/worker/parsers/pptx.test.ts \
        frontend-desktop/package.json \
        frontend-desktop/package-lock.json
git commit -m "feat(pptx): bring spike parser + protocol kind into master"
```

---

## Task 3: Wire worker dispatch + parseClient generic test

Worker module now needs to handle `'pptx'`. Add the dispatch arm and a unit test that the typed client returns the new kind correctly.

**Files:**
- Modify: `frontend-desktop/src/preview/worker/fileParser.worker.ts`
- Modify: `frontend-desktop/src/preview/worker/parseClient.test.ts`

- [ ] **Step 1: Add the `'pptx'` arm to worker dispatch**

In `frontend-desktop/src/preview/worker/fileParser.worker.ts`, the existing dispatch is a switch on `kind`. Add the pptx case alongside xlsx. Replace the existing dispatch function body with:

```ts
async function dispatch(req: ParseRequest): Promise<unknown> {
  switch (req.kind) {
    case 'xlsx': {
      const { parseXlsx } = await import('./parsers/xlsx')
      return parseXlsx(req.buffer)
    }
    case 'pptx': {
      const { parsePptx } = await import('./parsers/pptx')
      const result = await parsePptx(req.buffer)
      // themeFonts comes back as a Map; convert to plain object for
      // structured-clone friendliness and matching ParseResultData.pptx.
      return {
        slides: result.slides,
        themeFonts: Object.fromEntries(result.themeFonts),
      }
    }
    default: {
      const _exhaustive: never = req.kind
      throw new Error(`Unsupported parse kind: ${String(_exhaustive)}`)
    }
  }
}
```

> If the existing worker file uses a slightly different name for the request object (`ParseRequestMessage` vs `ParseRequest`), preserve that — match the file's existing convention. The `case` blocks are what matter.

- [ ] **Step 2: Add a typing test in parseClient.test.ts**

Append (do not replace existing tests) the following case in `frontend-desktop/src/preview/worker/parseClient.test.ts`:

```ts
it('returns PptxResult for kind=pptx', async () => {
  const fakeWorker = createFakeWorker((msg) => {
    if (msg.type === 'parse' && msg.kind === 'pptx') {
      return {
        type: 'result',
        id: msg.id,
        kind: 'pptx',
        data: {
          slides: [],
          themeFonts: {},
        },
      }
    }
    return undefined
  })
  __setWorkerFactory(() => fakeWorker)
  const result = await parseInWorker('pptx', new ArrayBuffer(0))
  // Type-level: result is PptxResult thanks to the ParseResultData<'pptx'> generic.
  expect(result).toEqual({ slides: [], themeFonts: {} })
  // Validate the typed surface:
  expect(Array.isArray(result.slides)).toBe(true)
  expect(typeof result.themeFonts).toBe('object')
})
```

If `createFakeWorker` and `__setWorkerFactory` exist with different names in the current test file, use the file's existing helpers and adapt the assertion. The point of this test is the *generic typing* of `parseInWorker<'pptx'>` returning `PptxResult`, plus exercising the worker dispatch arm. Read the existing test file first; mimic its patterns.

- [ ] **Step 3: Verify**

```bash
cd frontend-desktop
npx tsc -b
npm test
```

Expected: tsc clean; existing 19 tests + this new one all pass (20 / 8 files).

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/src/preview/worker/fileParser.worker.ts \
        frontend-desktop/src/preview/worker/parseClient.test.ts
git commit -m "feat(pptx): wire pptx kind through worker dispatch + parseClient"
```

---

## Task 4: Port NID's `_presetGeomPaths` data table

This is a large data table mapping preset shape geom IDs to SVG `<path d="...">` strings. Verbatim copy from NID.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pptx/ported/README.md`
- Create: `frontend-desktop/src/preview/viewers/pptx/ported/presetGeomPaths.ts`

- [ ] **Step 1: Create the ported/ README**

Create `frontend-desktop/src/preview/viewers/pptx/ported/README.md` with this content:

```markdown
# `ported/` — verbatim mirror of NID renderer code

The files in this directory are 1:1 ports of selected functions and data
tables from `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts`.

**Do not "refactor" or "improve" the code here.** The whole point of this
directory is that every line was forged by real-world PPT corner cases NID
hit before us. Refactoring breaks the alignment story — debug fidelity
issues by diffing against the same function in NID's source.

When porting:

- Drop `vscode` / webview imports.
- Use the local `escapeHtml` defined in `shapeBuilder.ts` instead of NID's
  `htmlUtils.ts` import.
- `export` whatever the wrapper (`slideToHtml.ts`) needs to call.
- Preserve all comments — they document the corner cases.

| File | Mirrors NID lines | Function/Table |
|------|-------------------|----------------|
| `presetGeomPaths.ts` | 2320–2395 | `_presetGeomPaths: Record<string, string>` |
| `shapeBuilder.ts`    | 2396–2842 (+helpers) | `_buildShapeParts` + `_formatAutoNum` + `_toRoman` + `_toAlpha` + small helpers |
```

- [ ] **Step 2: Open NID source and copy the data table**

In your editor, open `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts`. Find lines **2320–2395** (the `_presetGeomPaths` declaration). Copy that block verbatim.

- [ ] **Step 3: Create the file**

Create `frontend-desktop/src/preview/viewers/pptx/ported/presetGeomPaths.ts`. Paste the copied lines, then prepend a single `export ` keyword to the `const` line. The file should start exactly like:

```ts
/**
 * Verbatim port of `_presetGeomPaths` from NID
 * `src/webview/pptxViewerPanel.ts` (lines 2320–2395).
 *
 * SVG path-data lookup table for OOXML preset shape geometries.
 * Used by _buildShapeParts to render shape outlines when the shape has
 * a known `prstGeom` and no `customSvgPath` from parsing.
 *
 * DO NOT EDIT — see ported/README.md.
 */
export const _presetGeomPaths: Record<string, string> = {
  // ... (verbatim from NID 2320–2395)
}
```

- [ ] **Step 4: Type-check**

Run (in `frontend-desktop/`): `npx tsc -b`
Expected: clean. The new file is referenced by no one yet; the project still compiles.

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/viewers/pptx/ported/README.md \
        frontend-desktop/src/preview/viewers/pptx/ported/presetGeomPaths.ts
git commit -m "feat(pptx): port NID _presetGeomPaths SVG path table"
```

---

## Task 5: Port small helpers into `shapeBuilder.ts`

`_buildShapeParts` calls a handful of small helpers. Get those into the new file first; `_buildShapeParts` itself comes in Task 6. Pasting the helpers verbatim means Task 6 only has to add a single (very long) function.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pptx/ported/shapeBuilder.ts`

- [ ] **Step 1: Scaffold the file with imports + header**

Create `frontend-desktop/src/preview/viewers/pptx/ported/shapeBuilder.ts` with this header (we'll append more in following steps):

```ts
/**
 * Verbatim port of `_buildShapeParts` + its inline helpers from NID
 * `src/webview/pptxViewerPanel.ts` (lines 2396–2842 plus a few small
 * utilities below). Do not refactor — see ported/README.md.
 */
import type {
  SlideShape, TextShape, ImageShape, TableShape, ConnectorShape,
} from '../../../worker/parsers/pptx'
import { _presetGeomPaths } from './presetGeomPaths'

// --- escapeHtml: NID imports this from htmlUtils.ts; we keep a local copy
// so ported/ stays self-contained. Matches NID's escapeHtml exactly. ---
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
```

> If on visiting `D:\20_code\NetworkIntegrationDesign\src\webview\htmlUtils.ts` you find NID's `escapeHtml` differs (e.g. uses `&apos;` for `'` or omits a substitution), match NID's version exactly.

- [ ] **Step 2: Append `_isDark` (NID lines 1901–1917)**

Open `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts`. Find lines 1901–1917 (the `_isDark` function). Copy them verbatim and append to `shapeBuilder.ts`:

```ts
// Verbatim from NID pptxViewerPanel.ts:1901–1917 — keep, do not modify.
function _isDark(hex: string): boolean {
  // ... (verbatim)
}
```

- [ ] **Step 3: Append `_resolveSymbolText` and its companions**

NID has a chain: `_wingdingsMap` / `_wingdings2Map` / `_wingdings3Map` / `_knownSymbolFonts` (data) and `_resolveSymbolChar` / `_resolveBulletChar` / `_resolveSymbolText` (functions). Copy them verbatim from NID lines **1824–1879** (the whole block — 4 data tables + 3 functions).

Append to `shapeBuilder.ts` as a single block; preserve all NID comments.

- [ ] **Step 4: Append `_formatAutoNum`, `_toRoman`, `_toAlpha`**

Copy NID lines **2396–2429** verbatim. Append to `shapeBuilder.ts`. These are the auto-numbering helpers used by `_buildShapeParts` for bulleted lists.

- [ ] **Step 5: Type-check**

Run: `npx tsc -b`
Expected: clean (helpers are exported from the same file; nothing external references them yet).

If you see TypeScript errors, they likely come from helpers using TypeScript syntax NID's tsconfig allows that ours doesn't (unlikely — they share TS strict mode). Fix only by changing what compilation requires; do not refactor logic.

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/preview/viewers/pptx/ported/shapeBuilder.ts
git commit -m "feat(pptx): port small rendering helpers (escapeHtml, _isDark, _resolveSymbolText, _formatAutoNum, _toRoman, _toAlpha)"
```

---

## Task 6: Port `_buildShapeParts` (the big function)

The core renderer function. ~410 lines. Verbatim port; the helpers it needs are already in place from Task 5 (above it in the same file).

**Files:**
- Modify: `frontend-desktop/src/preview/viewers/pptx/ported/shapeBuilder.ts` (append)

- [ ] **Step 1: Locate and copy from NID**

Open NID `D:\20_code\NetworkIntegrationDesign\src\webview\pptxViewerPanel.ts`. Find the `_buildShapeParts` function — starts at line **2430** with the signature `function _buildShapeParts(`. Find its end (the matching closing `}` at column 0). This is approximately lines 2430–2842 (~410 lines).

Also locate the `_BuildParts` type definition NID uses for the return — search for `type _BuildParts` or `interface _BuildParts`. Bring it across too. (It's likely just above `_buildShapeParts` or in NID's type section near the top.)

- [ ] **Step 2: Append to shapeBuilder.ts**

Append the `_BuildParts` type and then the entire `_buildShapeParts` body, verbatim. Add `export` to both so the wrapper can import them. Final relevant lines look like:

```ts
// Verbatim from NID pptxViewerPanel.ts.
export interface _BuildParts {
  css: string
  html: string
}

// Verbatim from NID pptxViewerPanel.ts:2430–2842.
// Do not modify — see ported/README.md.
export function _buildShapeParts(
  shape: SlideShape, slideW: number, slideH: number,
  slideIdx: number | string, shapeIdx: number,
): _BuildParts {
  // ... (verbatim ~410 lines from NID)
}
```

- [ ] **Step 3: Resolve type-check errors specifically caused by porting**

Run `npx tsc -b`. Likely first-round issues and how to handle:

| Error | Fix |
|---|---|
| `Cannot find name 'X'` where X is a helper present in NID's source | Find the helper in NID, port it the same way (verbatim) into shapeBuilder.ts |
| `Cannot find name 'escapeHtml'` | Already added in Task 5 — verify import path/spelling |
| `Cannot find name '_presetGeomPaths'` | Already imported at file top in Task 5 — verify spelling |
| Strict-mode error about `any` or implicit `any` | Add explicit `any` cast matching what NID uses; do not refactor types |
| Missing field on `TextShape`/`SlideShape` etc. | The parser type and the renderer expectations may differ — go to spike `parsers/pptx.ts` and confirm the field name; this is real data-flow info, not refactoring |

Repeat: bring missing helpers and resolve compilation. Stop refactoring at "make tsc happy." Do not attempt readability changes.

- [ ] **Step 4: Verify build**

```bash
cd frontend-desktop
npx tsc -b
npm run build
npm test
```

Expected: all clean. (`_buildShapeParts` is dead code at this point — exported but unused — so any failure is purely a porting issue.)

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/viewers/pptx/ported/shapeBuilder.ts
git commit -m "feat(pptx): port _buildShapeParts core renderer from NID"
```

---

## Task 7: `slideToHtml` wrapper + `prefixSelectors` (TDD)

Pure function that takes one `SlideData` and returns the `{ css, html }` for that slide. Mirrors NID's `_buildHtml` inner loop (master shapes + layout shapes + slide shapes — three layers stacked). Plus the `prefixSelectors` regex that scopes CSS to `.ipm-pptx-root`.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pptx/slideToHtml.ts`
- Create: `frontend-desktop/src/preview/viewers/pptx/slideToHtml.test.ts`

- [ ] **Step 1: Write the failing test for `prefixSelectors`**

Create `frontend-desktop/src/preview/viewers/pptx/slideToHtml.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { prefixSelectors } from './slideToHtml'

describe('prefixSelectors', () => {
  it('prefixes a single class selector', () => {
    expect(prefixSelectors('.sh-0-1 { color: red; }', '.ipm-pptx-root '))
      .toBe('.ipm-pptx-root .sh-0-1 { color: red; }')
  })

  it('prefixes each selector in a list', () => {
    expect(prefixSelectors('.a, .b { x:1; }', '.ipm-pptx-root '))
      .toBe('.ipm-pptx-root .a, .ipm-pptx-root .b { x:1; }')
  })

  it('handles consecutive rules separated by }', () => {
    const css = '.a { x:1; } .b { y:2; }'
    expect(prefixSelectors(css, '.ipm-pptx-root '))
      .toBe('.ipm-pptx-root .a { x:1; } .ipm-pptx-root .b { y:2; }')
  })

  it('preserves leading whitespace inside rule bodies', () => {
    const css = '.a {\n  color: red;\n}\n'
    expect(prefixSelectors(css, '.ipm-pptx-root '))
      .toBe('.ipm-pptx-root .a {\n  color: red;\n}\n')
  })
})
```

- [ ] **Step 2: Run test, expect to fail (module not found)**

```bash
cd frontend-desktop
npm test -- slideToHtml
```

Expected: FAIL — `Cannot find module './slideToHtml'`.

- [ ] **Step 3: Create `slideToHtml.ts` with `prefixSelectors` and `slideToHtml`**

Create `frontend-desktop/src/preview/viewers/pptx/slideToHtml.ts`:

```ts
/**
 * Per-slide CSS+HTML emitter. Mirrors the inner loop of NID's `_buildHtml`
 * (master shapes, then layout shapes, then slide shapes — three stacked
 * z-layers that produce the final visible slide). Differences from NID:
 *   1. We omit the webview shell (nonce/CSP/script tags) — the React host
 *      composes one outer scope.
 *   2. We rewrite emitted selectors so they are scoped to `.ipm-pptx-root`
 *      and cannot leak into the rest of the app.
 *   3. We return per-slide { css, html } so PptxViewer can concatenate them
 *      into a single <style> with N siblings.
 */
import type { SlideData } from '../../worker/parsers/pptx'
import { _buildShapeParts } from './ported/shapeBuilder'

/**
 * Prefix every selector in `css` with `prefix` (e.g. `.ipm-pptx-root `).
 *
 * NID's renderer only emits single-class selectors at rule heads, separated
 * by `}` or `,`. No `@media`, no nested combinators, no pseudo-selectors.
 * A regex over the start of each selector list is sufficient.
 *
 * Algorithm: split the CSS on `}` (with `}` re-attached to the preceding
 * chunk), then for each chunk find the selector list (text before the first
 * `{`) and prefix each comma-separated selector. Leaves rule bodies
 * untouched (so newlines / whitespace inside `{ ... }` are preserved).
 */
export function prefixSelectors(css: string, prefix: string): string {
  // Match `<selectors> {` at rule heads. The capture group is the selector list.
  return css.replace(/([^{}]+)\{/g, (_match, selectorList: string) => {
    const trimmed = selectorList.replace(/\s+$/, '')
    const trailing = selectorList.slice(trimmed.length)
    const parts = trimmed.split(',').map((s) => {
      const leading = (s.match(/^\s*/) ?? [''])[0]
      const body = s.slice(leading.length)
      return `${leading}${prefix}${body}`
    })
    return `${parts.join(',')}${trailing}{`
  })
}

/**
 * Convert one parsed slide into { css, html }. The HTML is intended to be
 * injected with React's `dangerouslySetInnerHTML` inside the slide card.
 * The CSS is meant to be concatenated with sibling slides' CSS and rendered
 * once in a top-level `<style>` element.
 *
 * Mirrors NID's `_buildHtml` per-slide loop body (with the master + layout
 * + slide layer stack). Master shapes are positioned identically on every
 * slide; we emit them per slide too (small cost; matches NID's output and
 * avoids the cross-slide selector matching complexity).
 */
export function slideToHtml(
  slide: SlideData,
  slideIdx: number,
): { css: string; html: string } {
  let css = ''
  let html = ''

  // Master shapes (NID 2867 — same idea, here per slide).
  for (let shi = 0; shi < slide.masterShapes.length; shi++) {
    const parts = _buildShapeParts(
      slide.masterShapes[shi], slide.width, slide.height, `m${slideIdx}`, shi,
    )
    css += parts.css
    html += parts.html
  }

  // Layout shapes (NID 2901).
  for (let shi = 0; shi < slide.layoutShapes.length; shi++) {
    const parts = _buildShapeParts(
      slide.layoutShapes[shi], slide.width, slide.height, `l${slideIdx}`, shi,
    )
    css += parts.css
    html += parts.html
  }

  // Slide content shapes (NID 2908).
  for (let shi = 0; shi < slide.shapes.length; shi++) {
    const parts = _buildShapeParts(
      slide.shapes[shi], slide.width, slide.height, slideIdx, shi,
    )
    css += parts.css
    html += parts.html
  }

  return {
    css: prefixSelectors(css, '.ipm-pptx-root '),
    html,
  }
}
```

- [ ] **Step 4: Run tests, expect green**

```bash
npm test -- slideToHtml
```

Expected: 4 passed.

- [ ] **Step 5: Type-check + commit**

```bash
npx tsc -b
git add frontend-desktop/src/preview/viewers/pptx/slideToHtml.ts \
        frontend-desktop/src/preview/viewers/pptx/slideToHtml.test.ts
git commit -m "feat(pptx): slideToHtml wrapper + prefixSelectors with TDD"
```

---

## Task 8: `extractTitle` (TDD)

Pure function for TOC labels.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pptx/extractTitle.ts`
- Create: `frontend-desktop/src/preview/viewers/pptx/extractTitle.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend-desktop/src/preview/viewers/pptx/extractTitle.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { extractTitle } from './extractTitle'
import type { SlideData } from '../../worker/parsers/pptx'

function makeSlide(opts: { shapes?: SlideData['shapes'] } = {}): SlideData {
  return {
    index: 0,
    width: 960,
    height: 540,
    shapes: opts.shapes ?? [],
    masterShapes: [],
    layoutShapes: [],
    suppressMasterShapes: false,
  }
}

describe('extractTitle', () => {
  it('returns the first non-empty run text of the first text shape', () => {
    const slide = makeSlide({
      shapes: [{
        type: 'text', left: 0, top: 0, width: 100, height: 50,
        paragraphs: [{
          runs: [
            { text: '  ', bold: false, italic: false, underline: false,
              strikethrough: false, fontSize: null, fontFamily: null,
              color: null, spacing: null, href: null, baseline: null,
              highlight: null },
            { text: '系统架构', bold: false, italic: false, underline: false,
              strikethrough: false, fontSize: null, fontFamily: null,
              color: null, spacing: null, href: null, baseline: null,
              highlight: null },
          ],
          align: 'l', bullet: null,
        }],
      }],
    })
    expect(extractTitle(slide)).toBe('系统架构')
  })

  it('returns empty string when there is no text shape', () => {
    const slide = makeSlide({
      shapes: [{
        type: 'image', left: 0, top: 0, width: 100, height: 50,
        dataUri: 'data:image/png;base64,xxx',
      }],
    })
    expect(extractTitle(slide)).toBe('')
  })

  it('returns empty string when all runs are whitespace', () => {
    const slide = makeSlide({
      shapes: [{
        type: 'text', left: 0, top: 0, width: 100, height: 50,
        paragraphs: [{
          runs: [{
            text: '   \n\t', bold: false, italic: false, underline: false,
            strikethrough: false, fontSize: null, fontFamily: null,
            color: null, spacing: null, href: null, baseline: null,
            highlight: null,
          }],
          align: 'l', bullet: null,
        }],
      }],
    })
    expect(extractTitle(slide)).toBe('')
  })
})
```

> If the parser's `TextRun` interface has different field names from those above, this test will fail to compile. In that case, open `frontend-desktop/src/preview/worker/parsers/pptx.ts`, copy the exact `TextRun` interface, and update the test's run literals to use the correct field names. The intent of each case (ordinary / no-text-shape / whitespace-only) does not change.

- [ ] **Step 2: Run test, expect fail (no module)**

```bash
npm test -- extractTitle
```

Expected: FAIL — `Cannot find module './extractTitle'`.

- [ ] **Step 3: Implement**

Create `frontend-desktop/src/preview/viewers/pptx/extractTitle.ts`:

```ts
import type { SlideData } from '../../worker/parsers/pptx'

const MAX_TITLE_CHARS = 50

/**
 * Returns the first non-empty run text from the first text shape on the slide,
 * trimmed to MAX_TITLE_CHARS code points (CJK-safe via Array.from). Returns
 * '' if no text shape or all runs are whitespace.
 *
 * This is a heuristic — PPT OOXML marks the title placeholder explicitly
 * (<ph type="title">), but the spike parser doesn't propagate that field
 * and NID didn't need it (NID has no TOC). When/if real decks expose mismatches,
 * upgrade the parser to surface the placeholder type and read it here.
 */
export function extractTitle(slide: SlideData): string {
  for (const shape of slide.shapes) {
    if (shape.type !== 'text') continue
    for (const paragraph of shape.paragraphs) {
      for (const run of paragraph.runs) {
        const text = run.text.trim()
        if (text) {
          const codePoints = Array.from(text)
          if (codePoints.length <= MAX_TITLE_CHARS) return text
          return codePoints.slice(0, MAX_TITLE_CHARS).join('') + '…'
        }
      }
    }
    // First text shape found but it was all whitespace — stop searching;
    // matches the "first text shape" intent of NID-style heuristics.
    return ''
  }
  return ''
}
```

- [ ] **Step 4: Run test, expect green**

```bash
npm test -- extractTitle
```

Expected: 3 passed.

- [ ] **Step 5: Type-check + commit**

```bash
npx tsc -b
git add frontend-desktop/src/preview/viewers/pptx/extractTitle.ts \
        frontend-desktop/src/preview/viewers/pptx/extractTitle.test.ts
git commit -m "feat(pptx): extractTitle heuristic for TOC labels"
```

---

## Task 9: CSS + i18n + PptxViewer component

Glue it all together: the React host, scoping CSS, and the new i18n key.

**Files:**
- Create: `frontend-desktop/src/preview/viewers/pptx/pptx.css`
- Create: `frontend-desktop/src/preview/viewers/PptxViewer.tsx`
- Modify: `frontend-desktop/src/i18n.tsx`

- [ ] **Step 1: Create `pptx.css`**

```css
/* Scope all NID-emitted shape CSS under this root so it can't leak into the
 * rest of the app. Also defines the per-slide card + zoom variable used by
 * PptxViewer to scale slides. NID's shape CSS uses `cqi` (container query
 * inline) units that require an inline-size container — provided here. */
.ipm-pdf-container,  /* unused, kept for shared cascade with PDF viewer */
.ipm-pptx-root {
  background: var(--bg2);
}

.ipm-pptx-root {
  position: absolute;
  inset: 0;
  overflow: auto;
  --pptx-zoom: 1;
  padding: 16px 0;
}

.ipm-pptx-slide {
  /* Width drives the container query inside the slide. Aspect ratio is
   * derived from per-slide CSS that NID emits (background-image padding-bottom
   * trick), so we just set the width via the zoom variable. */
  width: calc(min(100%, 1280px) * var(--pptx-zoom));
  max-width: none;
  margin: 16px auto;
  background: white;
  border: 1px solid var(--border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  position: relative;
  container-type: inline-size;
  overflow: hidden;
}
```

- [ ] **Step 2: Add i18n keys**

In `frontend-desktop/src/i18n.tsx`, add to both `zh` and `en` dictionaries (search for an existing `preview.toc` line; add `preview.slideN` near it):

For `zh` dict:

```ts
'preview.slideN': '第 {n} 页',
```

For `en` dict:

```ts
'preview.slideN': 'Slide {n}',
```

- [ ] **Step 3: Create `PptxViewer.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react'
import { fetchOrThrow, rawUrl, Loading, ErrorMsg } from './common'
import { parseInWorker } from '../worker/parseClient'
import type { PptxResult } from '../worker/protocol'
import { usePreviewToolbar } from '../toolbar/PreviewToolbarContext'
import type { TocItem } from '../toolbar/capabilities'
import { useI18n } from '@/i18n'
import { slideToHtml } from './pptx/slideToHtml'
import { extractTitle } from './pptx/extractTitle'
import './pptx/pptx.css'

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.5
const ZOOM_MAX = 4

interface RenderedSlide {
  idx: number
  html: string
}

export function PptxViewer({ path, filename }: { path: string; filename: string }) {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement>(null)
  const slideRefs = useRef<HTMLDivElement[]>([])
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PptxResult | null>(null)
  const [combinedCss, setCombinedCss] = useState<string>('')
  const [rendered, setRendered] = useState<RenderedSlide[]>([])
  const [scale, setScale] = useState(1)
  const [current, setCurrent] = useState(1)
  const [toc, setToc] = useState<TocItem[]>([])

  // Load + parse the PPTX.
  useEffect(() => {
    const ac = new AbortController()
    setError(null); setResult(null); setRendered([]); setCombinedCss(''); setCurrent(1); setToc([])
    fetchOrThrow(rawUrl(path))
      .then((r) => r.arrayBuffer())
      .then((buf) => parseInWorker('pptx', buf, { signal: ac.signal }))
      .then((res) => {
        if (ac.signal.aborted) return
        setResult(res)
        // Render all slides + assemble CSS.
        let css = ''
        const html: RenderedSlide[] = []
        for (let i = 0; i < res.slides.length; i++) {
          const out = slideToHtml(res.slides[i], i)
          css += out.css
          html.push({ idx: i, html: out.html })
        }
        setCombinedCss(css)
        setRendered(html)
        // Build TOC.
        const tocItems: TocItem[] = res.slides.map((slide, i) => {
          const title = extractTitle(slide)
          const label = title || t('preview.slideN', { n: i + 1 })
          const prefix = title ? `${i + 1}. ` : ''
          return { id: `slide-${i}`, label: `${prefix}${label}` }
        })
        setToc(tocItems)
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== 'AbortError') {
          setError(String(e))
        }
      })
    return () => ac.abort()
  }, [path, t])

  // IntersectionObserver: track which slide centre is in the viewport.
  useEffect(() => {
    const container = containerRef.current
    if (!container || rendered.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the most visible slide.
        let best = -1
        let bestRatio = 0
        for (const e of entries) {
          if (e.intersectionRatio > bestRatio) {
            bestRatio = e.intersectionRatio
            best = Number((e.target as HTMLElement).dataset.idx)
          }
        }
        if (best >= 0) setCurrent(best + 1)
      },
      {
        root: container,
        rootMargin: '-40% 0px -40% 0px',
        threshold: [0, 0.25, 0.5, 0.75, 1],
      },
    )
    for (const el of slideRefs.current) {
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [rendered])

  // Register toolbar capabilities.
  usePreviewToolbar({
    pages: {
      count: rendered.length,
      current,
      goto: (n: number) => {
        const el = slideRefs.current[n - 1]
        if (el) el.scrollIntoView({ block: 'start', behavior: 'auto' })
      },
    },
    zoom: {
      scale,
      in: () => setScale((s) => Math.min(ZOOM_MAX, s + ZOOM_STEP)),
      out: () => setScale((s) => Math.max(ZOOM_MIN, s - ZOOM_STEP)),
      fit: () => setScale(1),
      reset: () => setScale(1),
    },
    toc: toc.length > 0 ? {
      items: toc,
      goto: (id: string) => {
        const idx = Number(id.replace('slide-', ''))
        const el = slideRefs.current[idx]
        if (el) el.scrollIntoView({ block: 'start', behavior: 'auto' })
      },
    } : undefined,
    download: { url: rawUrl(path), filename },
  }, [rendered.length, current, scale, toc, path, filename])

  if (error) return <ErrorMsg msg={error} />
  if (result === null) return <Loading />

  return (
    <div
      ref={containerRef}
      className="ipm-pptx-root"
      style={{ ['--pptx-zoom' as string]: String(scale) }}
    >
      <style dangerouslySetInnerHTML={{ __html: combinedCss }} />
      {rendered.map((s) => (
        <div
          key={s.idx}
          ref={(el) => { if (el) slideRefs.current[s.idx] = el }}
          className="ipm-pptx-slide"
          data-idx={s.idx}
          dangerouslySetInnerHTML={{ __html: s.html }}
        />
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Type-check, build, test**

```bash
npx tsc -b
npm run build
npm test
```

Expected: all clean. Test counts unchanged from Task 8 (the new component has no unit test — covered by manual fidelity verification in Task 11).

- [ ] **Step 5: Commit**

```bash
git add frontend-desktop/src/preview/viewers/pptx/pptx.css \
        frontend-desktop/src/preview/viewers/PptxViewer.tsx \
        frontend-desktop/src/i18n.tsx
git commit -m "feat(pptx): PptxViewer component, scoped CSS, slideN i18n"
```

---

## Task 10: Route `'pptx'` in FilePreviewModal + bump 0.2.10

**Files:**
- Modify: `frontend-desktop/src/components/FilePreviewModal.tsx`
- Modify: `electron/package.json`

- [ ] **Step 1: Route + remove from unsupported fallback**

In `frontend-desktop/src/components/FilePreviewModal.tsx`, find the viewer-routing region (it looks like a series of conditional renders by `type`, with a fallback for unsupported types). Add a `PptxViewer` import:

```tsx
import { PptxViewer } from '@/preview/viewers/PptxViewer'
```

In the rendering body, the current state has `type === 'pdf'` already routed, and `type === 'pptx'` falling through to the unsupported message. Replace the unsupported predicate (which currently includes `'pptx'`) so that `'pptx'` routes to the new viewer. The body should look like:

```tsx
{type === 'image' && <ImageViewer path={path} filename={name} />}
{type === 'markdown' && <MarkdownViewer path={path} filename={name} />}
{type === 'docx' && <DocxViewer path={path} filename={name} />}
{type === 'excel' && <ExcelViewer path={path} filename={name} />}
{type === 'code' && <CodeViewer path={path} lang={CODE_LANGS[ext]} filename={name} />}
{type === 'text' && <TextViewer path={path} filename={name} />}
{type === 'pdf' && <PdfViewer path={path} filename={name} />}
{type === 'pptx' && <PptxViewer path={path} filename={name} />}
{type === 'binary' && (
  <div className="flex h-full items-center justify-center text-sm" style={{ color: 'var(--t3)' }}>
    {t('filePreview.unsupported', { ext: ext || t('filePreview.unknownExt') })}
  </div>
)}
```

> Match the existing file's formatting and the exact name of the unsupported region's predicate. If the current code uses something like `(type === 'pptx' || type === 'binary')`, change it to just `type === 'binary'` (since pptx is now handled above).

- [ ] **Step 2: Bump version**

In `electron/package.json`, change `"version": "0.2.9"` to `"version": "0.2.10"`. Leave `frontend-desktop/package.json`'s private `0.0.0` alone.

- [ ] **Step 3: Verify**

```bash
cd frontend-desktop
npx tsc -b
npm run build
npm test
```

Expected: all green; build emits `pptx`-related code as part of the main chunk (no separate worker chunk for pptx itself — the parser is shared in the existing `fileParser.worker` chunk).

- [ ] **Step 4: Commit**

```bash
git add frontend-desktop/src/components/FilePreviewModal.tsx \
        electron/package.json
git commit -m "feat(pptx): route .pptx to PptxViewer; bump desktop version to 0.2.10"
```

---

## Task 11: Manual fidelity verification + gap fixes

The actual proof of "align with NID." Run side-by-side comparison; any visible difference is a porting gap to investigate.

**Files (potentially):** any of the `ported/` files if a gap is found.

- [ ] **Step 1: Build a packaged build**

From repo root:

```bash
rm -rf build/electron-dist/win-unpacked
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "packaging/build_electron.ps1"
```

This runs PyInstaller + electron-builder; ~10–15 minutes. The installer ends up at `build/electron-dist/IPMaster-Cowork Setup 0.2.10.exe`.

> Reminder: the project's PyInstaller spec embeds `frontend-desktop/dist/` into the backend exe. If you skip the backend rebuild (`-SkipBackend`), the installed app will serve the OLD frontend — confirmed gotcha from PDF slice debugging.

- [ ] **Step 2: Install and open the sample**

Install the 0.2.10 .exe. In its workspace panel, open `frontend-desktop/src/preview/worker/parsers/__fixtures__/sample.pptx` (browse to the dev tree, or copy the file somewhere accessible). Step through each of the 6 slides.

- [ ] **Step 3: Compare against NID**

Open the same file in NID (VS Code with the network-designer extension). For each of the 6 slides, screenshot NID's rendering side-by-side with our rendering. Note any visible difference: missing shape outline, wrong text colour, broken table border, missing image, mis-positioned shape, etc.

- [ ] **Step 4: Investigate each gap (loop until none)**

For each visible difference, the cause is almost always one of:

1. **Missing ported helper.** A function in NID's `pptxViewerPanel.ts` that `_buildShapeParts` calls indirectly that we didn't bring across. Find it in NID, port verbatim into `ported/shapeBuilder.ts`. Re-build and re-compare.
2. **Renamed/missing field on parser output.** The parser populated a field with a different shape than the renderer expects. Read the parser's `_extract*` functions and the renderer's expected field; reconcile. Prefer changing renderer (it's ported) to match parser only if both sides actually disagree on what NID does. (Most of the time the parser is faithful.)
3. **CSS prefix interaction.** `prefixSelectors` may have a corner case that breaks a specific selector style. Add a test case in `slideToHtml.test.ts` reproducing the broken CSS, then fix the regex.
4. **Real PPT feature NID also doesn't handle.** Log as a known limitation; document in `viewers/pptx/ported/README.md`.

For each fix, commit separately with the message format `fix(pptx): port <thing>` or `fix(pptx): handle <case>`.

- [ ] **Step 5: Run the user's real-deck samples (if provided)**

If the user has supplied 1–2 real business PPTs, repeat steps 2–4 against them. They will surface different corner cases from `sample.pptx`.

- [ ] **Step 6: Document remaining differences**

After the comparison loop converges, write a short "known limitations" section in `viewers/pptx/ported/README.md` listing differences that remain (animations, embedded video, custom fonts not on user system, etc.). Commit.

```bash
git add frontend-desktop/src/preview/viewers/pptx/ported/README.md
git commit -m "docs(pptx): document known PPTX rendering limitations"
```

---

## Self-Review (against the spec)

**Spec coverage check:**

| Spec section | Implementing task(s) |
|---|---|
| §3 architecture / data flow | Tasks 1, 2, 3, 7, 9 |
| §4 module structure | Tasks 2, 4, 5, 6, 7, 8, 9 |
| §5 CSS isolation via `prefixSelectors` | Task 7 |
| §6 zoom (`--pptx-zoom`) | Tasks 9, 10 (and `pptx.css`) |
| §7 page tracking via IntersectionObserver | Task 9 |
| §8 `extractTitle` heuristic | Task 8 |
| §9 worker pipeline extension | Tasks 1, 2, 3 |
| §10 error / loading | Task 9 (PptxViewer state machine) |
| §11 testing | Tasks 3, 7, 8 (unit); Task 11 (manual fidelity) |
| §12 version 0.2.10 | Task 10 |
| §13 non-goals | Honoured by omission; documented in Task 11 README |
| §15 known risks | Task 6 explicitly handles the dependency-chain risk; Task 11 handles fidelity-gap risk |

No spec section is unimplemented.

**Placeholder scan:** Verified no TBD / "implement later" / vague descriptions. Where verbatim code from NID is required, the plan instructs the engineer to copy specific line ranges and shows the file structure context. The one exception is a small note in Task 5 acknowledging that NID's exact `escapeHtml` may differ slightly — the instruction is concrete ("match NID's version exactly").

**Type consistency:**

- `ParseKind`, `PptxResult`, `ParseResultData['pptx']` consistent across Tasks 1, 3, 9.
- `SlideData` import path is `'../../worker/parsers/pptx'` from `viewers/pptx/extractTitle.ts` and `slideToHtml.ts`; consistent.
- `_buildShapeParts`, `_BuildParts`, `_presetGeomPaths`, `escapeHtml` consistently named across Tasks 4, 5, 6, 7.
- `usePreviewToolbar(caps, deps)` signature matches PDF slice; capability shapes (`zoom`/`pages`/`toc`/`download`) match Phase 1 `ViewerCapabilities`.

**One spec → plan gap fix applied inline:** the spec says "tests should reach floor of 23 tests / 9 files" but my Task 8 only adds 3 cases. Task 7 adds 4 (prefixSelectors). 3+4 = 7 new tests → 19+7 = 26 (not 23). That's better than spec said. No fix needed.

**One ambiguity fix applied inline:** the spec didn't say how the renderer handles the cases where `extractTitle` returns empty for all slides → no TOC. Task 9's code shows `toc: toc.length > 0 ? {...} : undefined`. This means if all slides have no title, no Contents button appears. Acceptable; aligned with PDF behaviour (PDF without outline also hides the button).
