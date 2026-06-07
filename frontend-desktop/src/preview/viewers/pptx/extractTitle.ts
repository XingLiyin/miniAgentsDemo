import type { SlideData, SlideShape, TextShape } from '../../worker/parsers/pptx'

const MAX_TITLE_CHARS = 50

/**
 * Returns the first non-empty run text from the slide, trimmed to
 * MAX_TITLE_CHARS code points (CJK-safe via Array.from). Returns '' only
 * when nothing in the slide/layout/master has usable text.
 *
 * Search order:
 *   1. slide.shapes — author-provided content (overrides layout).
 *   2. slide.layoutShapes — fallback when the slide author didn't override
 *      anything on this slide and just relies on the layout's placeholder.
 *   3. slide.masterShapes — fallback when the title lives in the master
 *      template (corporate decks often put the section heading there).
 *
 * Empty-text shapes are skipped within each list so a blank title placeholder
 * doesn't end the search prematurely.
 *
 * This is a heuristic — PPT OOXML marks the title placeholder explicitly
 * (<ph type="title">), but the spike parser doesn't propagate that field
 * and NID didn't need it (NID has no TOC). When/if real decks expose
 * mismatches, upgrade the parser to surface the placeholder type and read
 * it here.
 */
export function extractTitle(slide: SlideData): string {
  return extractFromShapes(slide.shapes)
      || extractFromShapes(slide.layoutShapes)
      || extractFromShapes(slide.masterShapes)
}

function extractFromShapes(shapes: SlideShape[]): string {
  // Sort text shapes by vertical position — titles are typically near the
  // top of the slide, while footers (page numbers, copyright notices) live
  // at the bottom. Without this sort, a slide whose first author-defined
  // text shape happens to be the footer would surface the footer instead
  // of the real title.
  const textShapes = shapes
    .filter((s): s is TextShape => s.type === 'text')
    .slice()
    .sort((a, b) => a.top - b.top)
  for (const shape of textShapes) {
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
    // Empty text shape — continue to the next one rather than giving up.
  }
  return ''
}
