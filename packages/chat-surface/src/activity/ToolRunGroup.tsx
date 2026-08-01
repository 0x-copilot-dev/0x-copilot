// ToolRunGroup — one collapsible line for a run of activity cards (PRD-03).
//
// Source: docs/plan/windowed-mode/PRD-03-transcript-density.md
//
// `ReasoningGroup` is the shipped precedent this generalises: a native
// `<details>`, collapsed by default, with a status-driven label flip and a
// synthesised elapsed stamp. It does the same job for reasoning parts that this
// does for tool calls.
//
// ⚠️ ONE THING IT DELIBERATELY DOES NOT COPY. `ReasoningGroup`'s own comment
// says its CSS "lives in the host substrate (apps/frontend/src/styles.css)",
// and it does — `.aui-reasoning-group` is defined only in the WEB host, so the
// component is unstyled on desktop. That is the stranded-CSS failure this repo
// has already paid for (PR #459). Every rule this component needs ships in the
// scoped `<style>` below, inside the package, and a test asserts no host
// stylesheet re-declares its class names (FR-3.11).
//
// Chrome is `activityCardChrome` — the same frame, padding, tile and meta scale
// the tool and subagent cards use — so a group reads as the same UI system as
// the cards inside it rather than as a new one.

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

import {
  activityCardFrameStyle,
  activityCardHeaderStyle,
  activityCardTileStyle,
} from "./ActivityCardChrome";

/** Matches `GroupRunState` in `thread-canvas/groupActivity.ts`. */
export type ToolRunGroupState = "running" | "settled" | "failed";

export interface ToolRunGroupProps {
  readonly state: ToolRunGroupState;
  /** Settled member count, for the running label's `N of M`. */
  readonly done: number;
  readonly total: number;
  /** Members whose failure the run recovered from; drives the muted suffix. */
  readonly retried?: number;
  /** Pre-formatted elapsed (PRD-07's single formatter). `null` → omitted. */
  readonly elapsed?: string | null;
  /** The member cards. */
  readonly children?: ReactNode;
  /**
   * Narrow surface. Passed through by the caller from the shell width class;
   * the group itself only uses it to keep the label short.
   */
  readonly compact?: boolean;
  readonly id?: string;
}

/**
 * Compose the summary line.
 *
 * The label is the only place the group states anything, so it has to be honest
 * in every sub-state: "Working" while anything is in flight, and never
 * "Worked for" over a run that failed.
 */
function summaryLabel(props: {
  state: ToolRunGroupState;
  done: number;
  total: number;
  elapsed: string | null;
  compact: boolean;
}): string {
  const { state, done, total, elapsed, compact } = props;
  const steps = `${total} step${total === 1 ? "" : "s"}`;
  if (state === "running") {
    return compact ? `${done}/${total}` : `Working · ${done} of ${total}`;
  }
  if (state === "failed") {
    return elapsed !== null
      ? `Failed after ${elapsed} · ${steps}`
      : `Failed · ${steps}`;
  }
  return elapsed !== null ? `Worked for ${elapsed} · ${steps}` : steps;
}

