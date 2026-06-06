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
import { _buildShapeParts, _isDark } from './ported/shapeBuilder'

/**
 * Prefix every selector in `css` with `prefix` (e.g. `.ipm-pptx-root `).
 *
 * NID's renderer only emits single-class selectors at rule heads, separated
 * by `}` or `,`. No `@media`, no nested combinators, no pseudo-selectors.
 * A regex over the start of each selector list is sufficient.
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
 * Three layers of CSS/HTML are emitted, mirroring NID's `_buildHtml`:
 *   1. A per-slide rule `.sld-N { ... }` sets the `--pt` cqi factor and the
 *      slide background. Every `var(--pt, 1pt)` inside shape CSS resolves
 *      against this value so text/borders/spacing scale with slide width.
 *   2. Three calls to `_buildShapeParts` (master → layout → content)
 *      produce the absolutely-positioned shape boxes.
 *   3. PptxViewer wraps the returned `html` in `<div class="sld-N">
 *      <div class="slide-inner">…</div></div>` so the absolute shapes
 *      position relative to .slide-inner (NID structure).
 */
export function slideToHtml(
  slide: SlideData,
  slideIdx: number,
): { css: string; html: string } {
  // --pt: pt → cqi factor. 1pt = 4/3px at 96 DPI; 1cqi = 1% of container
  // width; Xpt at reference width = X * 400 / (3 * slideWidthPx) cqi.
  const ptFactor = (400 / (3 * slide.width)).toFixed(5)
  let slideRule = `.sld-${slideIdx}{--pt:${ptFactor}cqi;`
  if (slide.bgImage) {
    slideRule += `background-image:url(${slide.bgImage});background-size:cover;background-position:center;`
  } else if (slide.bgColor) {
    if (slide.bgColor.includes('gradient')) {
      slideRule += `background:${slide.bgColor};`
    } else {
      slideRule += `background:#${slide.bgColor};`
      if (_isDark(slide.bgColor)) slideRule += `color:#eee;`
    }
  }
  slideRule += `}\n`

  let css = slideRule
  let html = ''

  // Master shapes (NID _buildHtml stacks master shapes per slide too).
  if (!slide.suppressMasterShapes) {
    for (let shi = 0; shi < slide.masterShapes.length; shi++) {
      const parts = _buildShapeParts(
        slide.masterShapes[shi], slide.width, slide.height, `m${slideIdx}`, shi,
      )
      css += parts.css
      html += parts.html
    }
  }

  // Layout shapes.
  for (let shi = 0; shi < slide.layoutShapes.length; shi++) {
    const parts = _buildShapeParts(
      slide.layoutShapes[shi], slide.width, slide.height, `l${slideIdx}`, shi,
    )
    css += parts.css
    html += parts.html
  }

  // Slide content shapes.
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
