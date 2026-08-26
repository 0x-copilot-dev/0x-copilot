// Streamdown's `plugins.code` seam, filled with our highlighter.
//
// Streamdown ships no highlighter of its own — `CodeHighlighterPlugin` is a
// hole the host fills, and until this module existed nobody filled it, which is
// why every code block in the app rendered as flat text while `shiki` sat
// resolved in the lockfile the whole time.
//
// THE STALENESS TRAP, which is why `highlight` never returns `null`.
// `HighlightedCodeBlockBody` holds its result in `useState(raw)` and its effect
// only writes that state when `highlight` returns a value or calls back:
//
//     let r = plugin.highlight({...}, c => setResult(c));
//     r && setResult(r);
//
// There is no `else`. Return `null` from a cold grammar and the body keeps
// rendering the result from a PREVIOUS render — during streaming that is the
// first delta, frozen, while the model keeps writing. So a cold or unsupported
// language returns uncoloured tokens for the CURRENT code instead: same text
// streamdown would have shown unplugged, upgraded in place once the grammar
// lands.

import type {
  BundledLanguage,
  CodeHighlighterPlugin,
  PluginConfig,
  ThemeInput,
} from "streamdown";

import { getSyntaxHighlighter } from "./syntaxHighlighting";
import {
  SYNTAX_LANGUAGES,
  resolveSyntaxLanguage,
  type SyntaxLine,
} from "./syntaxTokens";

// Streamdown declares these two but does not export them, so they are derived
// from the one member it does export. Re-declaring them by hand would give this
// file its own private copy of a contract it does not own — the shape would
// drift on the next streamdown bump with nothing failing until a code block
// rendered wrong.
type HighlightResult = NonNullable<
  ReturnType<CodeHighlighterPlugin["highlight"]>
>;
type HighlightToken = HighlightResult["tokens"][number][number];

/**
 * Streamdown asks the plugin which theme pair to hand back to `highlight`, and
 * we ignore the answer: our token colours are CSS variables resolved by the
 * app's own theme (see `markdown.css`), so there is no light/dark shiki pair to
 * choose between. A frozen constant keeps streamdown's context memo stable.
 */
const THEMES: [ThemeInput, ThemeInput] = [
  { name: "copilot-syntax", settings: [] },
  { name: "copilot-syntax", settings: [] },
];

const LANGUAGES = SYNTAX_LANGUAGES as readonly string[] as BundledLanguage[];

const shikiCodePlugin: CodeHighlighterPlugin = {
  name: "shiki",
  type: "code-highlighter",

  getSupportedLanguages: () => LANGUAGES,
  getThemes: () => THEMES,
  supportsLanguage: (language) => resolveSyntaxLanguage(language) !== null,

  highlight({ code, language }, callback) {
    const highlighter = getSyntaxHighlighter();
    const resolved = resolveSyntaxLanguage(language);
    if (!highlighter || !resolved) return plainResult(code);

    const ready = highlighter.tokenize(code, resolved);
    if (ready) return toHighlightResult(ready);

    if (callback) {
      // Ordering is what makes a late callback safe. Every call for the same
      // language awaits the SAME in-flight load promise, so its continuations
      // run in registration order — i.e. in the order the deltas arrived — and
      // the newest code is the last to write. No generation counter needed.
      void highlighter.load(resolved).then(() => {
        const late = highlighter.tokenize(code, resolved);
        if (late) callback(toHighlightResult(late));
      });
    }
    return plainResult(code);
  },
};

/** The `plugins` prop itself — one frozen object, so streamdown's memo holds. */
export const codeHighlighterPlugins: PluginConfig = Object.freeze({
  code: shikiCodePlugin,
});

export { shikiCodePlugin };

function toHighlightResult(lines: readonly SyntaxLine[]): HighlightResult {
  return {
    tokens: lines.map((line) =>
      line.map((token): HighlightToken => {
        const style: Record<string, string> = {};
        if (token.italic) style["font-style"] = "italic";
        if (token.bold) style["font-weight"] = "bold";
        if (token.underline) style["text-decoration"] = "underline";
        return {
          content: token.content,
          ...(token.color ? { color: token.color } : {}),
          ...(Object.keys(style).length > 0 ? { htmlStyle: style } : {}),
        };
      }),
    ),
  };
}

/** One colourless token per line — streamdown's own `raw` shape, rebuilt. */
function plainResult(code: string): HighlightResult {
  return { tokens: code.split("\n").map((line) => [{ content: line }]) };
}
