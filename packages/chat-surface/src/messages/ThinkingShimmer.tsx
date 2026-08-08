import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

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

/**
 * THE ONE TYPOGRAPHIC RULE IN THIS FILE: thinking chrome steps DOWN from the
 * answer, never up.
 *
 * The transcript body is `--font-size-sm` (13px — TcChat's container, and the
 * rung the design's `body{font-size:13px}` anchors on). This header used to be
 * a hardcoded `14.5px` and the body prose `13.5px`, so the two quietest things
 * in the column — chrome above the answer, and a thought the reader did not ask
 * to see — were the LARGEST text in it, and the answer itself the smallest.
 * Both now sit on `--font-size-xs` (12.5px), one rung below the answer.
 *
 * `--font-size-xs` and not a hand-picked number: the ladder is the contract,
 * and a fourth off-ladder size in a column that already has three is how the
 * scale drifted in the first place.
 */
const CHROME_SIZE = "var(--font-size-xs)";

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  fontSize: CHROME_SIZE,
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

/* Negative inline margin so the hover/focus plate below can have real padding
   without the label shifting off the text column it heads. */
const summaryStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  cursor: "pointer",
  listStyle: "none",
  userSelect: "none",
  padding: "3px 6px",
  margin: "0 -6px",
  borderRadius: 6,
  width: "fit-content",
};

