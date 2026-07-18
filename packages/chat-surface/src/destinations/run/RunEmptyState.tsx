// RunEmptyState — the Run cockpit's empty/idle state (PR-3.11).
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md — US-3.8 / FR-3.25
//   "The Run empty/idle state MUST render a goal composer with a
//    'Give it a goal…' prompt when no active run exists (no blank canvas /
//    placeholder string), and starting a goal MUST transition to the live
//    layout without shell remount."
//
// Ownership: RunEmptyState is presentation only. It renders the honest
// empty-cockpit copy + a goal composer, and calls `onSubmitGoal(goal)` when the
// user starts a run (Enter without Shift, or the Start button). The
// RunDestination shell owns what "start" does: it POSTs the run through the
// Transport port and binds the freshly-created `runId` back into `useRunSession`
// via the `runId` seam — so the empty→live transition swaps this state for the
// live layout WITHOUT unmounting the shell (FR-3.25). This component never
// fabricates a fake run; when there is nothing to show, it says so and offers
// the one action that starts real work.
//
// Boundary: framework-agnostic. No bare window/document/fetch/localStorage
// (FR-3.27) — the only side effect is the `onSubmitGoal` callback.

import {
  useCallback,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

const DEFAULT_AGENT_NAME = "the agent";

export interface RunEmptyStateProps {
  /** Agent display name — woven into the honest empty copy. */
  readonly agentName?: string;
  /**
   * Fired with the trimmed goal when the user starts a run. The shell turns
   * this into a real run and rebinds the cockpit to it (FR-3.25). Never called
   * with an empty/whitespace goal.
   */
  readonly onSubmitGoal: (goal: string) => void;
  /**
   * `true` while the shell is creating the run (the POST is in flight). Disables
   * the composer and flips the button to its "Starting…" label so a double
   * submit can't spawn two runs.
   */
  readonly submitting?: boolean;
}

export function RunEmptyState(props: RunEmptyStateProps): ReactElement {
  const {
    agentName = DEFAULT_AGENT_NAME,
    onSubmitGoal,
    submitting = false,
  } = props;

  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();
  const canSubmit = trimmed !== "" && !submitting;

  const submit = useCallback((): void => {
    const goal = draft.trim();
    if (goal === "" || submitting) {
      return;
    }
    onSubmitGoal(goal);
  }, [draft, submitting, onSubmitGoal]);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>): void => {
      event.preventDefault();
      submit();
    },
    [submit],
  );

  // Enter starts the run; Shift+Enter inserts a newline (composer convention).
  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>): void => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submit();
      }
    },
    [submit],
  );

  return (
    <div data-testid="run-empty-state" style={rootStyle}>
      <div style={cardStyle}>
        <span data-testid="run-empty-kicker" style={kickerStyle}>
          NO ACTIVE RUN
        </span>
        <h2 data-testid="run-empty-title" style={titleStyle}>
          Give it a goal
        </h2>
        <p data-testid="run-empty-prompt" style={promptStyle}>
          Describe what you want done — {agentName} will plan the steps, work
          across your files and connected apps, and pause for your approval
          before it acts.
        </p>

        <form
          data-testid="run-empty-form"
          style={formStyle}
          onSubmit={handleSubmit}
        >
          <textarea
            data-testid="run-empty-goal-input"
            style={textareaStyle}
            aria-label={`Goal for ${agentName}`}
            placeholder="Give it a goal…"
            rows={3}
            value={draft}
            disabled={submitting}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div style={actionsRowStyle}>
            <span data-testid="run-empty-hint" style={hintStyle}>
              ↵ start · ⇧+↵ new line
            </span>
            <button
              type="submit"
              data-testid="run-empty-submit"
              style={submitButtonStyle(canSubmit)}
              disabled={!canSubmit}
            >
              {submitting ? "Starting…" : "Start run"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ============================================================
// Styles (design-system tokens only — sky accent, no lime)
// ============================================================

const rootStyle: CSSProperties = {
  height: "100%",
  width: "100%",
  minHeight: 0,
  display: "grid",
  placeItems: "center",
  padding: 24,
  background: "var(--color-bg, #0e1015)",
  color: "var(--color-text, #f4f5f6)",
  fontFamily: "var(--font-sans)",
};

const cardStyle: CSSProperties = {
  width: "100%",
  maxWidth: 560,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 24,
  borderRadius: 14,
  background: "var(--color-bg-elevated, #16181f)",
  border: "1px solid var(--color-border, #22252e)",
};

const kickerStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display, var(--font-sans))",
  fontSize: "var(--font-size-lg, 19px)",
  fontWeight: 600,
  letterSpacing: "-0.01em",
  color: "var(--color-text, #f4f5f6)",
};

const promptStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.5,
  color: "var(--color-text-muted, #9aa0a6)",
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  marginTop: 6,
};

const textareaStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  resize: "vertical",
  minHeight: 68,
  padding: "10px 12px",
  borderRadius: 10,
  background: "var(--color-bg, #0e1015)",
  color: "var(--color-text, #f4f5f6)",
  border: "1px solid var(--color-border, #2a2d31)",
  fontFamily: "inherit",
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.5,
  outline: "none",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const hintStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const submitButtonStyle = (enabled: boolean): CSSProperties => ({
  flexShrink: 0,
  background: enabled ? "var(--color-accent, #5fb2ec)" : "transparent",
  color: enabled
    ? "var(--color-accent-contrast, #08131d)"
    : "var(--color-text-subtle, #7e7e84)",
  border: enabled
    ? "1px solid var(--color-accent, #5fb2ec)"
    : "1px solid var(--color-border, #2a2d31)",
  borderRadius: 8,
  padding: "6px 16px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
  fontFamily: "inherit",
});
