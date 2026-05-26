import { t as typescript } from "./typescript-CM89_DFx.js";
import { t as jsx } from "./jsx-qzcfdqSK.js";
//#region node_modules/refractor/lang/tsx.js
/**
* @import {Refractor} from '../lib/core.js'
*/
tsx.displayName = "tsx";
tsx.aliases = [];
/** @param {Refractor} Prism */
function tsx(Prism) {
	Prism.register(jsx);
	Prism.register(typescript);
	(function(Prism) {
		var typescript = Prism.util.clone(Prism.languages.typescript);
		Prism.languages.tsx = Prism.languages.extend("jsx", typescript);
		delete Prism.languages.tsx["parameter"];
		delete Prism.languages.tsx["literal-property"];
		var tag = Prism.languages.tsx.tag;
		tag.pattern = RegExp(/(^|[^\w$]|(?=<\/))/.source + "(?:" + tag.pattern.source + ")", tag.pattern.flags);
		tag.lookbehind = true;
	})(Prism);
}
//#endregion
//#region node_modules/react-syntax-highlighter/dist/esm/languages/prism/tsx.js
var tsx_default = tsx;
//#endregion
export { tsx_default as default };

//# sourceMappingURL=react-syntax-highlighter_dist_esm_languages_prism_tsx.js.map