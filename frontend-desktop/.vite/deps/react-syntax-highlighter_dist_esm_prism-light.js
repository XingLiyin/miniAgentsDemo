import { o as __toESM } from "./chunk-Cf1989ZW.js";
import { a as find, c as parse$1, i as svg, r as html, s as normalize, t as parse, u as decodeNamedCharacterReference } from "./space-separated-tokens-DgEtoQgH.js";
import { t as require_react } from "./react.js";
//#region node_modules/@babel/runtime/helpers/esm/objectWithoutPropertiesLoose.js
function _objectWithoutPropertiesLoose(r, e) {
	if (null == r) return {};
	var t = {};
	for (var n in r) if ({}.hasOwnProperty.call(r, n)) {
		if (-1 !== e.indexOf(n)) continue;
		t[n] = r[n];
	}
	return t;
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/objectWithoutProperties.js
function _objectWithoutProperties(e, t) {
	if (null == e) return {};
	var o, r, i = _objectWithoutPropertiesLoose(e, t);
	if (Object.getOwnPropertySymbols) {
		var n = Object.getOwnPropertySymbols(e);
		for (r = 0; r < n.length; r++) o = n[r], -1 === t.indexOf(o) && {}.propertyIsEnumerable.call(e, o) && (i[o] = e[o]);
	}
	return i;
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/arrayLikeToArray.js
function _arrayLikeToArray(r, a) {
	(null == a || a > r.length) && (a = r.length);
	for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e];
	return n;
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/arrayWithoutHoles.js
function _arrayWithoutHoles(r) {
	if (Array.isArray(r)) return _arrayLikeToArray(r);
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/iterableToArray.js
function _iterableToArray(r) {
	if ("undefined" != typeof Symbol && null != r[Symbol.iterator] || null != r["@@iterator"]) return Array.from(r);
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/unsupportedIterableToArray.js
function _unsupportedIterableToArray(r, a) {
	if (r) {
		if ("string" == typeof r) return _arrayLikeToArray(r, a);
		var t = {}.toString.call(r).slice(8, -1);
		return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0;
	}
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/nonIterableSpread.js
function _nonIterableSpread() {
	throw new TypeError("Invalid attempt to spread non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/toConsumableArray.js
function _toConsumableArray(r) {
	return _arrayWithoutHoles(r) || _iterableToArray(r) || _unsupportedIterableToArray(r) || _nonIterableSpread();
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/typeof.js
function _typeof(o) {
	"@babel/helpers - typeof";
	return _typeof = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function(o) {
		return typeof o;
	} : function(o) {
		return o && "function" == typeof Symbol && o.constructor === Symbol && o !== Symbol.prototype ? "symbol" : typeof o;
	}, _typeof(o);
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/toPrimitive.js
function toPrimitive(t, r) {
	if ("object" != _typeof(t) || !t) return t;
	var e = t[Symbol.toPrimitive];
	if (void 0 !== e) {
		var i = e.call(t, r || "default");
		if ("object" != _typeof(i)) return i;
		throw new TypeError("@@toPrimitive must return a primitive value.");
	}
	return ("string" === r ? String : Number)(t);
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/toPropertyKey.js
function toPropertyKey(t) {
	var i = toPrimitive(t, "string");
	return "symbol" == _typeof(i) ? i : i + "";
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/defineProperty.js
function _defineProperty(e, r, t) {
	return (r = toPropertyKey(r)) in e ? Object.defineProperty(e, r, {
		value: t,
		enumerable: !0,
		configurable: !0,
		writable: !0
	}) : e[r] = t, e;
}
//#endregion
//#region node_modules/@babel/runtime/helpers/esm/extends.js
var import_react = /* @__PURE__ */ __toESM(require_react());
function _extends() {
	return _extends = Object.assign ? Object.assign.bind() : function(n) {
		for (var e = 1; e < arguments.length; e++) {
			var t = arguments[e];
			for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]);
		}
		return n;
	}, _extends.apply(null, arguments);
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/create-element.js
function ownKeys$1(e, r) {
	var t = Object.keys(e);
	if (Object.getOwnPropertySymbols) {
		var o = Object.getOwnPropertySymbols(e);
		r && (o = o.filter(function(r) {
			return Object.getOwnPropertyDescriptor(e, r).enumerable;
		})), t.push.apply(t, o);
	}
	return t;
}
function _objectSpread$1(e) {
	for (var r = 1; r < arguments.length; r++) {
		var t = null != arguments[r] ? arguments[r] : {};
		r % 2 ? ownKeys$1(Object(t), !0).forEach(function(r) {
			_defineProperty(e, r, t[r]);
		}) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys$1(Object(t)).forEach(function(r) {
			Object.defineProperty(e, r, Object.getOwnPropertyDescriptor(t, r));
		});
	}
	return e;
}
function powerSetPermutations(arr) {
	var arrLength = arr.length;
	if (arrLength === 0 || arrLength === 1) return arr;
	if (arrLength === 2) return [
		arr[0],
		arr[1],
		"".concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[1], ".").concat(arr[0])
	];
	if (arrLength === 3) return [
		arr[0],
		arr[1],
		arr[2],
		"".concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[0]),
		"".concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[1], ".").concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[2], ".").concat(arr[1], ".").concat(arr[0])
	];
	if (arrLength >= 4) return [
		arr[0],
		arr[1],
		arr[2],
		arr[3],
		"".concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[3]),
		"".concat(arr[1], ".").concat(arr[0]),
		"".concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[3]),
		"".concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[2], ".").concat(arr[3]),
		"".concat(arr[3], ".").concat(arr[0]),
		"".concat(arr[3], ".").concat(arr[1]),
		"".concat(arr[3], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[1], ".").concat(arr[3]),
		"".concat(arr[0], ".").concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[2], ".").concat(arr[3]),
		"".concat(arr[0], ".").concat(arr[3], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[3], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[0], ".").concat(arr[3]),
		"".concat(arr[1], ".").concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[1], ".").concat(arr[2], ".").concat(arr[3]),
		"".concat(arr[1], ".").concat(arr[3], ".").concat(arr[0]),
		"".concat(arr[1], ".").concat(arr[3], ".").concat(arr[2]),
		"".concat(arr[2], ".").concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[2], ".").concat(arr[0], ".").concat(arr[3]),
		"".concat(arr[2], ".").concat(arr[1], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[1], ".").concat(arr[3]),
		"".concat(arr[2], ".").concat(arr[3], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[3], ".").concat(arr[1]),
		"".concat(arr[3], ".").concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[3], ".").concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[3], ".").concat(arr[1], ".").concat(arr[0]),
		"".concat(arr[3], ".").concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[3], ".").concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[3], ".").concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[1], ".").concat(arr[2], ".").concat(arr[3]),
		"".concat(arr[0], ".").concat(arr[1], ".").concat(arr[3], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[2], ".").concat(arr[1], ".").concat(arr[3]),
		"".concat(arr[0], ".").concat(arr[2], ".").concat(arr[3], ".").concat(arr[1]),
		"".concat(arr[0], ".").concat(arr[3], ".").concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[0], ".").concat(arr[3], ".").concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[1], ".").concat(arr[0], ".").concat(arr[2], ".").concat(arr[3]),
		"".concat(arr[1], ".").concat(arr[0], ".").concat(arr[3], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[2], ".").concat(arr[0], ".").concat(arr[3]),
		"".concat(arr[1], ".").concat(arr[2], ".").concat(arr[3], ".").concat(arr[0]),
		"".concat(arr[1], ".").concat(arr[3], ".").concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[1], ".").concat(arr[3], ".").concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[0], ".").concat(arr[1], ".").concat(arr[3]),
		"".concat(arr[2], ".").concat(arr[0], ".").concat(arr[3], ".").concat(arr[1]),
		"".concat(arr[2], ".").concat(arr[1], ".").concat(arr[0], ".").concat(arr[3]),
		"".concat(arr[2], ".").concat(arr[1], ".").concat(arr[3], ".").concat(arr[0]),
		"".concat(arr[2], ".").concat(arr[3], ".").concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[2], ".").concat(arr[3], ".").concat(arr[1], ".").concat(arr[0]),
		"".concat(arr[3], ".").concat(arr[0], ".").concat(arr[1], ".").concat(arr[2]),
		"".concat(arr[3], ".").concat(arr[0], ".").concat(arr[2], ".").concat(arr[1]),
		"".concat(arr[3], ".").concat(arr[1], ".").concat(arr[0], ".").concat(arr[2]),
		"".concat(arr[3], ".").concat(arr[1], ".").concat(arr[2], ".").concat(arr[0]),
		"".concat(arr[3], ".").concat(arr[2], ".").concat(arr[0], ".").concat(arr[1]),
		"".concat(arr[3], ".").concat(arr[2], ".").concat(arr[1], ".").concat(arr[0])
	];
}
var classNameCombinations = {};
function getClassNameCombinations(classNames) {
	if (classNames.length === 0 || classNames.length === 1) return classNames;
	var key = classNames.join(".");
	if (!classNameCombinations[key]) classNameCombinations[key] = powerSetPermutations(classNames);
	return classNameCombinations[key];
}
function createStyleObject(classNames) {
	var elementStyle = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
	var stylesheet = arguments.length > 2 ? arguments[2] : void 0;
	return getClassNameCombinations(classNames.filter(function(className) {
		return className !== "token";
	})).reduce(function(styleObject, className) {
		return _objectSpread$1(_objectSpread$1({}, styleObject), stylesheet[className]);
	}, elementStyle);
}
function createClassNameString(classNames) {
	return classNames.join(" ");
}
function createChildren(stylesheet, useInlineStyles) {
	var childrenCount = 0;
	return function(children) {
		childrenCount += 1;
		return children.map(function(child, i) {
			return createElement({
				node: child,
				stylesheet,
				useInlineStyles,
				key: "code-segment-".concat(childrenCount, "-").concat(i)
			});
		});
	};
}
function createElement(_ref) {
	var node = _ref.node, stylesheet = _ref.stylesheet, _ref$style = _ref.style, style = _ref$style === void 0 ? {} : _ref$style, useInlineStyles = _ref.useInlineStyles, key = _ref.key;
	var properties = node.properties, type = node.type, TagName = node.tagName, value = node.value;
	if (type === "text") return value;
	else if (TagName) {
		var childrenCreator = createChildren(stylesheet, useInlineStyles);
		var props;
		if (!useInlineStyles) props = _objectSpread$1(_objectSpread$1({}, properties), {}, { className: createClassNameString(properties.className) });
		else {
			var allStylesheetSelectors = Object.keys(stylesheet).reduce(function(classes, selector) {
				selector.split(".").forEach(function(className) {
					if (!classes.includes(className)) classes.push(className);
				});
				return classes;
			}, []);
			var startingClassName = properties.className && properties.className.includes("token") ? ["token"] : [];
			var className = properties.className && startingClassName.concat(properties.className.filter(function(className) {
				return !allStylesheetSelectors.includes(className);
			}));
			props = _objectSpread$1(_objectSpread$1({}, properties), {}, {
				className: createClassNameString(className) || void 0,
				style: createStyleObject(properties.className, Object.assign({}, properties.style, style), stylesheet)
			});
		}
		var children = childrenCreator(node.children);
		return /* @__PURE__ */ import_react.createElement(TagName, _extends({ key }, props), children);
	}
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/checkForListedLanguage.js
var checkForListedLanguage_default = (function(astGenerator, language) {
	return astGenerator.listLanguages().indexOf(language) !== -1;
});
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/highlight.js
var _excluded = [
	"language",
	"children",
	"style",
	"customStyle",
	"codeTagProps",
	"useInlineStyles",
	"showLineNumbers",
	"showInlineLineNumbers",
	"startingLineNumber",
	"lineNumberContainerStyle",
	"lineNumberStyle",
	"wrapLines",
	"wrapLongLines",
	"lineProps",
	"renderer",
	"PreTag",
	"CodeTag",
	"code",
	"astGenerator"
];
function ownKeys(e, r) {
	var t = Object.keys(e);
	if (Object.getOwnPropertySymbols) {
		var o = Object.getOwnPropertySymbols(e);
		r && (o = o.filter(function(r) {
			return Object.getOwnPropertyDescriptor(e, r).enumerable;
		})), t.push.apply(t, o);
	}
	return t;
}
function _objectSpread(e) {
	for (var r = 1; r < arguments.length; r++) {
		var t = null != arguments[r] ? arguments[r] : {};
		r % 2 ? ownKeys(Object(t), !0).forEach(function(r) {
			_defineProperty(e, r, t[r]);
		}) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys(Object(t)).forEach(function(r) {
			Object.defineProperty(e, r, Object.getOwnPropertyDescriptor(t, r));
		});
	}
	return e;
}
var newLineRegex = /\n/g;
function getNewLines(str) {
	return str.match(newLineRegex);
}
function getAllLineNumbers(_ref) {
	var lines = _ref.lines, startingLineNumber = _ref.startingLineNumber, style = _ref.style;
	return lines.map(function(_, i) {
		var number = i + startingLineNumber;
		return /* @__PURE__ */ import_react.createElement("span", {
			key: "line-".concat(i),
			className: "react-syntax-highlighter-line-number",
			style: typeof style === "function" ? style(number) : style
		}, "".concat(number, "\n"));
	});
}
function AllLineNumbers(_ref2) {
	var codeString = _ref2.codeString, codeStyle = _ref2.codeStyle, _ref2$containerStyle = _ref2.containerStyle, containerStyle = _ref2$containerStyle === void 0 ? {
		"float": "left",
		paddingRight: "10px"
	} : _ref2$containerStyle, _ref2$numberStyle = _ref2.numberStyle, numberStyle = _ref2$numberStyle === void 0 ? {} : _ref2$numberStyle, startingLineNumber = _ref2.startingLineNumber;
	return /* @__PURE__ */ import_react.createElement("code", { style: Object.assign({}, codeStyle, containerStyle) }, getAllLineNumbers({
		lines: codeString.replace(/\n$/, "").split("\n"),
		style: numberStyle,
		startingLineNumber
	}));
}
function getEmWidthOfNumber(num) {
	return "".concat(num.toString().length, ".25em");
}
function getInlineLineNumber(lineNumber, inlineLineNumberStyle) {
	return {
		type: "element",
		tagName: "span",
		properties: {
			key: "line-number--".concat(lineNumber),
			className: [
				"comment",
				"linenumber",
				"react-syntax-highlighter-line-number"
			],
			style: inlineLineNumberStyle
		},
		children: [{
			type: "text",
			value: lineNumber
		}]
	};
}
function assembleLineNumberStyles(lineNumberStyle, lineNumber, largestLineNumber) {
	var defaultLineNumberStyle = {
		display: "inline-block",
		minWidth: getEmWidthOfNumber(largestLineNumber),
		paddingRight: "1em",
		textAlign: "right",
		userSelect: "none"
	};
	var customLineNumberStyle = typeof lineNumberStyle === "function" ? lineNumberStyle(lineNumber) : lineNumberStyle;
	return _objectSpread(_objectSpread({}, defaultLineNumberStyle), customLineNumberStyle);
}
function createLineElement(_ref3) {
	var children = _ref3.children, lineNumber = _ref3.lineNumber, lineNumberStyle = _ref3.lineNumberStyle, largestLineNumber = _ref3.largestLineNumber, showInlineLineNumbers = _ref3.showInlineLineNumbers, _ref3$lineProps = _ref3.lineProps, lineProps = _ref3$lineProps === void 0 ? {} : _ref3$lineProps, _ref3$className = _ref3.className, className = _ref3$className === void 0 ? [] : _ref3$className, showLineNumbers = _ref3.showLineNumbers, wrapLongLines = _ref3.wrapLongLines, _ref3$wrapLines = _ref3.wrapLines;
	var properties = (_ref3$wrapLines === void 0 ? false : _ref3$wrapLines) ? _objectSpread({}, typeof lineProps === "function" ? lineProps(lineNumber) : lineProps) : {};
	properties["className"] = properties["className"] ? [].concat(_toConsumableArray(properties["className"].trim().split(/\s+/)), _toConsumableArray(className)) : className;
	if (lineNumber && showInlineLineNumbers) {
		var inlineLineNumberStyle = assembleLineNumberStyles(lineNumberStyle, lineNumber, largestLineNumber);
		children.unshift(getInlineLineNumber(lineNumber, inlineLineNumberStyle));
	}
	if (wrapLongLines & showLineNumbers) properties.style = _objectSpread({ display: "flex" }, properties.style);
	return {
		type: "element",
		tagName: "span",
		properties,
		children
	};
}
function flattenCodeTree(tree) {
	var className = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : [];
	var newTree = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : [];
	if (tree.length === void 0) tree = [tree];
	for (var i = 0; i < tree.length; i++) {
		var node = tree[i];
		if (node.type === "text") newTree.push(createLineElement({
			children: [node],
			className: _toConsumableArray(new Set(className))
		}));
		else if (node.children) {
			var _node$properties;
			var classNames = className.concat(((_node$properties = node.properties) === null || _node$properties === void 0 ? void 0 : _node$properties.className) || []);
			flattenCodeTree(node.children, classNames).forEach(function(i) {
				return newTree.push(i);
			});
		}
	}
	return newTree;
}
function processLines(codeTree, wrapLines, lineProps, showLineNumbers, showInlineLineNumbers, startingLineNumber, largestLineNumber, lineNumberStyle, wrapLongLines) {
	var _ref4;
	var tree = flattenCodeTree(codeTree.value);
	var newTree = [];
	var lastLineBreakIndex = -1;
	var index = 0;
	function createWrappedLine(children, lineNumber) {
		return createLineElement({
			children,
			lineNumber,
			lineNumberStyle,
			largestLineNumber,
			showInlineLineNumbers,
			lineProps,
			className: arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : [],
			showLineNumbers,
			wrapLongLines,
			wrapLines
		});
	}
	function createUnwrappedLine(children, lineNumber) {
		if (showLineNumbers && lineNumber && showInlineLineNumbers) {
			var inlineLineNumberStyle = assembleLineNumberStyles(lineNumberStyle, lineNumber, largestLineNumber);
			children.unshift(getInlineLineNumber(lineNumber, inlineLineNumberStyle));
		}
		return children;
	}
	function createLine(children, lineNumber) {
		var className = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : [];
		return wrapLines || className.length > 0 ? createWrappedLine(children, lineNumber, className) : createUnwrappedLine(children, lineNumber);
	}
	var _loop = function _loop() {
		var node = tree[index];
		var value = node.children[0].value;
		if (getNewLines(value)) {
			var splitValue = value.split("\n");
			splitValue.forEach(function(text, i) {
				var lineNumber = showLineNumbers && newTree.length + startingLineNumber;
				var newChild = {
					type: "text",
					value: "".concat(text, "\n")
				};
				if (i === 0) {
					var _line = createLine(tree.slice(lastLineBreakIndex + 1, index).concat(createLineElement({
						children: [newChild],
						className: node.properties.className
					})), lineNumber);
					newTree.push(_line);
				} else if (i === splitValue.length - 1) {
					var stringChild = tree[index + 1] && tree[index + 1].children && tree[index + 1].children[0];
					var lastLineInPreviousSpan = {
						type: "text",
						value: "".concat(text)
					};
					if (stringChild) {
						var newElem = createLineElement({
							children: [lastLineInPreviousSpan],
							className: node.properties.className
						});
						tree.splice(index + 1, 0, newElem);
					} else {
						var _line2 = createLine([lastLineInPreviousSpan], lineNumber, node.properties.className);
						newTree.push(_line2);
					}
				} else {
					var _line3 = createLine([newChild], lineNumber, node.properties.className);
					newTree.push(_line3);
				}
			});
			lastLineBreakIndex = index;
		}
		index++;
	};
	while (index < tree.length) _loop();
	if (lastLineBreakIndex !== tree.length - 1) {
		var children = tree.slice(lastLineBreakIndex + 1, tree.length);
		if (children && children.length) {
			var line = createLine(children, showLineNumbers && newTree.length + startingLineNumber);
			newTree.push(line);
		}
	}
	return wrapLines ? newTree : (_ref4 = []).concat.apply(_ref4, newTree);
}
function defaultRenderer(_ref5) {
	var rows = _ref5.rows, stylesheet = _ref5.stylesheet, useInlineStyles = _ref5.useInlineStyles;
	return rows.map(function(node, i) {
		return createElement({
			node,
			stylesheet,
			useInlineStyles,
			key: "code-segment-".concat(i)
		});
	});
}
function isHighlightJs(astGenerator) {
	return astGenerator && typeof astGenerator.highlightAuto !== "undefined";
}
function getCodeTree(_ref6) {
	var astGenerator = _ref6.astGenerator, language = _ref6.language, code = _ref6.code, defaultCodeValue = _ref6.defaultCodeValue;
	if (isHighlightJs(astGenerator)) {
		var hasLanguage = checkForListedLanguage_default(astGenerator, language);
		if (language === "text") return {
			value: defaultCodeValue,
			language: "text"
		};
		else if (hasLanguage) return astGenerator.highlight(language, code);
		else return astGenerator.highlightAuto(code);
	}
	try {
		return language && language !== "text" ? { value: astGenerator.highlight(code, language) } : { value: defaultCodeValue };
	} catch (e) {
		return { value: defaultCodeValue };
	}
}
function highlight_default(defaultAstGenerator, defaultStyle) {
	return function SyntaxHighlighter(_ref7) {
		var _code$match$length, _code$match;
		var language = _ref7.language, children = _ref7.children, _ref7$style = _ref7.style, style = _ref7$style === void 0 ? defaultStyle : _ref7$style, _ref7$customStyle = _ref7.customStyle, customStyle = _ref7$customStyle === void 0 ? {} : _ref7$customStyle, _ref7$codeTagProps = _ref7.codeTagProps, codeTagProps = _ref7$codeTagProps === void 0 ? {
			className: language ? "language-".concat(language) : void 0,
			style: _objectSpread(_objectSpread({}, style["code[class*=\"language-\"]"]), style["code[class*=\"language-".concat(language, "\"]")])
		} : _ref7$codeTagProps, _ref7$useInlineStyles = _ref7.useInlineStyles, useInlineStyles = _ref7$useInlineStyles === void 0 ? true : _ref7$useInlineStyles, _ref7$showLineNumbers = _ref7.showLineNumbers, showLineNumbers = _ref7$showLineNumbers === void 0 ? false : _ref7$showLineNumbers, _ref7$showInlineLineN = _ref7.showInlineLineNumbers, showInlineLineNumbers = _ref7$showInlineLineN === void 0 ? true : _ref7$showInlineLineN, _ref7$startingLineNum = _ref7.startingLineNumber, startingLineNumber = _ref7$startingLineNum === void 0 ? 1 : _ref7$startingLineNum, lineNumberContainerStyle = _ref7.lineNumberContainerStyle, _ref7$lineNumberStyle = _ref7.lineNumberStyle, lineNumberStyle = _ref7$lineNumberStyle === void 0 ? {} : _ref7$lineNumberStyle, wrapLines = _ref7.wrapLines, _ref7$wrapLongLines = _ref7.wrapLongLines, wrapLongLines = _ref7$wrapLongLines === void 0 ? false : _ref7$wrapLongLines, _ref7$lineProps = _ref7.lineProps, lineProps = _ref7$lineProps === void 0 ? {} : _ref7$lineProps, renderer = _ref7.renderer, _ref7$PreTag = _ref7.PreTag, PreTag = _ref7$PreTag === void 0 ? "pre" : _ref7$PreTag, _ref7$CodeTag = _ref7.CodeTag, CodeTag = _ref7$CodeTag === void 0 ? "code" : _ref7$CodeTag, _ref7$code = _ref7.code, code = _ref7$code === void 0 ? (Array.isArray(children) ? children[0] : children) || "" : _ref7$code, astGenerator = _ref7.astGenerator, rest = _objectWithoutProperties(_ref7, _excluded);
		astGenerator = astGenerator || defaultAstGenerator;
		var allLineNumbers = showLineNumbers ? /* @__PURE__ */ import_react.createElement(AllLineNumbers, {
			containerStyle: lineNumberContainerStyle,
			codeStyle: codeTagProps.style || {},
			numberStyle: lineNumberStyle,
			startingLineNumber,
			codeString: code
		}) : null;
		var defaultPreStyle = style.hljs || style["pre[class*=\"language-\"]"] || { backgroundColor: "#fff" };
		var generatorClassName = isHighlightJs(astGenerator) ? "hljs" : "prismjs";
		var preProps = useInlineStyles ? Object.assign({}, rest, { style: Object.assign({}, defaultPreStyle, customStyle) }) : Object.assign({}, rest, {
			className: rest.className ? "".concat(generatorClassName, " ").concat(rest.className) : generatorClassName,
			style: Object.assign({}, customStyle)
		});
		if (wrapLongLines) codeTagProps.style = _objectSpread({ whiteSpace: "pre-wrap" }, codeTagProps.style);
		else codeTagProps.style = _objectSpread({ whiteSpace: "pre" }, codeTagProps.style);
		if (!astGenerator) return /* @__PURE__ */ import_react.createElement(PreTag, preProps, allLineNumbers, /* @__PURE__ */ import_react.createElement(CodeTag, codeTagProps, code));
		if (wrapLines === void 0 && renderer || wrapLongLines) wrapLines = true;
		renderer = renderer || defaultRenderer;
		var defaultCodeValue = [{
			type: "text",
			value: code
		}];
		var codeTree = getCodeTree({
			astGenerator,
			language,
			code,
			defaultCodeValue
		});
		if (codeTree.language === null) codeTree.value = defaultCodeValue;
		var largestLineNumber = startingLineNumber + ((_code$match$length = (_code$match = code.match(/\n/g)) === null || _code$match === void 0 ? void 0 : _code$match.length) !== null && _code$match$length !== void 0 ? _code$match$length : 0);
		var rows = processLines(codeTree, wrapLines, lineProps, showLineNumbers, showInlineLineNumbers, startingLineNumber, largestLineNumber, lineNumberStyle, wrapLongLines);
		return /* @__PURE__ */ import_react.createElement(PreTag, preProps, /* @__PURE__ */ import_react.createElement(CodeTag, codeTagProps, !showInlineLineNumbers && allLineNumbers, renderer({
			rows,
			stylesheet: style,
			useInlineStyles
		})));
	};
}
//#endregion
//#region node_modules/hast-util-parse-selector/lib/index.js
/**
* @typedef {import('hast').Element} Element
* @typedef {import('hast').Properties} Properties
*/
/**
* @template {string} SimpleSelector
*   Selector type.
* @template {string} DefaultTagName
*   Default tag name.
* @typedef {(
*   SimpleSelector extends ''
*     ? DefaultTagName
*     : SimpleSelector extends `${infer TagName}.${infer Rest}`
*     ? ExtractTagName<TagName, DefaultTagName>
*     : SimpleSelector extends `${infer TagName}#${infer Rest}`
*     ? ExtractTagName<TagName, DefaultTagName>
*     : SimpleSelector extends string
*     ? SimpleSelector
*     : DefaultTagName
* )} ExtractTagName
*   Extract tag name from a simple selector.
*/
var search = /[#.]/g;
/**
* Create a hast element from a simple CSS selector.
*
* @template {string} Selector
*   Type of selector.
* @template {string} [DefaultTagName='div']
*   Type of default tag name (default: `'div'`).
* @param {Selector | null | undefined} [selector]
*   Simple CSS selector (optional).
*
*   Can contain a tag name (`foo`), classes (`.bar`), and an ID (`#baz`).
*   Multiple classes are allowed.
*   Uses the last ID if multiple IDs are found.
* @param {DefaultTagName | null | undefined} [defaultTagName='div']
*   Tag name to use if `selector` does not specify one (default: `'div'`).
* @returns {Element & {tagName: ExtractTagName<Selector, DefaultTagName>}}
*   Built element.
*/
function parseSelector(selector, defaultTagName) {
	const value = selector || "";
	/** @type {Properties} */
	const props = {};
	let start = 0;
	/** @type {string | undefined} */
	let previous;
	/** @type {string | undefined} */
	let tagName;
	while (start < value.length) {
		search.lastIndex = start;
		const match = search.exec(value);
		const subvalue = value.slice(start, match ? match.index : value.length);
		if (subvalue) {
			if (!previous) tagName = subvalue;
			else if (previous === "#") props.id = subvalue;
			else if (Array.isArray(props.className)) props.className.push(subvalue);
			else props.className = [subvalue];
			start += subvalue.length;
		}
		if (match) {
			previous = match[0];
			start++;
		}
	}
	return {
		type: "element",
		tagName: tagName || defaultTagName || "div",
		properties: props,
		children: []
	};
}
//#endregion
//#region node_modules/hastscript/lib/create-h.js
/**
* @import {Element, Nodes, RootContent, Root} from 'hast'
* @import {Info, Schema} from 'property-information'
*/
/**
* @typedef {Array<Nodes | PrimitiveChild>} ArrayChildNested
*   List of children (deep).
*/
/**
* @typedef {Array<ArrayChildNested | Nodes | PrimitiveChild>} ArrayChild
*   List of children.
*/
/**
* @typedef {Array<number | string>} ArrayValue
*   List of property values for space- or comma separated values (such as `className`).
*/
/**
* @typedef {ArrayChild | Nodes | PrimitiveChild} Child
*   Acceptable child value.
*/
/**
* @typedef {number | string | null | undefined} PrimitiveChild
*   Primitive children, either ignored (nullish), or turned into text nodes.
*/
/**
* @typedef {boolean | number | string | null | undefined} PrimitiveValue
*   Primitive property value.
*/
/**
* @typedef {Record<string, PropertyValue | Style>} Properties
*   Acceptable value for element properties.
*/
/**
* @typedef {ArrayValue | PrimitiveValue} PropertyValue
*   Primitive value or list value.
*/
/**
* @typedef {Element | Root} Result
*   Result from a `h` (or `s`) call.
*/
/**
* @typedef {number | string} StyleValue
*   Value for a CSS style field.
*/
/**
* @typedef {Record<string, StyleValue>} Style
*   Supported value of a `style` prop.
*/
/**
* @param {Schema} schema
*   Schema to use.
* @param {string} defaultTagName
*   Default tag name.
* @param {ReadonlyArray<string> | undefined} [caseSensitive]
*   Case-sensitive tag names (default: `undefined`).
* @returns
*   `h`.
*/
function createH(schema, defaultTagName, caseSensitive) {
	const adjust = caseSensitive ? createAdjustMap(caseSensitive) : void 0;
	/**
	* Hyperscript compatible DSL for creating virtual hast trees.
	*
	* @overload
	* @param {null | undefined} [selector]
	* @param {...Child} children
	* @returns {Root}
	*
	* @overload
	* @param {string} selector
	* @param {Properties} properties
	* @param {...Child} children
	* @returns {Element}
	*
	* @overload
	* @param {string} selector
	* @param {...Child} children
	* @returns {Element}
	*
	* @param {string | null | undefined} [selector]
	*   Selector.
	* @param {Child | Properties | null | undefined} [properties]
	*   Properties (or first child) (default: `undefined`).
	* @param {...Child} children
	*   Children.
	* @returns {Result}
	*   Result.
	*/
	function h(selector, properties, ...children) {
		/** @type {Result} */
		let node;
		if (selector === null || selector === void 0) {
			node = {
				type: "root",
				children: []
			};
			const child = properties;
			children.unshift(child);
		} else {
			node = parseSelector(selector, defaultTagName);
			const lower = node.tagName.toLowerCase();
			const adjusted = adjust ? adjust.get(lower) : void 0;
			node.tagName = adjusted || lower;
			if (isChild(properties)) children.unshift(properties);
			else for (const [key, value] of Object.entries(properties)) addProperty(schema, node.properties, key, value);
		}
		for (const child of children) addChild(node.children, child);
		if (node.type === "element" && node.tagName === "template") {
			node.content = {
				type: "root",
				children: node.children
			};
			node.children = [];
		}
		return node;
	}
	return h;
}
/**
* Check if something is properties or a child.
*
* @param {Child | Properties} value
*   Value to check.
* @returns {value is Child}
*   Whether `value` is definitely a child.
*/
function isChild(value) {
	if (value === null || typeof value !== "object" || Array.isArray(value)) return true;
	if (typeof value.type !== "string") return false;
	const record = value;
	const keys = Object.keys(value);
	for (const key of keys) {
		const value = record[key];
		if (value && typeof value === "object") {
			if (!Array.isArray(value)) return true;
			const list = value;
			for (const item of list) if (typeof item !== "number" && typeof item !== "string") return true;
		}
	}
	if ("children" in value && Array.isArray(value.children)) return true;
	return false;
}
/**
* @param {Schema} schema
*   Schema.
* @param {Properties} properties
*   Properties object.
* @param {string} key
*   Property name.
* @param {PropertyValue | Style} value
*   Property value.
* @returns {undefined}
*   Nothing.
*/
function addProperty(schema, properties, key, value) {
	const info = find(schema, key);
	/** @type {PropertyValue} */
	let result;
	if (value === null || value === void 0) return;
	if (typeof value === "number") {
		if (Number.isNaN(value)) return;
		result = value;
	} else if (typeof value === "boolean") result = value;
	else if (typeof value === "string") if (info.spaceSeparated) result = parse(value);
	else if (info.commaSeparated) result = parse$1(value);
	else if (info.commaOrSpaceSeparated) result = parse(parse$1(value).join(" "));
	else result = parsePrimitive(info, info.property, value);
	else if (Array.isArray(value)) result = [...value];
	else result = info.property === "style" ? style(value) : String(value);
	if (Array.isArray(result)) {
		/** @type {Array<number | string>} */
		const finalResult = [];
		for (const item of result) finalResult.push(parsePrimitive(info, info.property, item));
		result = finalResult;
	}
	if (info.property === "className" && Array.isArray(properties.className)) result = properties.className.concat(result);
	properties[info.property] = result;
}
/**
* @param {Array<RootContent>} nodes
*   Children.
* @param {Child} value
*   Child.
* @returns {undefined}
*   Nothing.
*/
function addChild(nodes, value) {
	if (value === null || value === void 0) {} else if (typeof value === "number" || typeof value === "string") nodes.push({
		type: "text",
		value: String(value)
	});
	else if (Array.isArray(value)) for (const child of value) addChild(nodes, child);
	else if (typeof value === "object" && "type" in value) if (value.type === "root") addChild(nodes, value.children);
	else nodes.push(value);
	else throw new Error("Expected node, nodes, or string, got `" + value + "`");
}
/**
* Parse a single primitives.
*
* @param {Info} info
*   Property information.
* @param {string} name
*   Property name.
* @param {PrimitiveValue} value
*   Property value.
* @returns {PrimitiveValue}
*   Property value.
*/
function parsePrimitive(info, name, value) {
	if (typeof value === "string") {
		if (info.number && value && !Number.isNaN(Number(value))) return Number(value);
		if ((info.boolean || info.overloadedBoolean) && (value === "" || normalize(value) === normalize(name))) return true;
	}
	return value;
}
/**
* Serialize a `style` object as a string.
*
* @param {Style} styles
*   Style object.
* @returns {string}
*   CSS string.
*/
function style(styles) {
	/** @type {Array<string>} */
	const result = [];
	for (const [key, value] of Object.entries(styles)) result.push([key, value].join(": "));
	return result.join("; ");
}
/**
* Create a map to adjust casing.
*
* @param {ReadonlyArray<string>} values
*   List of properly cased keys.
* @returns {Map<string, string>}
*   Map of lowercase keys to uppercase keys.
*/
function createAdjustMap(values) {
	/** @type {Map<string, string>} */
	const result = /* @__PURE__ */ new Map();
	for (const value of values) result.set(value.toLowerCase(), value);
	return result;
}
//#endregion
//#region node_modules/hastscript/lib/svg-case-sensitive-tag-names.js
/**
* List of case-sensitive SVG tag names.
*
* @type {ReadonlyArray<string>}
*/
var svgCaseSensitiveTagNames = [
	"altGlyph",
	"altGlyphDef",
	"altGlyphItem",
	"animateColor",
	"animateMotion",
	"animateTransform",
	"clipPath",
	"feBlend",
	"feColorMatrix",
	"feComponentTransfer",
	"feComposite",
	"feConvolveMatrix",
	"feDiffuseLighting",
	"feDisplacementMap",
	"feDistantLight",
	"feDropShadow",
	"feFlood",
	"feFuncA",
	"feFuncB",
	"feFuncG",
	"feFuncR",
	"feGaussianBlur",
	"feImage",
	"feMerge",
	"feMergeNode",
	"feMorphology",
	"feOffset",
	"fePointLight",
	"feSpecularLighting",
	"feSpotLight",
	"feTile",
	"feTurbulence",
	"foreignObject",
	"glyphRef",
	"linearGradient",
	"radialGradient",
	"solidColor",
	"textArea",
	"textPath"
];
//#endregion
//#region node_modules/hastscript/lib/index.js
/**
* @typedef {import('./jsx-classic.js').Element} h.JSX.Element
* @typedef {import('./jsx-classic.js').ElementChildrenAttribute} h.JSX.ElementChildrenAttribute
* @typedef {import('./jsx-classic.js').IntrinsicAttributes} h.JSX.IntrinsicAttributes
* @typedef {import('./jsx-classic.js').IntrinsicElements} h.JSX.IntrinsicElements
*/
/**
* @typedef {import('./jsx-classic.js').Element} s.JSX.Element
* @typedef {import('./jsx-classic.js').ElementChildrenAttribute} s.JSX.ElementChildrenAttribute
* @typedef {import('./jsx-classic.js').IntrinsicAttributes} s.JSX.IntrinsicAttributes
* @typedef {import('./jsx-classic.js').IntrinsicElements} s.JSX.IntrinsicElements
*/
/** @type {ReturnType<createH>} */
var h = createH(html, "div");
createH(svg, "g", svgCaseSensitiveTagNames);
//#endregion
//#region node_modules/character-entities-legacy/index.js
/**
* List of legacy HTML named character references that don’t need a trailing semicolon.
*
* @type {Array<string>}
*/
var characterEntitiesLegacy = [
	"AElig",
	"AMP",
	"Aacute",
	"Acirc",
	"Agrave",
	"Aring",
	"Atilde",
	"Auml",
	"COPY",
	"Ccedil",
	"ETH",
	"Eacute",
	"Ecirc",
	"Egrave",
	"Euml",
	"GT",
	"Iacute",
	"Icirc",
	"Igrave",
	"Iuml",
	"LT",
	"Ntilde",
	"Oacute",
	"Ocirc",
	"Ograve",
	"Oslash",
	"Otilde",
	"Ouml",
	"QUOT",
	"REG",
	"THORN",
	"Uacute",
	"Ucirc",
	"Ugrave",
	"Uuml",
	"Yacute",
	"aacute",
	"acirc",
	"acute",
	"aelig",
	"agrave",
	"amp",
	"aring",
	"atilde",
	"auml",
	"brvbar",
	"ccedil",
	"cedil",
	"cent",
	"copy",
	"curren",
	"deg",
	"divide",
	"eacute",
	"ecirc",
	"egrave",
	"eth",
	"euml",
	"frac12",
	"frac14",
	"frac34",
	"gt",
	"iacute",
	"icirc",
	"iexcl",
	"igrave",
	"iquest",
	"iuml",
	"laquo",
	"lt",
	"macr",
	"micro",
	"middot",
	"nbsp",
	"not",
	"ntilde",
	"oacute",
	"ocirc",
	"ograve",
	"ordf",
	"ordm",
	"oslash",
	"otilde",
	"ouml",
	"para",
	"plusmn",
	"pound",
	"quot",
	"raquo",
	"reg",
	"sect",
	"shy",
	"sup1",
	"sup2",
	"sup3",
	"szlig",
	"thorn",
	"times",
	"uacute",
	"ucirc",
	"ugrave",
	"uml",
	"uuml",
	"yacute",
	"yen",
	"yuml"
];
//#endregion
//#region node_modules/character-reference-invalid/index.js
/**
* Map of invalid numeric character references to their replacements, according to HTML.
*
* @type {Record<number, string>}
*/
var characterReferenceInvalid = {
	0: "�",
	128: "€",
	130: "‚",
	131: "ƒ",
	132: "„",
	133: "…",
	134: "†",
	135: "‡",
	136: "ˆ",
	137: "‰",
	138: "Š",
	139: "‹",
	140: "Œ",
	142: "Ž",
	145: "‘",
	146: "’",
	147: "“",
	148: "”",
	149: "•",
	150: "–",
	151: "—",
	152: "˜",
	153: "™",
	154: "š",
	155: "›",
	156: "œ",
	158: "ž",
	159: "Ÿ"
};
//#endregion
//#region node_modules/is-decimal/index.js
/**
* Check if the given character code, or the character code at the first
* character, is decimal.
*
* @param {string|number} character
* @returns {boolean} Whether `character` is a decimal
*/
function isDecimal(character) {
	const code = typeof character === "string" ? character.charCodeAt(0) : character;
	return code >= 48 && code <= 57;
}
//#endregion
//#region node_modules/is-hexadecimal/index.js
/**
* Check if the given character code, or the character code at the first
* character, is hexadecimal.
*
* @param {string|number} character
* @returns {boolean} Whether `character` is hexadecimal
*/
function isHexadecimal(character) {
	const code = typeof character === "string" ? character.charCodeAt(0) : character;
	return code >= 97 && code <= 102 || code >= 65 && code <= 70 || code >= 48 && code <= 57;
}
//#endregion
//#region node_modules/is-alphabetical/index.js
/**
* Check if the given character code, or the character code at the first
* character, is alphabetical.
*
* @param {string|number} character
* @returns {boolean} Whether `character` is alphabetical.
*/
function isAlphabetical(character) {
	const code = typeof character === "string" ? character.charCodeAt(0) : character;
	return code >= 97 && code <= 122 || code >= 65 && code <= 90;
}
//#endregion
//#region node_modules/is-alphanumerical/index.js
/**
* Check if the given character code, or the character code at the first
* character, is alphanumerical.
*
* @param {string|number} character
* @returns {boolean} Whether `character` is alphanumerical.
*/
function isAlphanumerical(character) {
	return isAlphabetical(character) || isDecimal(character);
}
//#endregion
//#region node_modules/parse-entities/lib/index.js
/**
* @import {Point} from 'unist'
* @import {Options} from '../index.js'
*/
var messages = [
	"",
	"Named character references must be terminated by a semicolon",
	"Numeric character references must be terminated by a semicolon",
	"Named character references cannot be empty",
	"Numeric character references cannot be empty",
	"Named character references must be known",
	"Numeric character references cannot be disallowed",
	"Numeric character references cannot be outside the permissible Unicode range"
];
/**
* Parse HTML character references.
*
* @param {string} value
* @param {Readonly<Options> | null | undefined} [options]
*/
function parseEntities(value, options) {
	const settings = options || {};
	const additional = typeof settings.additional === "string" ? settings.additional.charCodeAt(0) : settings.additional;
	/** @type {Array<string>} */
	const result = [];
	let index = 0;
	let lines = -1;
	let queue = "";
	/** @type {Point | undefined} */
	let point;
	/** @type {Array<number>|undefined} */
	let indent;
	if (settings.position) if ("start" in settings.position || "indent" in settings.position) {
		indent = settings.position.indent;
		point = settings.position.start;
	} else point = settings.position;
	let line = (point ? point.line : 0) || 1;
	let column = (point ? point.column : 0) || 1;
	let previous = now();
	/** @type {number|undefined} */
	let character;
	index--;
	while (++index <= value.length) {
		if (character === 10) column = (indent ? indent[lines] : 0) || 1;
		character = value.charCodeAt(index);
		if (character === 38) {
			const following = value.charCodeAt(index + 1);
			if (following === 9 || following === 10 || following === 12 || following === 32 || following === 38 || following === 60 || Number.isNaN(following) || additional && following === additional) {
				queue += String.fromCharCode(character);
				column++;
				continue;
			}
			const start = index + 1;
			let begin = start;
			let end = start;
			/** @type {string} */
			let type;
			if (following === 35) {
				end = ++begin;
				const following = value.charCodeAt(end);
				if (following === 88 || following === 120) {
					type = "hexadecimal";
					end = ++begin;
				} else type = "decimal";
			} else type = "named";
			let characterReferenceCharacters = "";
			let characterReference = "";
			let characters = "";
			const test = type === "named" ? isAlphanumerical : type === "decimal" ? isDecimal : isHexadecimal;
			end--;
			while (++end <= value.length) {
				const following = value.charCodeAt(end);
				if (!test(following)) break;
				characters += String.fromCharCode(following);
				if (type === "named" && characterEntitiesLegacy.includes(characters)) {
					characterReferenceCharacters = characters;
					characterReference = decodeNamedCharacterReference(characters);
				}
			}
			let terminated = value.charCodeAt(end) === 59;
			if (terminated) {
				end++;
				const namedReference = type === "named" ? decodeNamedCharacterReference(characters) : false;
				if (namedReference) {
					characterReferenceCharacters = characters;
					characterReference = namedReference;
				}
			}
			let diff = 1 + end - start;
			let reference = "";
			if (!terminated && settings.nonTerminated === false) {} else if (!characters) {
				if (type !== "named") warning(4, diff);
			} else if (type === "named") {
				if (terminated && !characterReference) warning(5, 1);
				else {
					if (characterReferenceCharacters !== characters) {
						end = begin + characterReferenceCharacters.length;
						diff = 1 + end - begin;
						terminated = false;
					}
					if (!terminated) {
						const reason = characterReferenceCharacters ? 1 : 3;
						if (settings.attribute) {
							const following = value.charCodeAt(end);
							if (following === 61) {
								warning(reason, diff);
								characterReference = "";
							} else if (isAlphanumerical(following)) characterReference = "";
							else warning(reason, diff);
						} else warning(reason, diff);
					}
				}
				reference = characterReference;
			} else {
				if (!terminated) warning(2, diff);
				let referenceCode = Number.parseInt(characters, type === "hexadecimal" ? 16 : 10);
				if (prohibited(referenceCode)) {
					warning(7, diff);
					reference = String.fromCharCode(65533);
				} else if (referenceCode in characterReferenceInvalid) {
					warning(6, diff);
					reference = characterReferenceInvalid[referenceCode];
				} else {
					let output = "";
					if (disallowed(referenceCode)) warning(6, diff);
					if (referenceCode > 65535) {
						referenceCode -= 65536;
						output += String.fromCharCode(referenceCode >>> 10 | 55296);
						referenceCode = 56320 | referenceCode & 1023;
					}
					reference = output + String.fromCharCode(referenceCode);
				}
			}
			if (reference) {
				flush();
				previous = now();
				index = end - 1;
				column += end - start + 1;
				result.push(reference);
				const next = now();
				next.offset++;
				if (settings.reference) settings.reference.call(settings.referenceContext || void 0, reference, {
					start: previous,
					end: next
				}, value.slice(start - 1, end));
				previous = next;
			} else {
				characters = value.slice(start - 1, end);
				queue += characters;
				column += characters.length;
				index = end - 1;
			}
		} else {
			if (character === 10) {
				line++;
				lines++;
				column = 0;
			}
			if (Number.isNaN(character)) flush();
			else {
				queue += String.fromCharCode(character);
				column++;
			}
		}
	}
	return result.join("");
	function now() {
		return {
			line,
			column,
			offset: index + ((point ? point.offset : 0) || 0)
		};
	}
	/**
	* Handle the warning.
	*
	* @param {1|2|3|4|5|6|7} code
	* @param {number} offset
	*/
	function warning(code, offset) {
		/** @type {ReturnType<now>} */
		let position;
		if (settings.warning) {
			position = now();
			position.column += offset;
			position.offset += offset;
			settings.warning.call(settings.warningContext || void 0, messages[code], position, code);
		}
	}
	/**
	* Flush `queue` (normal text).
	* Macro invoked before each reference and at the end of `value`.
	* Does nothing when `queue` is empty.
	*/
	function flush() {
		if (queue) {
			result.push(queue);
			if (settings.text) settings.text.call(settings.textContext || void 0, queue, {
				start: previous,
				end: now()
			});
			queue = "";
		}
	}
}
/**
* Check if `character` is outside the permissible unicode range.
*
* @param {number} code
* @returns {boolean}
*/
function prohibited(code) {
	return code >= 55296 && code <= 57343 || code > 1114111;
}
/**
* Check if `character` is disallowed.
*
* @param {number} code
* @returns {boolean}
*/
function disallowed(code) {
	return code >= 1 && code <= 8 || code === 11 || code >= 13 && code <= 31 || code >= 127 && code <= 159 || code >= 64976 && code <= 65007 || (code & 65535) === 65535 || (code & 65535) === 65534;
}
//#endregion
//#region node_modules/refractor/lib/prism-core.js
var uniqueId = 0;
var plainTextGrammar = {};
var _ = {
	/**
	* A namespace for utility methods.
	*
	* All function in this namespace that are not explicitly marked as _public_ are for __internal use only__ and may
	* change or disappear at any time.
	*
	* @namespace
	* @memberof Prism
	*/
	util: {
		/**
		* Returns the name of the type of the given value.
		*
		* @param {any} o
		* @returns {string}
		* @example
		* type(null)      === 'Null'
		* type(undefined) === 'Undefined'
		* type(123)       === 'Number'
		* type('foo')     === 'String'
		* type(true)      === 'Boolean'
		* type([1, 2])    === 'Array'
		* type({})        === 'Object'
		* type(String)    === 'Function'
		* type(/abc+/)    === 'RegExp'
		*/
		type: function(o) {
			return Object.prototype.toString.call(o).slice(8, -1);
		},
		/**
		* Returns a unique number for the given object. Later calls will still return the same number.
		*
		* @param {Object} obj
		* @returns {number}
		*/
		objId: function(obj) {
			if (!obj["__id"]) Object.defineProperty(obj, "__id", { value: ++uniqueId });
			return obj["__id"];
		},
		/**
		* Creates a deep clone of the given object.
		*
		* The main intended use of this function is to clone language definitions.
		*
		* @param {T} o
		* @param {Record<number, any>} [visited]
		* @returns {T}
		* @template T
		*/
		clone: function deepClone(o, visited) {
			visited = visited || {};
			var clone;
			var id;
			switch (_.util.type(o)) {
				case "Object":
					id = _.util.objId(o);
					if (visited[id]) return visited[id];
					clone = {};
					visited[id] = clone;
					for (var key in o) if (o.hasOwnProperty(key)) clone[key] = deepClone(o[key], visited);
					return clone;
				case "Array":
					id = _.util.objId(o);
					if (visited[id]) return visited[id];
					clone = [];
					visited[id] = clone;
					/** @type {Array} */ o.forEach(function(v, i) {
						clone[i] = deepClone(v, visited);
					});
					return clone;
				default: return o;
			}
		}
	},
	/**
	* This namespace contains all currently loaded languages and the some helper functions to create and modify languages.
	*
	* @namespace
	* @memberof Prism
	* @public
	*/
	languages: {
		/**
		* The grammar for plain, unformatted text.
		*/
		plain: plainTextGrammar,
		plaintext: plainTextGrammar,
		text: plainTextGrammar,
		txt: plainTextGrammar,
		/**
		* Creates a deep copy of the language with the given id and appends the given tokens.
		*
		* If a token in `redef` also appears in the copied language, then the existing token in the copied language
		* will be overwritten at its original position.
		*
		* ## Best practices
		*
		* Since the position of overwriting tokens (token in `redef` that overwrite tokens in the copied language)
		* doesn't matter, they can technically be in any order. However, this can be confusing to others that trying to
		* understand the language definition because, normally, the order of tokens matters in Prism grammars.
		*
		* Therefore, it is encouraged to order overwriting tokens according to the positions of the overwritten tokens.
		* Furthermore, all non-overwriting tokens should be placed after the overwriting ones.
		*
		* @param {string} id The id of the language to extend. This has to be a key in `Prism.languages`.
		* @param {Grammar} redef The new tokens to append.
		* @returns {Grammar} The new language created.
		* @public
		* @example
		* Prism.languages['css-with-colors'] = Prism.languages.extend('css', {
		*     // Prism.languages.css already has a 'comment' token, so this token will overwrite CSS' 'comment' token
		*     // at its original position
		*     'comment': { ... },
		*     // CSS doesn't have a 'color' token, so this token will be appended
		*     'color': /\b(?:red|green|blue)\b/
		* });
		*/
		extend: function(id, redef) {
			var lang = _.util.clone(_.languages[id]);
			for (var key in redef) lang[key] = redef[key];
			return lang;
		},
		/**
		* Inserts tokens _before_ another token in a language definition or any other grammar.
		*
		* ## Usage
		*
		* This helper method makes it easy to modify existing languages. For example, the CSS language definition
		* not only defines CSS highlighting for CSS documents, but also needs to define highlighting for CSS embedded
		* in HTML through `<style>` elements. To do this, it needs to modify `Prism.languages.markup` and add the
		* appropriate tokens. However, `Prism.languages.markup` is a regular JavaScript object literal, so if you do
		* this:
		*
		* ```js
		* Prism.languages.markup.style = {
		*     // token
		* };
		* ```
		*
		* then the `style` token will be added (and processed) at the end. `insertBefore` allows you to insert tokens
		* before existing tokens. For the CSS example above, you would use it like this:
		*
		* ```js
		* Prism.languages.insertBefore('markup', 'cdata', {
		*     'style': {
		*         // token
		*     }
		* });
		* ```
		*
		* ## Special cases
		*
		* If the grammars of `inside` and `insert` have tokens with the same name, the tokens in `inside`'s grammar
		* will be ignored.
		*
		* This behavior can be used to insert tokens after `before`:
		*
		* ```js
		* Prism.languages.insertBefore('markup', 'comment', {
		*     'comment': Prism.languages.markup.comment,
		*     // tokens after 'comment'
		* });
		* ```
		*
		* ## Limitations
		*
		* The main problem `insertBefore` has to solve is iteration order. Since ES2015, the iteration order for object
		* properties is guaranteed to be the insertion order (except for integer keys) but some browsers behave
		* differently when keys are deleted and re-inserted. So `insertBefore` can't be implemented by temporarily
		* deleting properties which is necessary to insert at arbitrary positions.
		*
		* To solve this problem, `insertBefore` doesn't actually insert the given tokens into the target object.
		* Instead, it will create a new object and replace all references to the target object with the new one. This
		* can be done without temporarily deleting properties, so the iteration order is well-defined.
		*
		* However, only references that can be reached from `Prism.languages` or `insert` will be replaced. I.e. if
		* you hold the target object in a variable, then the value of the variable will not change.
		*
		* ```js
		* var oldMarkup = Prism.languages.markup;
		* var newMarkup = Prism.languages.insertBefore('markup', 'comment', { ... });
		*
		* assert(oldMarkup !== Prism.languages.markup);
		* assert(newMarkup === Prism.languages.markup);
		* ```
		*
		* @param {string} inside The property of `root` (e.g. a language id in `Prism.languages`) that contains the
		* object to be modified.
		* @param {string} before The key to insert before.
		* @param {Grammar} insert An object containing the key-value pairs to be inserted.
		* @param {Object<string, any>} [root] The object containing `inside`, i.e. the object that contains the
		* object to be modified.
		*
		* Defaults to `Prism.languages`.
		* @returns {Grammar} The new grammar object.
		* @public
		*/
		insertBefore: function(inside, before, insert, root) {
			root = root || _.languages;
			var grammar = root[inside];
			/** @type {Grammar} */
			var ret = {};
			for (var token in grammar) if (grammar.hasOwnProperty(token)) {
				if (token == before) {
					for (var newToken in insert) if (insert.hasOwnProperty(newToken)) ret[newToken] = insert[newToken];
				}
				if (!insert.hasOwnProperty(token)) ret[token] = grammar[token];
			}
			var old = root[inside];
			root[inside] = ret;
			_.languages.DFS(_.languages, function(key, value) {
				if (value === old && key != inside) this[key] = ret;
			});
			return ret;
		},
		DFS: function DFS(o, callback, type, visited) {
			visited = visited || {};
			var objId = _.util.objId;
			for (var i in o) if (o.hasOwnProperty(i)) {
				callback.call(o, i, o[i], type || i);
				var property = o[i];
				var propertyType = _.util.type(property);
				if (propertyType === "Object" && !visited[objId(property)]) {
					visited[objId(property)] = true;
					DFS(property, callback, null, visited);
				} else if (propertyType === "Array" && !visited[objId(property)]) {
					visited[objId(property)] = true;
					DFS(property, callback, i, visited);
				}
			}
		}
	},
	plugins: {},
	/**
	* Low-level function, only use if you know what you’re doing. It accepts a string of text as input
	* and the language definitions to use, and returns a string with the HTML produced.
	*
	* The following hooks will be run:
	* 1. `before-tokenize`
	* 2. `after-tokenize`
	* 3. `wrap`: On each {@link Token}.
	*
	* @param {string} text A string with the code to be highlighted.
	* @param {Grammar} grammar An object containing the tokens to use.
	*
	* Usually a language definition like `Prism.languages.markup`.
	* @param {string} language The name of the language definition passed to `grammar`.
	* @returns {string} The highlighted HTML.
	* @memberof Prism
	* @public
	* @example
	* Prism.highlight('var foo = true;', Prism.languages.javascript, 'javascript');
	*/
	highlight: function(text, grammar, language) {
		var env = {
			code: text,
			grammar,
			language
		};
		_.hooks.run("before-tokenize", env);
		if (!env.grammar) throw new Error("The language \"" + env.language + "\" has no grammar.");
		env.tokens = _.tokenize(env.code, env.grammar);
		_.hooks.run("after-tokenize", env);
		return Token.stringify(_.util.encode(env.tokens), env.language);
	},
	/**
	* This is the heart of Prism, and the most low-level function you can use. It accepts a string of text as input
	* and the language definitions to use, and returns an array with the tokenized code.
	*
	* When the language definition includes nested tokens, the function is called recursively on each of these tokens.
	*
	* This method could be useful in other contexts as well, as a very crude parser.
	*
	* @param {string} text A string with the code to be highlighted.
	* @param {Grammar} grammar An object containing the tokens to use.
	*
	* Usually a language definition like `Prism.languages.markup`.
	* @returns {TokenStream} An array of strings and tokens, a token stream.
	* @memberof Prism
	* @public
	* @example
	* let code = `var foo = 0;`;
	* let tokens = Prism.tokenize(code, Prism.languages.javascript);
	* tokens.forEach(token => {
	*     if (token instanceof Prism.Token && token.type === 'number') {
	*         console.log(`Found numeric literal: ${token.content}`);
	*     }
	* });
	*/
	tokenize: function(text, grammar) {
		var rest = grammar.rest;
		if (rest) {
			for (var token in rest) grammar[token] = rest[token];
			delete grammar.rest;
		}
		var tokenList = new LinkedList();
		addAfter(tokenList, tokenList.head, text);
		matchGrammar(text, tokenList, grammar, tokenList.head, 0);
		return toArray(tokenList);
	},
	/**
	* @namespace
	* @memberof Prism
	* @public
	*/
	hooks: {
		all: {},
		/**
		* Adds the given callback to the list of callbacks for the given hook.
		*
		* The callback will be invoked when the hook it is registered for is run.
		* Hooks are usually directly run by a highlight function but you can also run hooks yourself.
		*
		* One callback function can be registered to multiple hooks and the same hook multiple times.
		*
		* @param {string} name The name of the hook.
		* @param {HookCallback} callback The callback function which is given environment variables.
		* @public
		*/
		add: function(name, callback) {
			var hooks = _.hooks.all;
			hooks[name] = hooks[name] || [];
			hooks[name].push(callback);
		},
		/**
		* Runs a hook invoking all registered callbacks with the given environment variables.
		*
		* Callbacks will be invoked synchronously and in the order in which they were registered.
		*
		* @param {string} name The name of the hook.
		* @param {Object<string, any>} env The environment variables of the hook passed to all callbacks registered.
		* @public
		*/
		run: function(name, env) {
			var callbacks = _.hooks.all[name];
			if (!callbacks || !callbacks.length) return;
			for (var i = 0, callback; callback = callbacks[i++];) callback(env);
		}
	},
	Token
};
/**
* Creates a new token.
*
* @param {string} type See {@link Token#type type}
* @param {string | TokenStream} content See {@link Token#content content}
* @param {string|string[]} [alias] The alias(es) of the token.
* @param {string} [matchedStr=""] A copy of the full string this token was created from.
* @class
* @global
* @public
*/
function Token(type, content, alias, matchedStr) {
	/**
	* The type of the token.
	*
	* This is usually the key of a pattern in a {@link Grammar}.
	*
	* @type {string}
	* @see GrammarToken
	* @public
	*/
	this.type = type;
	/**
	* The strings or tokens contained by this token.
	*
	* This will be a token stream if the pattern matched also defined an `inside` grammar.
	*
	* @type {string | TokenStream}
	* @public
	*/
	this.content = content;
	/**
	* The alias(es) of the token.
	*
	* @type {string|string[]}
	* @see GrammarToken
	* @public
	*/
	this.alias = alias;
	this.length = (matchedStr || "").length | 0;
}
/**
* A token stream is an array of strings and {@link Token Token} objects.
*
* Token streams have to fulfill a few properties that are assumed by most functions (mostly internal ones) that process
* them.
*
* 1. No adjacent strings.
* 2. No empty strings.
*
*    The only exception here is the token stream that only contains the empty string and nothing else.
*
* @typedef {Array<string | Token>} TokenStream
* @global
* @public
*/
/**
* @param {RegExp} pattern
* @param {number} pos
* @param {string} text
* @param {boolean} lookbehind
* @returns {RegExpExecArray | null}
*/
function matchPattern(pattern, pos, text, lookbehind) {
	pattern.lastIndex = pos;
	var match = pattern.exec(text);
	if (match && lookbehind && match[1]) {
		var lookbehindLength = match[1].length;
		match.index += lookbehindLength;
		match[0] = match[0].slice(lookbehindLength);
	}
	return match;
}
/**
* @param {string} text
* @param {LinkedList<string | Token>} tokenList
* @param {any} grammar
* @param {LinkedListNode<string | Token>} startNode
* @param {number} startPos
* @param {RematchOptions} [rematch]
* @returns {void}
* @private
*
* @typedef RematchOptions
* @property {string} cause
* @property {number} reach
*/
function matchGrammar(text, tokenList, grammar, startNode, startPos, rematch) {
	for (var token in grammar) {
		if (!grammar.hasOwnProperty(token) || !grammar[token]) continue;
		var patterns = grammar[token];
		patterns = Array.isArray(patterns) ? patterns : [patterns];
		for (var j = 0; j < patterns.length; ++j) {
			if (rematch && rematch.cause == token + "," + j) return;
			var patternObj = patterns[j];
			var inside = patternObj.inside;
			var lookbehind = !!patternObj.lookbehind;
			var greedy = !!patternObj.greedy;
			var alias = patternObj.alias;
			if (greedy && !patternObj.pattern.global) {
				var flags = patternObj.pattern.toString().match(/[imsuy]*$/)[0];
				patternObj.pattern = RegExp(patternObj.pattern.source, flags + "g");
			}
			/** @type {RegExp} */
			var pattern = patternObj.pattern || patternObj;
			for (var currentNode = startNode.next, pos = startPos; currentNode !== tokenList.tail; pos += currentNode.value.length, currentNode = currentNode.next) {
				if (rematch && pos >= rematch.reach) break;
				var str = currentNode.value;
				if (tokenList.length > text.length) return;
				if (str instanceof Token) continue;
				var removeCount = 1;
				var match;
				if (greedy) {
					match = matchPattern(pattern, pos, text, lookbehind);
					if (!match || match.index >= text.length) break;
					var from = match.index;
					var to = match.index + match[0].length;
					var p = pos;
					p += currentNode.value.length;
					while (from >= p) {
						currentNode = currentNode.next;
						p += currentNode.value.length;
					}
					p -= currentNode.value.length;
					pos = p;
					if (currentNode.value instanceof Token) continue;
					for (var k = currentNode; k !== tokenList.tail && (p < to || typeof k.value === "string"); k = k.next) {
						removeCount++;
						p += k.value.length;
					}
					removeCount--;
					str = text.slice(pos, p);
					match.index -= pos;
				} else {
					match = matchPattern(pattern, 0, str, lookbehind);
					if (!match) continue;
				}
				var from = match.index;
				var matchStr = match[0];
				var before = str.slice(0, from);
				var after = str.slice(from + matchStr.length);
				var reach = pos + str.length;
				if (rematch && reach > rematch.reach) rematch.reach = reach;
				var removeFrom = currentNode.prev;
				if (before) {
					removeFrom = addAfter(tokenList, removeFrom, before);
					pos += before.length;
				}
				removeRange(tokenList, removeFrom, removeCount);
				var wrapped = new Token(token, inside ? _.tokenize(matchStr, inside) : matchStr, alias, matchStr);
				currentNode = addAfter(tokenList, removeFrom, wrapped);
				if (after) addAfter(tokenList, currentNode, after);
				if (removeCount > 1) {
					/** @type {RematchOptions} */
					var nestedRematch = {
						cause: token + "," + j,
						reach
					};
					matchGrammar(text, tokenList, grammar, currentNode.prev, pos, nestedRematch);
					if (rematch && nestedRematch.reach > rematch.reach) rematch.reach = nestedRematch.reach;
				}
			}
		}
	}
}
/**
* @typedef LinkedListNode
* @property {T} value
* @property {LinkedListNode<T> | null} prev The previous node.
* @property {LinkedListNode<T> | null} next The next node.
* @template T
* @private
*/
/**
* @template T
* @private
*/
function LinkedList() {
	/** @type {LinkedListNode<T>} */
	var head = {
		value: null,
		prev: null,
		next: null
	};
	/** @type {LinkedListNode<T>} */
	var tail = {
		value: null,
		prev: head,
		next: null
	};
	head.next = tail;
	/** @type {LinkedListNode<T>} */
	this.head = head;
	/** @type {LinkedListNode<T>} */
	this.tail = tail;
	this.length = 0;
}
/**
* Adds a new node with the given value to the list.
*
* @param {LinkedList<T>} list
* @param {LinkedListNode<T>} node
* @param {T} value
* @returns {LinkedListNode<T>} The added node.
* @template T
*/
function addAfter(list, node, value) {
	var next = node.next;
	var newNode = {
		value,
		prev: node,
		next
	};
	node.next = newNode;
	next.prev = newNode;
	list.length++;
	return newNode;
}
/**
* Removes `count` nodes after the given node. The given node will not be removed.
*
* @param {LinkedList<T>} list
* @param {LinkedListNode<T>} node
* @param {number} count
* @template T
*/
function removeRange(list, node, count) {
	var next = node.next;
	for (var i = 0; i < count && next !== list.tail; i++) next = next.next;
	node.next = next;
	next.prev = node;
	list.length -= i;
}
/**
* @param {LinkedList<T>} list
* @returns {T[]}
* @template T
*/
function toArray(list) {
	var array = [];
	var node = list.head.next;
	while (node !== list.tail) {
		array.push(node.value);
		node = node.next;
	}
	return array;
}
var Prism = _;
//#endregion
//#region node_modules/refractor/lib/core.js
/**
* @import {Element, Root, Text} from 'hast'
* @import {Grammar, Languages} from 'prismjs'
*/
/**
* @typedef _Token
*   Hidden Prism token.
* @property {string} alias
*   Alias.
* @property {string} content
*   Content.
* @property {number} length
*   Length.
* @property {string} type
*   Type.
*/
/**
* @typedef _Env
*   Hidden Prism environment.
* @property {Record<string, string>} attributes
*   Attributes.
* @property {Array<string>} classes
*   Classes.
* @property {Array<Element | Text> | Element | Text} content
*   Content.
* @property {string} language
*   Language.
* @property {string} tag
*   Tag.
* @property {string} type
*   Type.
*/
/**
* @typedef {((prism: Refractor) => undefined | void) & {aliases?: Array<string> | undefined, displayName: string}} Syntax
*   Refractor syntax function.
*/
/**
* @typedef Refractor
*   Virtual syntax highlighting
* @property {typeof alias} alias
* @property {Languages} languages
* @property {typeof listLanguages} listLanguages
* @property {typeof highlight} highlight
* @property {typeof registered} registered
* @property {typeof register} register
*/
function Refractor() {}
Refractor.prototype = Prism;
/** @type {Refractor} */
var refractor = new Refractor();
refractor.highlight = highlight;
refractor.register = register;
refractor.alias = alias;
refractor.registered = registered;
refractor.listLanguages = listLanguages;
refractor.util.encode = encode;
refractor.Token.stringify = stringify;
/**
* Highlight `value` (code) as `language` (programming language).
*
* @param {string} value
*   Code to highlight.
* @param {Grammar | string} language
*   Programming language name, alias, or grammar.
* @returns {Root}
*   Node representing highlighted code.
*/
function highlight(value, language) {
	if (typeof value !== "string") throw new TypeError("Expected `string` for `value`, got `" + value + "`");
	/** @type {Grammar} */
	let grammar;
	/** @type {string | undefined} */
	let name;
	/* c8 ignore next 2 */
	if (language && typeof language === "object") grammar = language;
	else {
		name = language;
		if (typeof name !== "string") throw new TypeError("Expected `string` for `name`, got `" + name + "`");
		if (Object.hasOwn(refractor.languages, name)) grammar = refractor.languages[name];
		else throw new Error("Unknown language: `" + name + "` is not registered");
	}
	return {
		type: "root",
		children: Prism.highlight.call(refractor, value, grammar, name)
	};
}
/**
* Register a syntax.
*
* @param {Syntax} syntax
*   Language function made for refractor, as in, the files in
*   `refractor/lang/*.js`.
* @returns {undefined}
*   Nothing.
*/
function register(syntax) {
	if (typeof syntax !== "function" || !syntax.displayName) throw new Error("Expected `function` for `syntax`, got `" + syntax + "`");
	if (!Object.hasOwn(refractor.languages, syntax.displayName)) syntax(refractor);
}
/**
* Register aliases for already registered languages.
*
* @param {Record<string, ReadonlyArray<string> | string> | string} language
*   Language to alias.
* @param {ReadonlyArray<string> | string | null | undefined} [alias]
*   Aliases.
* @returns {undefined}
*   Nothing.
*/
function alias(language, alias) {
	const languages = refractor.languages;
	/** @type {Record<string, ReadonlyArray<string> | string>} */
	let map = {};
	if (typeof language === "string") {
		if (alias) map[language] = alias;
	} else map = language;
	/** @type {string} */
	let key;
	for (key in map) if (Object.hasOwn(map, key)) {
		const value = map[key];
		const list = typeof value === "string" ? [value] : value;
		let index = -1;
		while (++index < list.length) languages[list[index]] = languages[key];
	}
}
/**
* Check whether an `alias` or `language` is registered.
*
* @param {string} aliasOrLanguage
*   Language or alias to check.
* @returns {boolean}
*   Whether the language is registered.
*/
function registered(aliasOrLanguage) {
	if (typeof aliasOrLanguage !== "string") throw new TypeError("Expected `string` for `aliasOrLanguage`, got `" + aliasOrLanguage + "`");
	return Object.hasOwn(refractor.languages, aliasOrLanguage);
}
/**
* List all registered languages (names and aliases).
*
* @returns {Array<string>}
*   List of language names.
*/
function listLanguages() {
	const languages = refractor.languages;
	/** @type {Array<string>} */
	const list = [];
	/** @type {string} */
	let language;
	for (language in languages) if (Object.hasOwn(languages, language) && typeof languages[language] === "object") list.push(language);
	return list;
}
/**
* @param {Array<_Token | string> | _Token | string} value
*   Token to stringify.
* @param {string} language
*   Language of the token.
* @returns {Array<Element | Text> | Element | Text}
*   Node representing the token.
*/
function stringify(value, language) {
	if (typeof value === "string") return {
		type: "text",
		value
	};
	if (Array.isArray(value)) {
		/** @type {Array<Element | Text>} */
		const result = [];
		let index = -1;
		while (++index < value.length) if (value[index] !== null && value[index] !== void 0 && value[index] !== "") result.push(stringify(value[index], language));
		return result;
	}
	/** @type {_Env} */
	const env = {
		attributes: {},
		classes: ["token", value.type],
		content: stringify(value.content, language),
		language,
		tag: "span",
		type: value.type
	};
	if (value.alias) env.classes.push(...typeof value.alias === "string" ? [value.alias] : value.alias);
	refractor.hooks.run("wrap", env);
	return h(env.tag + "." + env.classes.join("."), attributes(env.attributes), env.content);
}
/**
* @template {unknown} T
*   Tokens.
* @param {T} tokens
*   Input.
* @returns {T}
*   Output, same as input.
*/
function encode(tokens) {
	return tokens;
}
/**
* @param {Record<string, string>} record
*   Attributes.
* @returns {Record<string, string>}
*   Attributes.
*/
function attributes(record) {
	/** @type {string} */
	let key;
	for (key in record) if (Object.hasOwn(record, key)) record[key] = parseEntities(record[key]);
	return record;
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/prism-light.js
var SyntaxHighlighter = highlight_default(refractor, {});
SyntaxHighlighter.registerLanguage = function(_, language) {
	return refractor.register(language);
};
SyntaxHighlighter.alias = function(name, aliases) {
	return refractor.alias(name, aliases);
};
//#endregion
export { SyntaxHighlighter as default };

//# sourceMappingURL=react-syntax-highlighter_dist_esm_prism-light.js.map