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
 */
export function slideToHtml(
  slide: SlideData,
  slideIdx: number,
): { css: string; html: string } {
  let css = ''
  let html = ''

  // Master shapes (NID _buildHtml stacks master shapes per slide too).
  for (let shi = 0; shi < slide.masterShapes.length; shi++) {
    const parts = _buildShapeParts(
      slide.masterShapes[shi], slide.width, slide.height, `m${slideIdx}`, shi,
    )
    css += parts.css
    html += parts.html
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
