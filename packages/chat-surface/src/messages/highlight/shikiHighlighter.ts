// The shiki-backed `SyntaxHighlighter`.
//
// HOW THE DEPENDENCY GOT HERE. `shiki@4.4.2` was already resolved in the root
// lockfile — `apps/website`'s Astro depends on it — and hoisted to the root
// `node_modules`. This package's lockfile does not survive regeneration (a peer
// conflict plus that same website hoisting mean no `npm install` reproduces its
// committed shape, which is why grouped dependabot updates cannot land), so the
// edge was added SURGICALLY: two lines in `packages/chat-surface/package.json`
// and the two matching lines in the workspace's own node in
// `package-lock.json`. No re-resolution, no new tree node, and
// `npm ci --dry-run` reports the tree already satisfied. If you bump this
// version, edit both files by hand the same way — do not run `npm install`.
//
// WHY THE JAVASCRIPT REGEX ENGINE, not oniguruma. Oniguruma is a WASM binary,
// and the desktop renderer is a single-file esbuild bundle whose loader map
// (`apps/desktop/esbuild.config.mjs`) has no `.wasm` entry — the build would
// fail, or worse, ship a bundle that throws on the first code block. The JS
// engine is pure JavaScript and `forgiving: true` degrades an unsupported
// pattern to "no colour here" instead of throwing mid-render.

import {
  MAX_HIGHLIGHTED_CHARS,
  SYNTAX_LANGUAGES,
  type SyntaxHighlighter,
  type SyntaxLanguage,
  type SyntaxLine,
  type SyntaxToken,
} from "./syntaxTokens";

type ShikiCore = Awaited<
  ReturnType<typeof import("shiki/core").createHighlighterCore>
>;

/**
 * Static import specifiers, one per grammar — never a computed
 * `import(\`@shikijs/langs/${id}\`)`. A bundler cannot follow a template
 * specifier: esbuild fails the build outright and Vite silently emits nothing,
 * so the "bounded subset" would be bounded at zero. This map IS the bound.
 */
const GRAMMARS: Readonly<Record<SyntaxLanguage, () => Promise<unknown>>> = {
  css: () => import("@shikijs/langs/css"),
  diff: () => import("@shikijs/langs/diff"),
  docker: () => import("@shikijs/langs/docker"),
  go: () => import("@shikijs/langs/go"),
  ini: () => import("@shikijs/langs/ini"),
  java: () => import("@shikijs/langs/java"),
  json: () => import("@shikijs/langs/json"),
  markdown: () => import("@shikijs/langs/markdown"),
  python: () => import("@shikijs/langs/python"),
  rust: () => import("@shikijs/langs/rust"),
  shellscript: () => import("@shikijs/langs/shellscript"),
  sql: () => import("@shikijs/langs/sql"),
  toml: () => import("@shikijs/langs/toml"),
  tsx: () => import("@shikijs/langs/tsx"),
  xml: () => import("@shikijs/langs/xml"),
  yaml: () => import("@shikijs/langs/yaml"),
};

/**
 * The theme name registered with shiki. Its colours are `var(--syntax-…)`
 * references rather than hexes (`createCssVariablesTheme`), which is the whole
 * reason one theme covers dark, light and slate: the app's stylesheet resolves
 * the variable, so switching theme re-paints highlighted code with zero
 * re-tokenization. `variablePrefix` matches the block in `markdown.css`.
 */
const THEME_NAME = "copilot-syntax";
const VARIABLE_PREFIX = "--syntax-";

// TextMate font-style bits (`FontStyle` in @shikijs/vscode-textmate). Inlined
// as constants rather than imported: the enum lives in a package we do not
// depend on directly, and importing it eagerly would drag shiki into the main
// chunk that the dynamic import below exists to keep it out of.
const FONT_STYLE_ITALIC = 1;
const FONT_STYLE_BOLD = 2;
const FONT_STYLE_UNDERLINE = 4;

export function createShikiHighlighter(): SyntaxHighlighter {
  let core: ShikiCore | null = null;
  let booting: Promise<ShikiCore | null> | null = null;
  const resident = new Set<SyntaxLanguage>();
  const pending = new Map<SyntaxLanguage, Promise<void>>();

  // Whole-module dynamic import, not a top-level one. Nothing shiki-related is
  // reachable until a code block actually renders, so a conversation with no
  // code costs nothing — and on the web host (a real code-splitting bundler)
  // the engine lands in its own chunk instead of the entry.
  async function boot(): Promise<ShikiCore | null> {
    if (core) return core;
    booting ??= (async () => {
      try {
        const [{ createHighlighterCore, createCssVariablesTheme }, engine] =
          await Promise.all([
            import("shiki/core"),
            import("shiki/engine/javascript"),
          ]);
        core = await createHighlighterCore({
          themes: [
            createCssVariablesTheme({
              name: THEME_NAME,
              variablePrefix: VARIABLE_PREFIX,
            }),
          ],
          langs: [],
          engine: engine.createJavaScriptRegexEngine({ forgiving: true }),
        });
        return core;
      } catch {
        // A highlighter that cannot boot must leave the transcript readable.
        return null;
      }
    })();
    return booting;
  }

  function isSupported(language: string): language is SyntaxLanguage {
    return language in GRAMMARS;
  }

  return {
    supports: isSupported,

    isReady: (language) => isSupported(language) && resident.has(language),

    tokenize(code, language) {
      if (!core || !isSupported(language)) return null;
      if (!resident.has(language)) return null;
      if (code.length > MAX_HIGHLIGHTED_CHARS) return null;
      try {
        const result = core.codeToTokens(code, {
          lang: language,
          theme: THEME_NAME,
        });
        return result.tokens.map(toSyntaxLine);
      } catch {
        // Never let a grammar edge case take the message down with it.
        return null;
      }
    },

    async load(language) {
      if (!isSupported(language) || resident.has(language)) return;
      let inFlight = pending.get(language);
      if (!inFlight) {
        inFlight = (async () => {
          const shiki = await boot();
          if (!shiki) return;
          try {
            await shiki.loadLanguage(
              GRAMMARS[language]() as Parameters<
                ShikiCore["loadLanguage"]
              >[number],
            );
            resident.add(language);
          } catch {
            // Leave it absent: `tokenize` keeps returning null and the caller
            // keeps rendering plain text. Retried on the next `load`.
          } finally {
            pending.delete(language);
          }
        })();
        pending.set(language, inFlight);
      }
      await inFlight;
    },
  };
}

/** Shiki's per-line themed tokens, narrowed to what a renderer needs. */
function toSyntaxLine(
  tokens: readonly {
    content: string;
    color?: string;
    fontStyle?: number;
  }[],
): SyntaxLine {
  return tokens.map((token): SyntaxToken => {
    const style = token.fontStyle ?? 0;
    return {
      content: token.content,
      ...(token.color ? { color: token.color } : {}),
      ...(style & FONT_STYLE_ITALIC ? { italic: true } : {}),
      ...(style & FONT_STYLE_BOLD ? { bold: true } : {}),
      ...(style & FONT_STYLE_UNDERLINE ? { underline: true } : {}),
    };
  });
}

/** Every grammar this highlighter can serve, for callers that advertise a set. */
export const SHIKI_LANGUAGES: readonly SyntaxLanguage[] = SYNTAX_LANGUAGES;
