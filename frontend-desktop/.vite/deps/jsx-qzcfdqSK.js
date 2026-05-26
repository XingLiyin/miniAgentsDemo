import { t as javascript } from "./javascript-CHuA3idV.js";
//#region node_modules/refractor/lang/markup.js
/**
* @import {Refractor} from '../lib/core.js'
*/
markup.displayName = "markup";
markup.aliases = [
	"atom",
	"html",
	"mathml",
	"rss",
	"ssml",
	"svg",
	"xml"
];
/** @param {Refractor} Prism */
function markup(Prism) {
	Prism.languages.markup = {
		comment: {
			pattern: /<!--(?:(?!<!--)[\s\S])*?-->/,
			greedy: true
		},
		prolog: {
			pattern: /<\?[\s\S]+?\?>/,
			greedy: true
		},
		doctype: {
			pattern: /<!DOCTYPE(?:[^>"'[\]]|"[^"]*"|'[^']*')+(?:\[(?:[^<"'\]]|"[^"]*"|'[^']*'|<(?!!--)|<!--(?:[^-]|-(?!->))*-->)*\]\s*)?>/i,
			greedy: true,
			inside: {
				"internal-subset": {
					pattern: /(^[^\[]*\[)[\s\S]+(?=\]>$)/,
					lookbehind: true,
					greedy: true,
					inside: null
				},
				string: {
					pattern: /"[^"]*"|'[^']*'/,
					greedy: true
				},
				punctuation: /^<!|>$|[[\]]/,
				"doctype-tag": /^DOCTYPE/i,
				name: /[^\s<>'"]+/
			}
		},
		cdata: {
			pattern: /<!\[CDATA\[[\s\S]*?\]\]>/i,
			greedy: true
		},
		tag: {
			pattern: /<\/?(?!\d)[^\s>\/=$<%]+(?:\s(?:\s*[^\s>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s'">=]+(?=[\s>]))|(?=[\s/>])))+)?\s*\/?>/,
			greedy: true,
			inside: {
				tag: {
					pattern: /^<\/?[^\s>\/]+/,
					inside: {
						punctuation: /^<\/?/,
						namespace: /^[^\s>\/:]+:/
					}
				},
				"special-attr": [],
				"attr-value": {
					pattern: /=\s*(?:"[^"]*"|'[^']*'|[^\s'">=]+)/,
					inside: { punctuation: [{
						pattern: /^=/,
						alias: "attr-equals"
					}, {
						pattern: /^(\s*)["']|["']$/,
						lookbehind: true
					}] }
				},
				punctuation: /\/?>/,
				"attr-name": {
					pattern: /[^\s>\/]+/,
					inside: { namespace: /^[^\s>\/:]+:/ }
				}
			}
		},
		entity: [{
			pattern: /&[\da-z]{1,8};/i,
			alias: "named-entity"
		}, /&#x?[\da-f]{1,8};/i]
	};
	Prism.languages.markup["tag"].inside["attr-value"].inside["entity"] = Prism.languages.markup["entity"];
	Prism.languages.markup["doctype"].inside["internal-subset"].inside = Prism.languages.markup;
	Prism.hooks.add("wrap", function(env) {
		if (env.type === "entity") env.attributes["title"] = env.content.value.replace(/&amp;/, "&");
	});
	Object.defineProperty(Prism.languages.markup.tag, "addInlined", { 
	/**
	* Adds an inlined language to markup.
	*
	* An example of an inlined language is CSS with `<style>` tags.
	*
	* @param {string} tagName The name of the tag that contains the inlined language. This name will be treated as
	* case insensitive.
	* @param {string} lang The language key.
	* @example
	* addInlined('style', 'css');
	*/
value: function addInlined(tagName, lang) {
		var includedCdataInside = {};
		includedCdataInside["language-" + lang] = {
			pattern: /(^<!\[CDATA\[)[\s\S]+?(?=\]\]>$)/i,
			lookbehind: true,
			inside: Prism.languages[lang]
		};
		includedCdataInside["cdata"] = /^<!\[CDATA\[|\]\]>$/i;
		var inside = { "included-cdata": {
			pattern: /<!\[CDATA\[[\s\S]*?\]\]>/i,
			inside: includedCdataInside
		} };
		inside["language-" + lang] = {
			pattern: /[\s\S]+/,
			inside: Prism.languages[lang]
		};
		var def = {};
		def[tagName] = {
			pattern: RegExp(/(<__[^>]*>)(?:<!\[CDATA\[(?:[^\]]|\](?!\]>))*\]\]>|(?!<!\[CDATA\[)[\s\S])*?(?=<\/__>)/.source.replace(/__/g, function() {
				return tagName;
			}), "i"),
			lookbehind: true,
			greedy: true,
			inside
		};
		Prism.languages.insertBefore("markup", "cdata", def);
	} });
	Object.defineProperty(Prism.languages.markup.tag, "addAttribute", { 
	/**
	* Adds an pattern to highlight languages embedded in HTML attributes.
	*
	* An example of an inlined language is CSS with `style` attributes.
	*
	* @param {string} attrName The name of the tag that contains the inlined language. This name will be treated as
	* case insensitive.
	* @param {string} lang The language key.
	* @example
	* addAttribute('style', 'css');
	*/
value: function(attrName, lang) {
		Prism.languages.markup.tag.inside["special-attr"].push({
			pattern: RegExp(/(^|["'\s])/.source + "(?:" + attrName + ")" + /\s*=\s*(?:"[^"]*"|'[^']*'|[^\s'">=]+(?=[\s>]))/.source, "i"),
			lookbehind: true,
			inside: {
				"attr-name": /^[^\s=]+/,
				"attr-value": {
					pattern: /=[\s\S]+/,
					inside: {
						value: {
							pattern: /(^=\s*(["']|(?!["'])))\S[\s\S]*(?=\2$)/,
							lookbehind: true,
							alias: [lang, "language-" + lang],
							inside: Prism.languages[lang]
						},
						punctuation: [{
							pattern: /^=/,
							alias: "attr-equals"
						}, /"|'/]
					}
				}
			}
		});
	} });
	Prism.languages.html = Prism.languages.markup;
	Prism.languages.mathml = Prism.languages.markup;
	Prism.languages.svg = Prism.languages.markup;
	Prism.languages.xml = Prism.languages.extend("markup", {});
	Prism.languages.ssml = Prism.languages.xml;
	Prism.languages.atom = Prism.languages.xml;
	Prism.languages.rss = Prism.languages.xml;
}
//#endregion
//#region node_modules/refractor/lang/jsx.js
/**
* @import {Refractor} from '../lib/core.js'
*/
jsx.displayName = "jsx";
jsx.aliases = [];
/** @param {Refractor} Prism */
function jsx(Prism) {
	Prism.register(javascript);
	Prism.register(markup);
	(function(Prism) {
		var javascript = Prism.util.clone(Prism.languages.javascript);
		var space = /(?:\s|\/\/.*(?!.)|\/\*(?:[^*]|\*(?!\/))\*\/)/.source;
		var braces = /(?:\{(?:\{(?:\{[^{}]*\}|[^{}])*\}|[^{}])*\})/.source;
		var spread = /(?:\{<S>*\.{3}(?:[^{}]|<BRACES>)*\})/.source;
		/**
		* @param {string} source
		* @param {string} [flags]
		*/
		function re(source, flags) {
			source = source.replace(/<S>/g, function() {
				return space;
			}).replace(/<BRACES>/g, function() {
				return braces;
			}).replace(/<SPREAD>/g, function() {
				return spread;
			});
			return RegExp(source, flags);
		}
		spread = re(spread).source;
		Prism.languages.jsx = Prism.languages.extend("markup", javascript);
		Prism.languages.jsx.tag.pattern = re(/<\/?(?:[\w.:-]+(?:<S>+(?:[\w.:$-]+(?:=(?:"(?:\\[\s\S]|[^\\"])*"|'(?:\\[\s\S]|[^\\'])*'|[^\s{'"/>=]+|<BRACES>))?|<SPREAD>))*<S>*\/?)?>/.source);
		Prism.languages.jsx.tag.inside["tag"].pattern = /^<\/?[^\s>\/]*/;
		Prism.languages.jsx.tag.inside["attr-value"].pattern = /=(?!\{)(?:"(?:\\[\s\S]|[^\\"])*"|'(?:\\[\s\S]|[^\\'])*'|[^\s'">]+)/;
		Prism.languages.jsx.tag.inside["tag"].inside["class-name"] = /^[A-Z]\w*(?:\.[A-Z]\w*)*$/;
		Prism.languages.jsx.tag.inside["comment"] = javascript["comment"];
		Prism.languages.insertBefore("inside", "attr-name", { spread: {
			pattern: re(/<SPREAD>/.source),
			inside: Prism.languages.jsx
		} }, Prism.languages.jsx.tag);
		Prism.languages.insertBefore("inside", "special-attr", { script: {
			pattern: re(/=<BRACES>/.source),
			alias: "language-javascript",
			inside: {
				"script-punctuation": {
					pattern: /^=(?=\{)/,
					alias: "punctuation"
				},
				rest: Prism.languages.jsx
			}
		} }, Prism.languages.jsx.tag);
		var stringifyToken = function(token) {
			if (!token) return "";
			if (typeof token === "string") return token;
			if (typeof token.content === "string") return token.content;
			return token.content.map(stringifyToken).join("");
		};
		var walkTokens = function(tokens) {
			var openedTags = [];
			for (var i = 0; i < tokens.length; i++) {
				var token = tokens[i];
				var notTagNorBrace = false;
				if (typeof token !== "string") if (token.type === "tag" && token.content[0] && token.content[0].type === "tag") if (token.content[0].content[0].content === "</") {
					if (openedTags.length > 0 && openedTags[openedTags.length - 1].tagName === stringifyToken(token.content[0].content[1])) openedTags.pop();
				} else if (token.content[token.content.length - 1].content === "/>") {} else openedTags.push({
					tagName: stringifyToken(token.content[0].content[1]),
					openedBraces: 0
				});
				else if (openedTags.length > 0 && token.type === "punctuation" && token.content === "{") openedTags[openedTags.length - 1].openedBraces++;
				else if (openedTags.length > 0 && openedTags[openedTags.length - 1].openedBraces > 0 && token.type === "punctuation" && token.content === "}") openedTags[openedTags.length - 1].openedBraces--;
				else notTagNorBrace = true;
				if (notTagNorBrace || typeof token === "string") {
					if (openedTags.length > 0 && openedTags[openedTags.length - 1].openedBraces === 0) {
						var plainText = stringifyToken(token);
						if (i < tokens.length - 1 && (typeof tokens[i + 1] === "string" || tokens[i + 1].type === "plain-text")) {
							plainText += stringifyToken(tokens[i + 1]);
							tokens.splice(i + 1, 1);
						}
						if (i > 0 && (typeof tokens[i - 1] === "string" || tokens[i - 1].type === "plain-text")) {
							plainText = stringifyToken(tokens[i - 1]) + plainText;
							tokens.splice(i - 1, 1);
							i--;
						}
						tokens[i] = new Prism.Token("plain-text", plainText, null, plainText);
					}
				}
				if (token.content && typeof token.content !== "string") walkTokens(token.content);
			}
		};
		Prism.hooks.add("after-tokenize", function(env) {
			if (env.language !== "jsx" && env.language !== "tsx") return;
			walkTokens(env.tokens);
		});
	})(Prism);
}
//#endregion
export { jsx as t };

//# sourceMappingURL=jsx-qzcfdqSK.js.map