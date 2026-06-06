/**
 * Verbatim port of `_buildShapeParts` + its inline helpers from NID
 * `src/webview/pptxViewerPanel.ts` (lines 2396–2842 plus a few small
 * utilities below). Do not refactor — see ported/README.md.
 */
// Type imports used by _buildShapeParts (Task 6). Re-exported here so
// noUnusedLocals does not fire before Task 6 lands.
export type {
  SlideShape, TextShape, ImageShape, TableShape, ConnectorShape,
} from '../../../worker/parsers/pptx'
import { _presetGeomPaths } from './presetGeomPaths'

// --- escapeHtml: NID imports this from htmlUtils.ts; we keep a local copy
// so ported/ stays self-contained. ---
// NOTE: NID's htmlUtils.ts escapeHtml does NOT include a `'` → `&#39;` substitution.
// The task scaffold included it, but the verbatim port matches NID exactly (4 replacements only).
export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Returns true if 6-digit hex color has perceived luminance < 0.5 (dark background) */
// Verbatim from NID pptxViewerPanel.ts:1901–1906 — keep, do not modify.
export function _isDark(hex: string): boolean {
  const r = parseInt(hex.slice(0, 2), 16) / 255;
  const g = parseInt(hex.slice(2, 4), 16) / 255;
  const b = parseInt(hex.slice(4, 6), 16) / 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5;
}

/**
 * Resolve a bullet character from a (possibly symbol) font to a displayable
 * Unicode character.  Many templates store bullets as ASCII letters in
 * Wingdings / Wingdings 2 / Symbol fonts where the glyphs don't match the
 * Unicode codepoints of those letters.
 */
// Verbatim from NID pptxViewerPanel.ts:1824–1879 — wingdings/symbol-font
// character mapping used by bullet rendering. Keep verbatim.
/** Wingdings code→Unicode mapping (extended) */
const _wingdingsMap: Record<number, string> = {
  0x6C: '●', 0x6E: '◆', 0x6F: '❖', 0x73: '★',
  0x4F: '○', 0x76: '✔', 0x78: '✖', 0xFC: '✓', 0xFB: '✗',
  0xE0: '⬛', 0xA8: '◉', 0xA1: '✈', 0xAC: '♠', 0xAB: '♣',
  0xAD: '♥', 0xAE: '♦', 0x22: '✂', 0x26: '☛', 0x28: '☞',
  0x2A: '☺', 0x2B: '☻', 0x2C: '☹', 0x46: '👍', 0x48: '👎',
  0x57: '⛏', 0x74: '◼', 0x75: '◻', 0x77: '⬥', 0x7D: '⌂',
  0x21: '✏', 0x23: '✇', 0x25: '☜', 0x27: '☝', 0x29: '☠',
  0x31: '☐', 0x32: '☑', 0x33: '☒',
};

const _wingdings2Map: Record<number, string> = {
  0x75: '◆', 0x76: '◇', 0x77: '●', 0x78: '○',
  0x52: '■', 0x53: '□', 0x71: '▶', 0x72: '▷',
  0x56: '✔', 0x57: '✘', 0x50: '☐', 0x51: '☑',
};

const _wingdings3Map: Record<number, string> = {
  0x77: '►', 0x78: '◄', 0x75: '▲', 0x76: '▼',
  0x7D: '⊳', 0x7E: '⊲', 0x7B: '△', 0x7C: '▽',
};

const _knownSymbolFonts = ['symbol', 'marlett', 'webdings',
  'wingdings', 'wingdings 2', 'wingdings 3'];

function _resolveSymbolChar(char: string, font?: string | null): string {
  if (!char) { return ''; }
  const f = (font ?? '').toLowerCase().trim();
  const code = char.charCodeAt(0);
  if (f === 'wingdings 2' && _wingdings2Map[code]) { return _wingdings2Map[code]; }
  if (f === 'wingdings' && _wingdingsMap[code]) { return _wingdingsMap[code]; }
  if (f === 'wingdings 3' && _wingdings3Map[code]) { return _wingdings3Map[code]; }
  if (_knownSymbolFonts.includes(f) && code < 128) { return '•'; }
  return char;
}

function _resolveBulletChar(char: string, font?: string | null): string {
  return _resolveSymbolChar(char, font);
}

/**
 * Resolve an entire text string through symbol font mapping.
 * If the font is a known symbol font, each character is individually mapped.
 */
export function _resolveSymbolText(text: string, fontFamily?: string | null): string {
  if (!text || !fontFamily) { return text; }
  const f = fontFamily.replace(/"/g, '').split(',')[0].toLowerCase().trim();
  if (!_knownSymbolFonts.includes(f)) { return text; }
  let result = '';
  for (let i = 0; i < text.length; i++) {
    result += _resolveSymbolChar(text[i], f);
  }
  return result;
}

/** Format an auto-numbered bullet value based on OOXML buAutoNum type */
// Verbatim from NID pptxViewerPanel.ts:2396–2429 — auto-numbering helpers
// used by _buildShapeParts for bulleted lists. Keep verbatim.
export function _formatAutoNum(type: string, n: number): string {
  switch (type) {
    case 'arabicPeriod': return `${n}.`;
    case 'arabicParenR': return `${n})`;
    case 'arabicParenBoth': return `(${n})`;
    case 'arabicPlain': return `${n}`;
    case 'romanUcPeriod': return `${_toRoman(n)}.`;
    case 'romanLcPeriod': return `${_toRoman(n).toLowerCase()}.`;
    case 'alphaUcPeriod': return `${_toAlpha(n)}.`;
    case 'alphaLcPeriod': return `${_toAlpha(n).toLowerCase()}.`;
    case 'alphaUcParenR': return `${_toAlpha(n)})`;
    case 'alphaLcParenR': return `${_toAlpha(n).toLowerCase()})`;
    case 'alphaUcParenBoth': return `(${_toAlpha(n)})`;
    case 'alphaLcParenBoth': return `(${_toAlpha(n).toLowerCase()})`;
    default: return `${n}.`;
  }
}
function _toRoman(n: number): string {
  const vals = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1];
  const syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I'];
  let result = '';
  for (let i = 0; i < vals.length; i++) {
    while (n >= vals[i]) { result += syms[i]; n -= vals[i]; }
  }
  return result;
}
function _toAlpha(n: number): string {
  let result = '';
  while (n > 0) { n--; result = String.fromCharCode(65 + (n % 26)) + result; n = Math.floor(n / 26); }
  return result;
}

// Task 6 will append _buildShapeParts here.
// The following re-exports keep _presetGeomPaths and the internal helpers
// accessible to _buildShapeParts (same file) and suppress noUnusedLocals on
// the import. _toRoman / _toAlpha / _resolveSymbolChar / _resolveBulletChar /
// _wingdingsMap / _wingdings2Map / _wingdings3Map / _knownSymbolFonts are all
// consumed within this file.
export { _presetGeomPaths }

// Explicitly reference internal-only helpers so noUnusedLocals doesn't fire
// before Task 6 adds _buildShapeParts. Remove these lines when Task 6 lands.
void _resolveBulletChar
void _toRoman
void _toAlpha
void _wingdingsMap
void _wingdings2Map
void _wingdings3Map
void _knownSymbolFonts
