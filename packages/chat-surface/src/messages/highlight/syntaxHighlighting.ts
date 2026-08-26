// The one highlighter every surface in this package renders through.
//
// It defaults to the shiki-backed one rather than starting empty and waiting
// for a host to install it. A seam whose only production wiring is "the host
// remembers to call the setter" is this repository's most expensive recurring
// defect — a correct mechanism, green tests, and nothing on screen. Both hosts
// mount `MarkdownText` and `TcFileDiff` without knowing this module exists, so
// the default IS the wiring; the setter exists for tests that need determinism
// and for a host that deliberately wants none.

import { createShikiHighlighter } from "./shikiHighlighter";
import type { SyntaxHighlighter } from "./syntaxTokens";

let installed: SyntaxHighlighter | null = null;
let overridden = false;

/** The active highlighter, or `null` when a caller has explicitly removed it. */
export function getSyntaxHighlighter(): SyntaxHighlighter | null {
  if (!overridden) installed ??= createShikiHighlighter();
  return installed;
}

/**
 * Replace the active highlighter. `null` means "no highlighting" and is a
 * supported state, not a broken one — every consumer already renders plain text
 * whenever tokens are unavailable, so removing the highlighter exercises the
 * same path a cold grammar does.
 */
export function setSyntaxHighlighter(
  highlighter: SyntaxHighlighter | null,
): void {
  installed = highlighter;
  overridden = true;
}

/** Restore the default (shiki). Tests use this to undo `setSyntaxHighlighter`. */
export function resetSyntaxHighlighter(): void {
  installed = null;
  overridden = false;
}
