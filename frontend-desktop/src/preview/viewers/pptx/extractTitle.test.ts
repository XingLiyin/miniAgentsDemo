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

  it('returns empty string when all text shapes are whitespace-only', () => {
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

  it('continues past an empty title placeholder to find the next shape with text', () => {
    const slide = makeSlide({
      shapes: [
        // Empty title-placeholder shape (very common — laid out from master,
        // not overridden by deck author).
        {
          type: 'text', left: 0, top: 0, width: 100, height: 20,
          paragraphs: [{
            runs: [{
              text: '', bold: false, italic: false, underline: false,
              strikethrough: false, fontSize: null, fontFamily: null,
              color: null, spacing: null, href: null, baseline: null,
              highlight: null,
            }],
            align: 'l', bullet: null,
          }],
        },
        // Body / content text where the visible heading actually lives.
        {
          type: 'text', left: 0, top: 30, width: 100, height: 50,
          paragraphs: [{
            runs: [{
              text: '业务架构', bold: false, italic: false, underline: false,
              strikethrough: false, fontSize: null, fontFamily: null,
              color: null, spacing: null, href: null, baseline: null,
              highlight: null,
            }],
            align: 'l', bullet: null,
          }],
        },
      ],
    })
    expect(extractTitle(slide)).toBe('业务架构')
  })

  it('falls back to layoutShapes when the slide itself has no text content', () => {
    const layoutTitle: SlideData['layoutShapes'] = [{
      type: 'text', left: 0, top: 0, width: 100, height: 20,
      paragraphs: [{
        runs: [{
          text: '7.2 IP地址规划', bold: false, italic: false, underline: false,
          strikethrough: false, fontSize: null, fontFamily: null,
          color: null, spacing: null, href: null, baseline: null,
          highlight: null,
        }],
        align: 'l', bullet: null,
      }],
    }]
    const slide: SlideData = {
      index: 0,
      width: 960,
      height: 540,
      shapes: [],          // author didn't override anything on this slide
      masterShapes: [],
      layoutShapes: layoutTitle,
      suppressMasterShapes: false,
    }
    expect(extractTitle(slide)).toBe('7.2 IP地址规划')
  })
})
