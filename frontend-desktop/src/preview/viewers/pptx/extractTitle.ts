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
    // Concatenate ALL runs across ALL paragraphs of this shape, not just
    // the first non-empty run. PowerPoint splits a single visible title
    // line into multiple runs whenever there's a formatting change — and
    // a mixed-script title like "5.1 IP承载网络规划流程" is almost always
    // split because Latin and East Asian glyphs use different fonts.
    // Taking only the first run would yield "5.1 IP" and lose the (real,
    // Chinese) title text.
    let combined = ''
    for (const paragraph of shape.paragraphs) {
      for (const run of paragraph.runs) {
        combined += run.text
      }
      // Soft return between paragraphs inside the same shape — usually a
      // chapter number and the chapter name. Join with a space so the
      // words don't smash together.
      combined += ' '
    }
    const trimmed = combined.replace(/\s+/g, ' ').trim()
    if (trimmed) {
      const codePoints = Array.from(trimmed)
      if (codePoints.length <= MAX_TITLE_CHARS) return trimmed
      return codePoints.slice(0, MAX_TITLE_CHARS).join('') + '…'
    }
    // Empty text shape — continue to the next one rather than giving up.
  }
  return ''
}
