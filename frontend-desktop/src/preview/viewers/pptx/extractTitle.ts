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
