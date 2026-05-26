//#region node_modules/refractor/lang/toml.js
/**
* @import {Refractor} from '../lib/core.js'
*/
toml.displayName = "toml";
toml.aliases = [];
/** @param {Refractor} Prism */
function toml(Prism) {
	(function(Prism) {
		var key = /(?:[\w-]+|'[^'\n\r]*'|"(?:\\.|[^\\"\r\n])*")/.source;
		/**
		* @param {string} pattern
		*/
		function insertKey(pattern) {
			return pattern.replace(/__/g, function() {
				return key;
			});
		}
		Prism.languages.toml = {
			comment: {
				pattern: /#.*/,
				greedy: true
			},
			table: {
				pattern: RegExp(insertKey(/(^[\t ]*\[\s*(?:\[\s*)?)__(?:\s*\.\s*__)*(?=\s*\])/.source), "m"),
				lookbehind: true,
				greedy: true,
				alias: "class-name"
			},
			key: {
				pattern: RegExp(insertKey(/(^[\t ]*|[{,]\s*)__(?:\s*\.\s*__)*(?=\s*=)/.source), "m"),
				lookbehind: true,
				greedy: true,
				alias: "property"
			},
			string: {
				pattern: /"""(?:\\[\s\S]|[^\\])*?"""|'''[\s\S]*?'''|'[^'\n\r]*'|"(?:\\.|[^\\"\r\n])*"/,
				greedy: true
			},
			date: [{
				pattern: /\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?\b/i,
				alias: "number"
			}, {
				pattern: /\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b/,
				alias: "number"
			}],
			number: /(?:\b0(?:x[\da-zA-Z]+(?:_[\da-zA-Z]+)*|o[0-7]+(?:_[0-7]+)*|b[10]+(?:_[10]+)*))\b|[-+]?\b\d+(?:_\d+)*(?:\.\d+(?:_\d+)*)?(?:[eE][+-]?\d+(?:_\d+)*)?\b|[-+]?\b(?:inf|nan)\b/,
			boolean: /\b(?:false|true)\b/,
			punctuation: /[.,=[\]{}]/
		};
	})(Prism);
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/languages/prism/toml.js
var toml_default = toml;
//#endregion
export { toml_default as default };

//# sourceMappingURL=react-syntax-highlighter_dist_esm_languages_prism_toml.js.map