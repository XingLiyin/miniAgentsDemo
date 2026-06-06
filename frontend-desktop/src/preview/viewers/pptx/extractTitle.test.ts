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
