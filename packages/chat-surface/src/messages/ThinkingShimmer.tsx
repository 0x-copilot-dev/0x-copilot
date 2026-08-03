import type { CSSProperties, ReactElement } from "react";

/**
 * The "Thinking" label, with a sweep across the glyphs while the model works.
 *
 * WHY THIS EXISTS
 * ---------------
 * Between pressing send and the first token the transcript rendered NOTHING.
 * On a reasoning model that is routinely 3–6 seconds of blank column (measured:
 * 5.16s on gpt-5.6-luna, 2.80s on claude-sonnet-5, on a trivial prompt with no
 * tools), and the only signal anything was happening lived in the composer.
 *
 * WHY THE STYLES ARE HERE AND NOT IN A STYLESHEET
 * -----------------------------------------------
 * `ReasoningGroup` — the pre-existing thought-process accordion — is styled
 * from `apps/frontend/src/styles.css`, which the desktop host never loads. It
 * therefore renders unstyled on the surface that matters. This component owns
 * its own appearance: inline styles for everything static, and one scoped
 * `<style>` for the keyframes, which cannot be expressed inline. That makes it
 * correct on both hosts without either of them having to know it exists.
 */

/** Keyframes + the reduced-motion fallback. Emitted once per mount; identical
 *  bytes, so duplicates are inert. */
const SHIMMER_CSS = `
@keyframes cs-thinking-sweep {
  from { background-position: 180% 0; }
  to   { background-position: -80% 0; }
}
.cs-thinking__label {
  background: linear-gradient(
    100deg,
    var(--color-text-subtle, #64646d) 20%,
    var(--color-text, #ececf1) 42%,
    var(--color-text, #ececf1) 50%,
    var(--color-text-subtle, #64646d) 72%
  );
  background-size: 260% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: cs-thinking-sweep 2.1s linear infinite;
}
/* A shimmer must DEGRADE, not vanish: hold the bright end so the label still
   reads as active for anyone who asked the OS to stop motion. */
@media (prefers-reduced-motion: reduce) {
  .cs-thinking__label {
    animation: none;
    background: none;
    -webkit-text-fill-color: var(--color-text-muted, #98989f);
    color: var(--color-text-muted, #98989f);
  }
}
`;

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  fontSize: "14.5px",
  lineHeight: 1.55,
  letterSpacing: "0.005em",
};

export interface ThinkingShimmerProps {
  /**
   * Optional detail after the word — "Thinking: Calculating probabilities".
   *
   * Providers put a bold title on the first line of a reasoning summary
   * (OpenAI's Responses summaries open with `**Calculating probabilities**`),
   * so `reasoningTitle` below lifts it out. Absent → the bare word, which is
   * the honest thing to show when the model has told us nothing yet.
   */
  readonly detail?: string | null;
  /** Overrides the word itself, for waits that are not thinking (see below). */
  readonly label?: string;
}

export function ThinkingShimmer({
  detail,
  label = "Thinking",
}: ThinkingShimmerProps): ReactElement {
  return (
    <span
      style={rowStyle}
      className="cs-thinking"
      data-testid="cs-thinking"
      // The label is a live region: it appears without user action and its
      // text changes as the run progresses. `polite` so it never interrupts.
      aria-live="polite"
    >
      <style>{SHIMMER_CSS}</style>
      <span className="cs-thinking__label">
        {detail ? `${label}: ${detail}` : label}
      </span>
    </span>
  );
}

const summaryStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  cursor: "pointer",
  listStyle: "none",
  userSelect: "none",
  padding: "1px 0",
};

const bodyStyle: CSSProperties = {
  marginTop: 6,
  paddingLeft: 11,
  borderLeft: "1px solid var(--color-border, rgba(255,255,255,0.06))",
  color: "var(--color-text-muted, #98989f)",
  fontSize: "13.5px",
  lineHeight: 1.6,
};

const settledLabelStyle: CSSProperties = {
  color: "var(--color-text-muted, #98989f)",
  fontSize: "14.5px",
};

const chevronStyle: CSSProperties = {
  color: "var(--color-text-subtle, #64646d)",
  fontSize: "10px",
  transform: "translateY(1px)",
};

export interface ThinkingBlockProps {
  /** The reasoning prose. May be empty while the first delta is in flight. */
  readonly text: string;
  readonly running: boolean;
  /** Seconds the span took; omitted or 0 hides the stamp. */
  readonly elapsedSeconds?: number;
  readonly children?: ReactElement | null;
}

/**
 * One reasoning span: a shimmering header you can expand.
 *
 * Collapsed by DEFAULT, running or not. Thinking is context, not the answer —
 * a span that expands itself pushes the reply down the column every time the
 * model pauses to think, which is the opposite of what the reader wants. The
 * header alone carries the signal ("it is working"); the prose is there for
 * anyone who asks.
 *
 * A native `<details>` rather than a button + state: it is keyboard-operable
 * (Enter/Space), announced as a disclosure by screen readers, and survives
 * re-render without the component owning open/closed state that would snap
 * shut on every streamed delta.
 */
export function ThinkingBlock({
  text,
  running,
  elapsedSeconds = 0,
  children,
}: ThinkingBlockProps): ReactElement {
  const detail = running ? reasoningTitle(text) : null;
  return (
    <details
      className="cs-thinking-block"
      data-testid="cs-thinking-block"
      data-status={running ? "running" : "complete"}
    >
      <style>{`.cs-thinking-block > summary::-webkit-details-marker{display:none}`}</style>
      <summary style={summaryStyle}>
        {running ? (
          <ThinkingShimmer detail={detail} />
        ) : (
          <span style={settledLabelStyle}>
            {elapsedSeconds > 0
              ? `Thought for ${elapsedSeconds}s`
              : "Thought process"}
          </span>
        )}
        <span style={chevronStyle} aria-hidden="true">
          ▾
        </span>
      </summary>
      <div style={bodyStyle}>{children}</div>
    </details>
  );
}

/**
 * Lift the bold title off the front of a reasoning summary.
 *
 * OpenAI's Responses summaries are shaped `**Calculating probabilities**\n\n…`
 * — verified against a live response — so the first bold line is a
 * model-authored headline for the span, which is exactly what a collapsed
 * disclosure wants. Everything else is the body.
 *
 * Returns `null` when the text has no such header (Anthropic's summaries do
 * not use one), because inventing a title from the first sentence would put
 * words in the model's mouth.
 */
export function reasoningTitle(text: string): string | null {
  const match = text.trim().match(/^\*\*([^*\n]+)\*\*(?:\r?\n|$)/);
  return match ? match[1].trim() : null;
}