const bodyStyle: CSSProperties = {
  marginTop: 6,
  paddingLeft: 11,
  borderLeft: "1px solid var(--color-border, rgba(255,255,255,0.06))",
  color: "var(--color-text-muted, #98989f)",
  fontSize: CHROME_SIZE,
  lineHeight: 1.6,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

/** The absorbed tool/fleet cards. A `<ul>` because the card renderers each
 *  return a keyed `<li>` — reusing them unchanged keeps ONE tool card in the
 *  product, and keeps this list valid HTML. */
const activityListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const settledLabelStyle: CSSProperties = {
  color: "var(--color-text-muted, #98989f)",
  fontSize: CHROME_SIZE,
};

/** The step count riding next to the label. Deliberately NOT inside the
 *  shimmer: a number that pulses is noise, and this one is the only thing
 *  telling the reader that work is hidden under a collapsed row. */
const stepsStyle: CSSProperties = {
  color: "var(--color-text-subtle, #64646d)",
  fontSize: CHROME_SIZE,
  whiteSpace: "nowrap",
};

const failedStepsStyle: CSSProperties = {
  ...stepsStyle,
  color: "var(--color-danger, #f0764f)",
};

export interface ThinkingBlockProps {
  /** The reasoning prose. May be empty while the first delta is in flight. */
  readonly text: string;
  readonly running: boolean;
  /** Seconds the span took; omitted or 0 hides the stamp. */
  readonly elapsedSeconds?: number;
  readonly children?: ReactNode;
  /**
   * Tool / fleet cards the model produced INSIDE this reasoning span — the
   * work it did while thinking, rather than a second disclosure stacked under
   * the first. Supplied already-rendered by `TcChat`, which owns transcript
   * ordering and the card renderers; this component only frames them.
   */
  readonly activity?: ReactNode;
  /** How many cards `activity` holds. Drives the header's `· N steps`. */
  readonly stepCount?: number;
  /** How many of them failed. Drives the header's red `· N failed`. */
  readonly failedCount?: number;
  /**
   * Any absorbed card still in flight. With `failedCount` this is what decides
   * auto-expansion — see the note on `autoOpen` below.
   */
  readonly activityRunning?: boolean;
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
  activity,
  stepCount = 0,
  failedCount = 0,
  activityRunning = false,
}: ThinkingBlockProps): ReactElement {
  const detail = running ? reasoningTitle(text) : null;
  const detailsRef = useRef<HTMLDetailsElement | null>(null);
  const [pinned, setPinned] = useState(false);

  /**
   * Collapsed by default for PROSE; open for LIVE OR FAILED WORK.
   *
   * Those are two rules that used to live in two components, and folding the
   * tool cards in here put them in one. Both survive, because each still
   * applies to what it was written about:
   *
   * - reasoning prose stays collapsed even while it streams (a span that
   *   expands itself shoves the reply down the column every time the model
   *   pauses to think — the reason this block exists);
   * - activity does NOT (`ToolRunGroup` D-3.2 "expanded while working, you
   *   watch it happen", D-3.5 "a failed run stays open").
   *
   * Absorbing the cards without carrying D-3.2/D-3.5 across would have buried
   * a failing tool call behind a collapsed row that says only "Thought for 6s"
   * — trading a stacked label for a silently hidden failure, which is a much
   * worse bug than the one being fixed.
   */
  const autoOpen = failedCount > 0 || activityRunning;

  useEffect(() => {
    const el = detailsRef.current;
    if (el === null || pinned) return;
    // Never collapse out from under a reader whose focus is inside (FR-3.7).
    if (el.contains(el.ownerDocument.activeElement)) return;
    if (el.open !== autoOpen) el.open = autoOpen;
  }, [autoOpen, pinned]);

  // Pin on the reader's ACTUAL interaction, never on `toggle` — `<details>`
  // fires `toggle` for the programmatic write above too, so pinning from it
  // makes the auto-expand at run start read as user intent and the row then
  // never auto-collapses. Same trap `ToolRunGroup` documents; jsdom does not
  // fire `toggle` on a property write, so no unit test would catch it.
  const pin = useCallback((): void => setPinned(true), []);
  const handleSummaryKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (event.key === "Enter" || event.key === " ") setPinned(true);
    },
    [],
  );

  const steps =
    stepCount > 0 ? `${stepCount} step${stepCount === 1 ? "" : "s"}` : null;

  return (
    <details
      ref={detailsRef}
      className="cs-thinking-block"
      data-testid="cs-thinking-block"
      data-status={running ? "running" : "complete"}
      data-steps={stepCount}
      data-pinned={pinned ? "true" : "false"}
      style={blockStyle}
    >
      <style>{THINKING_BLOCK_CSS}</style>
      <summary
        style={summaryStyle}
        onClick={pin}
        onKeyDown={handleSummaryKeyDown}
      >
        {running ? (
          <ThinkingShimmer detail={detail} />
        ) : (
          <span style={settledLabelStyle}>
            {elapsedSeconds > 0
              ? `Thought for ${elapsedSeconds}s`
              : "Thought process"}
          </span>
        )}
        {/* The count is not decoration: it is the only thing that tells a
            reader work is folded under a collapsed row. Without it, absorbing
            the tool cards would BE hiding them. */}
        {steps !== null ? (
          <span style={stepsStyle} data-testid="cs-thinking-block-steps">
            · {steps}
          </span>
        ) : null}
        {failedCount > 0 ? (
          <span style={failedStepsStyle} data-testid="cs-thinking-block-failed">
            · {failedCount} failed
          </span>
        ) : null}
        {/* A real icon, not the `▾` character. As text it was locked to a 10px
            font-size against a 12.5px label — the smallest glyph in the column
            standing in for the only affordance this control has — and it
            inherited the text baseline, so it needed a magic 1px nudge to sit
            straight. An SVG scales with the row and rotates on open. */}
        <svg
          className="cs-thinking-block__chevron"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </summary>
      <div style={bodyStyle}>
        {children}
        {activity !== undefined && activity !== null ? (
          <ul
            style={activityListStyle}
            data-testid="cs-thinking-block-activity"
          >
            {activity}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

/** Breathing room under the thought, so the answer is not glued to it.
 *
 *  It belongs HERE rather than on the answer: the two are siblings in one
 *  turn's `<li>` when the parts share a message and separate `<li>`s when they
 *  are seq-split, so a rule written from the answer's side lands in only one of
 *  those layouts. `TcChat` gives the same-`<li>` case a matching column gap;
 *  between them the boundary reads the same in both paths. */
const blockStyle: CSSProperties = {
  marginBottom: 2,
};

const THINKING_BLOCK_CSS = `
.cs-thinking-block > summary::-webkit-details-marker { display: none; }
.cs-thinking-block > summary::marker { content: ""; }
.cs-thinking-block > summary:hover { background: var(--color-surface-muted); }
.cs-thinking-block > summary:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
.cs-thinking-block__chevron {
  flex: none;
  width: 13px;
  height: 13px;
  opacity: 0.75;
  color: var(--color-text-subtle, #64646d);
  transition: transform 120ms ease;
}
.cs-thinking-block[open] > summary .cs-thinking-block__chevron {
  transform: rotate(180deg);
}
[data-reduce-motion="always"] .cs-thinking-block__chevron { transition: none; }
@media (prefers-reduced-motion: reduce) {
  .cs-thinking-block__chevron { transition: none; }
}
`;

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
