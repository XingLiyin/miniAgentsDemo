import { t as clike } from "./clike-CSktrxtf.js";
//#region node_modules/refractor/lang/go.js
/**
* @import {Refractor} from '../lib/core.js'
*/
go.displayName = "go";
go.aliases = [];
/** @param {Refractor} Prism */
function go(Prism) {
	Prism.register(clike);
	Prism.languages.go = Prism.languages.extend("clike", {
		string: {
			pattern: /(^|[^\\])"(?:\\.|[^"\\\r\n])*"|`[^`]*`/,
			lookbehind: true,
			greedy: true
		},
		keyword: /\b(?:break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go(?:to)?|if|import|interface|map|package|range|return|select|struct|switch|type|var)\b/,
		boolean: /\b(?:_|false|iota|nil|true)\b/,
		number: [
			/\b0(?:b[01_]+|o[0-7_]+)i?\b/i,
			/\b0x(?:[a-f\d_]+(?:\.[a-f\d_]*)?|\.[a-f\d_]+)(?:p[+-]?\d+(?:_\d+)*)?i?(?!\w)/i,
			/(?:\b\d[\d_]*(?:\.[\d_]*)?|\B\.\d[\d_]*)(?:e[+-]?[\d_]+)?i?(?!\w)/i
		],
		operator: /[*\/%^!=]=?|\+[=+]?|-[=-]?|\|[=|]?|&(?:=|&|\^=?)?|>(?:>=?|=)?|<(?:<=?|=|-)?|:=|\.\.\./,
		builtin: /\b(?:append|bool|byte|cap|close|complex|complex(?:64|128)|copy|delete|error|float(?:32|64)|u?int(?:8|16|32|64)?|imag|len|make|new|panic|print(?:ln)?|real|recover|rune|string|uintptr)\b/
	});
	Prism.languages.insertBefore("go", "string", { char: {
		pattern: /'(?:\\.|[^'\\\r\n]){0,10}'/,
		greedy: true
	} });
	delete Prism.languages.go["class-name"];
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/languages/prism/go.js
var go_default = go;
//#endregion
export { go_default as default };

//# sourceMappingURL=react-syntax-highlighter_dist_esm_languages_prism_go.js.map