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
