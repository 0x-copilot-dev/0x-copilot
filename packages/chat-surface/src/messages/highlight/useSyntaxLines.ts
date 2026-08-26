// Tokens for a block of code, for surfaces that render their own rows rather
// than going through streamdown — today the file-diff card.
//
// The contract this hook exists to keep: the first paint is never delayed. It
// returns whatever the highlighter can answer synchronously (`null` on a cold
// grammar), starts the load in an effect, and re-renders once. Callers paint
// plain text on `null`, so a slow or failed grammar costs a colour, never a
// frame.

import { useEffect, useMemo, useReducer } from "react";

import { getSyntaxHighlighter } from "./syntaxHighlighting";
import {
  MAX_HIGHLIGHTED_CHARS,
  type SyntaxLanguage,
  type SyntaxLine,
} from "./syntaxTokens";

export function useSyntaxLines(
  code: string,
  language: SyntaxLanguage | null,
): readonly SyntaxLine[] | null {
  const highlighter = getSyntaxHighlighter();
  // Bumped when a grammar arrives. `useMemo` below reads it so the memo — not
  // just the component — recomputes; without it the re-render would hand back
  // the cached `null` and the colours would never appear.
  const [grammarEpoch, grammarArrived] = useReducer(
    (epoch: number) => epoch + 1,
    0,
  );
  const tooLarge = code.length > MAX_HIGHLIGHTED_CHARS;

  useEffect(() => {
    if (!highlighter || !language || tooLarge) return;
    if (highlighter.isReady(language)) return;
    let live = true;
    void highlighter.load(language).then(() => {
      if (live) grammarArrived();
    });
    return () => {
      live = false;
    };
  }, [highlighter, language, tooLarge]);

  return useMemo(() => {
    if (!highlighter || !language || tooLarge) return null;
    return highlighter.tokenize(code, language);
    // `grammarEpoch` is a dependency on purpose: it is the signal that
    // `tokenize` will now answer for a language it previously refused.
  }, [highlighter, language, code, tooLarge, grammarEpoch]);
}
