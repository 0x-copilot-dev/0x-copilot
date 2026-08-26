// The vocabulary a syntax highlighter speaks to this package, and the map from
// a fence hint / file extension onto a grammar we actually carry.
//
// Deliberately NOT shiki's own types. Both consumers — the markdown code block
// (through streamdown's `plugins.code` seam) and the file-diff rows — depend on
// THIS interface, so the engine behind it is one wiring change. That mattered
// while adopting shiki was still in doubt: the risk in this repository is never
// the rendering, it is the lockfile (see `shikiHighlighter.ts` for how the
// dependency edge was added without regenerating it).

/** One run of characters that share a colour. */
export interface SyntaxToken {
  readonly content: string;
  /**
   * CSS colour for the run — always a `var(--syntax-…)` reference, never a
   * literal hex lifted out of a shiki theme. The variables are declared in
   * `markdown.css` from design-system tokens, so a theme switch (dark / light /
   * slate) re-paints highlighted code with no JS involved and no second
   * highlighter pass. Absent = paint at the inherited foreground.
   */
  readonly color?: string;
  readonly italic?: boolean;
  readonly bold?: boolean;
  readonly underline?: boolean;
}

/** Tokens of a single source line. An empty line is an empty array. */
export type SyntaxLine = readonly SyntaxToken[];

export interface SyntaxHighlighter {
  /** Is `language` one of the grammars this highlighter carries? */
  supports(language: string): boolean;
  /**
   * Tokens for `code`, or `null` when the grammar is not resident yet.
   *
   * SYNCHRONOUS AND TOTAL, by contract: it never awaits, never throws, and
   * never triggers a load. The transcript streams, and markdown parsing already
   * runs on the main thread — a highlighter that can block the render path is
   * worse than no highlighter. Callers render plain text on `null` and upgrade
   * when `load()` resolves.
   */
  tokenize(code: string, language: string): readonly SyntaxLine[] | null;
  /**
   * Is `language` resident, i.e. would `tokenize` colour it right now?
   *
   * Distinct from `supports`, and the distinction is the point: `tokenize`
   * returns `null` both for a grammar that has not loaded yet and for one that
   * never will (unknown language, oversized block, a grammar that threw). Only
   * the first case is worth awaiting a `load` for.
   */
  isReady(language: string): boolean;
  /**
   * Make `language` resident. Idempotent, and resolves rather than rejects when
   * the grammar cannot be loaded — a failed download must degrade to plain
   * text, not to an unhandled rejection inside a message renderer.
   */
  load(language: string): Promise<void>;
}

/**
 * The grammars we carry. Enumerated, not `shiki/bundle/full`, because the
 * desktop renderer is bundled by esbuild into ONE file with no code splitting
 * (`apps/desktop/esbuild.config.mjs` uses `outfile:`), so a dynamic
 * `import()` is *inlined* rather than deferred. Every grammar named here is
 * weight the app pays at boot: the full set is 2.2 MB of grammar JSON against a
 * 1.9 MB renderer bundle, this set is ~570 KB.
 *
 * Two absences are choices, not oversights:
 *
 * - `html` costs four times its own size — its grammar `import`s the full
 *   `javascript` grammar to highlight `<script>` bodies (60 KB + 181 KB).
 * - `typescript` / `javascript` / `jsx` are all served by `tsx`, which is a
 *   syntactic superset of the three. The one thing it gets wrong is the legacy
 *   `<T>expr` cast, which reads as a JSX tag; `as` has been the idiomatic form
 *   for years and this repository uses it exclusively.
 */
export const SYNTAX_LANGUAGES = [
  "css",
  "diff",
  "docker",
  "go",
  "ini",
  "java",
  "json",
  "markdown",
  "python",
  "rust",
  "shellscript",
  "sql",
  "toml",
  "tsx",
  "xml",
  "yaml",
] as const;

export type SyntaxLanguage = (typeof SYNTAX_LANGUAGES)[number];

const LANGUAGE_SET = new Set<string>(SYNTAX_LANGUAGES);

/** Fence hints (```ts) and tool-reported language names, lowercased. */
const ALIASES: Readonly<Record<string, SyntaxLanguage>> = {
  bash: "shellscript",
  cjs: "tsx",
  cts: "tsx",
  dockerfile: "docker",
  golang: "go",
  javascript: "tsx",
  js: "tsx",
  jsx: "tsx",
  md: "markdown",
  mjs: "tsx",
  mts: "tsx",
  patch: "diff",
  py: "python",
  rs: "rust",
  sh: "shellscript",
  shell: "shellscript",
  svg: "xml",
  ts: "tsx",
  typescript: "tsx",
  yml: "yaml",
  zsh: "shellscript",
};

/** File extensions, without the dot. Only where they differ from `ALIASES`. */
const EXTENSIONS: Readonly<Record<string, SyntaxLanguage>> = {
  cfg: "ini",
  conf: "ini",
  htm: "xml",
  mdx: "markdown",
  properties: "ini",
};

/**
 * The grammar for a fence hint, or `null` for "render this as plain text".
 *
 * `null` is the honest answer for an unknown hint AND for the explicit ones
 * (`text`, `plaintext`, no hint at all) — a code block whose language we cannot
 * name must look deliberate, not broken.
 */
export function resolveSyntaxLanguage(
  hint: string | null | undefined,
): SyntaxLanguage | null {
  if (!hint) return null;
  const key = hint.trim().toLowerCase();
  if (LANGUAGE_SET.has(key)) return key as SyntaxLanguage;
  return ALIASES[key] ?? null;
}

/**
 * The grammar implied by a file path, for surfaces that have a path but no
 * fence hint — the file-diff card, whose language is a property of the file the
 * agent edited and is never stated anywhere in the tool call.
 */
export function syntaxLanguageForPath(
  path: string | null | undefined,
): SyntaxLanguage | null {
  if (!path) return null;
  const name = path.split(/[/\\]/).pop()?.toLowerCase() ?? "";
  // Extension-less files that ARE a language: a Dockerfile, a Makefile.
  if (name === "dockerfile" || name.startsWith("dockerfile.")) return "docker";
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = name.slice(dot + 1);
  return EXTENSIONS[ext] ?? resolveSyntaxLanguage(ext);
}

/**
 * Above this many characters a block is left as plain text.
 *
 * Not a performance guess — the same reasoning as `lineDiff`'s `maxLines` and
 * `TcFileDiff`'s `maxRows`: the transcript is not a file viewer. A streaming
 * code block is re-tokenized on every model delta, so the cost is paid per
 * frame, and past a few hundred lines nobody is reading the colours anyway.
 */
// MEASURED, not chosen for readability. streamdown re-tokenizes the WHOLE
// block from scratch inside a useEffect keyed on `code`, and `code` grows on
// every model delta — so this bound is how long the main thread blocks per
// delta, not how much a person will read. At 40k the heaviest grammar (tsx)
// measured 49ms, and 87ms on dense TS: ~11fps while tokens arrive. At 8k it
// is ~10ms — one 60fps frame — and still ~135 lines, well past the point
// where nobody is reading the colours anyway.
export const MAX_HIGHLIGHTED_CHARS = 8_000;