export function ToolRunGroup({
  state,
  done,
  total,
  retried = 0,
  elapsed = null,
  children,
  compact = false,
  id,
}: ToolRunGroupProps): ReactElement {
  // D-3.3 — a manual toggle pins the group for the session. Auto-collapse only
  // ever applies to a group the reader has not touched; a transcript that
  // re-closes something you deliberately opened is hostile.
  const [pinned, setPinned] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement | null>(null);

  // D-3.2 — expanded while working (you watch it happen), collapsed once it
  // settles (you read the answer). D-3.5 — a FAILED run stays open: you need
  // the detail, and hiding it behind a click is the wrong default.
  const autoOpen = state === "running" || state === "failed";

  useEffect(() => {
    const el = detailsRef.current;
    if (el === null || pinned) {
      return;
    }
    // FR-3.7 — never collapse out from under a reader whose focus is inside.
    if (el.contains(el.ownerDocument.activeElement)) {
      return;
    }
    if (el.open !== autoOpen) {
      el.open = autoOpen;
    }
  }, [autoOpen, pinned]);

  // Pin on the user's ACTUAL interaction, not on `toggle`.
  //
  // `<details>` fires `toggle` for any change to `open`, including the
  // programmatic write above — so pinning from `onToggle` made the auto-expand
  // at run start mark the group as user-pinned, and it then never
  // auto-collapsed. The live journey caught exactly that
  // (`state: settled, open: true, pinned: true`); jsdom does not fire `toggle`
  // on a property write, so the unit test could not reproduce it.
  //
  // A "did I write this?" flag would work but can go stale in any environment
  // that does not deliver the event. A click / Enter / Space on the summary is
  // unambiguously the reader, and no programmatic path can forge it.
  const pin = useCallback((): void => {
    setPinned(true);
  }, []);
  const handleSummaryKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (event.key === "Enter" || event.key === " ") {
        setPinned(true);
      }
    },
    [],
  );

  const label = summaryLabel({ state, done, total, elapsed, compact });

  return (
    <details
      ref={detailsRef}
      id={id}
      className="cs-run-group"
      data-testid="tool-run-group"
      data-state={state}
      data-pinned={pinned ? "true" : "false"}
      style={activityCardFrameStyle}
    >
      <style>{TOOL_RUN_GROUP_CSS}</style>
      <summary
        className="cs-run-group__head"
        style={activityCardHeaderStyle}
        data-testid="tool-run-group-summary"
        onClick={pin}
        onKeyDown={handleSummaryKeyDown}
      >
        <span style={activityCardTileStyle} aria-hidden="true">
          {state === "running" ? (
            <span className="cs-run-group__spinner" style={spinnerStyle} />
          ) : (
            "⚙"
          )}
        </span>
        <span style={labelStyle} data-testid="tool-run-group-label">
          {label}
        </span>
        {/* PRD-04 territory: the red lives inside the group, not on it. This is
            a muted count so a recovered run reads as history, not an alarm. */}
        {retried > 0 && state !== "running" ? (
          <span style={retriedStyle} data-testid="tool-run-group-retried">
            {retried} retried
          </span>
        ) : null}
        <span
          className="cs-run-group__chevron"
          style={chevronStyle}
          aria-hidden="true"
        >
          ▾
        </span>
      </summary>
      <div className="cs-run-group__body" style={bodyStyle}>
        {children}
      </div>
    </details>
  );
}

// ===========================================================================
// Styles — tokens only, and ALL of them ship here (FR-3.11)
// ===========================================================================

const labelStyle: CSSProperties = {
  flex: "1 1 auto",
  minWidth: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  // One rung quieter than the answer below it — the group is a signpost, not
  // the content (FR-3.10 asserts this ordering).
  color: "var(--color-text-muted)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const retriedStyle: CSSProperties = {
  flex: "none",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  color: "var(--color-text-subtle)",
};

const chevronStyle: CSSProperties = {
  flex: "0 0 auto",
  width: 10,
  fontSize: 11,
  lineHeight: 1,
  color: "var(--color-text-subtle)",
};

const bodyStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  padding: 6,
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const spinnerStyle: CSSProperties = {
  width: 11,
  height: 11,
  border: "1.5px solid var(--color-border-strong)",
  borderTopColor: "var(--color-accent)",
  borderRadius: "50%",
  boxSizing: "border-box",
};

const TOOL_RUN_GROUP_CSS = `
.cs-run-group > .cs-run-group__head { cursor: pointer; user-select: none; list-style: none; }
.cs-run-group > .cs-run-group__head::-webkit-details-marker { display: none; }
.cs-run-group > .cs-run-group__head::marker { content: ""; }
.cs-run-group > .cs-run-group__head:hover { background: var(--color-surface-muted); }
.cs-run-group > .cs-run-group__head:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
.cs-run-group > .cs-run-group__head .cs-run-group__chevron {
  transition: transform 120ms ease;
}
.cs-run-group[open] > .cs-run-group__head .cs-run-group__chevron {
  transform: rotate(180deg);
}
/* The member cards drop their own frame — one border per group, not seven. */
.cs-run-group__body > * > .tc-activity-card,
.cs-run-group__body > .tc-activity-card {
  border-color: transparent;
  background: transparent;
}
@keyframes cs-run-group-spin { to { transform: rotate(360deg); } }
.cs-run-group__spinner { animation: cs-run-group-spin 0.7s linear infinite; }
[data-reduce-motion="1"] .cs-run-group__spinner,
[data-reduce-motion="always"] .cs-run-group__spinner { animation: none; }
[data-reduce-motion="always"] .cs-run-group__chevron { transition: none; }
@media (prefers-reduced-motion: reduce) {
  .cs-run-group__spinner { animation: none; }
  .cs-run-group > .cs-run-group__head .cs-run-group__chevron { transition: none; }
}
`;
