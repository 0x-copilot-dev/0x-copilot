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

/**
 * Structured failure from the last start attempt. `message` is the primary,
 * actionable line — the facade `safe_message` when present (e.g. "Missing API
 * key for model provider 'openai'. Add one in Settings -> Provider keys."),
 * never the raw transport envelope. `code` lets the composer branch (a
 * `configuration_error` shows an "Add a provider key" CTA). `correlationId` /
 * `raw` are demoted behind a "Show details" disclosure so a support reference
 * stays recoverable without dumping the envelope into the primary message.
 */
export interface StartRunError {
  readonly message: string;
  readonly code?: string;
  readonly correlationId?: string;
  readonly raw?: string;
}

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
  /**
   * Structured failure from the last start attempt (run POST 4xx/5xx, a
   * transport error, …). Rendered inline so a failed start is never silent, with
   * an "Add a provider key" CTA on a configuration error and a demoted
   * "Show details" disclosure. `null`/undefined = no error.
   */
  readonly error?: StartRunError | null;
  /**
   * `true` when NO model provider is configured (no BYOK key and no local
   * model). The composer is disabled and a "Set up your model" CTA is shown
   * instead of letting the user start a run that is guaranteed to fail with a
   * configuration error. The host binder computes this from the
   * provider-keys / local-models readiness probe.
   */
  readonly setupRequired?: boolean;
  /**
   * Open Settings → Provider keys. Wired by the host binder; when absent (e.g. a
   * substrate with no settings surface) the setup / error CTAs are hidden but
   * the honest copy still shows.
   */
  readonly onOpenModelSettings?: () => void;
}

export function RunEmptyState(props: RunEmptyStateProps): ReactElement {
  const {
    agentName = DEFAULT_AGENT_NAME,
    onSubmitGoal,
    submitting = false,
    error = null,
    setupRequired = false,
    onOpenModelSettings,
  } = props;

  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();
  // Not ready to run: mid-flight, or no model provider configured yet. Either
  // way the composer stays inert so a doomed start can't fire.
  const composerLocked = submitting || setupRequired;
  const canSubmit = trimmed !== "" && !composerLocked;

  const submit = useCallback((): void => {
    const goal = draft.trim();
    if (goal === "" || submitting || setupRequired) {
      return;
    }
    onSubmitGoal(goal);
  }, [draft, submitting, setupRequired, onSubmitGoal]);

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

        {/* Readiness gate (FR-1.x): no provider key + no local model → say so
            and offer the one action that unblocks a run, rather than letting the
            user start a run that fails with a configuration error. */}
        {setupRequired ? (
          <div
            data-testid="run-empty-setup"
            style={setupNoticeStyle}
            role="note"
          >
            <p style={setupTextStyle}>
              Before {agentName} can run, connect a model — a cloud API key
              (OpenAI, Anthropic, Google) or a local model. It takes a minute.
            </p>
            {onOpenModelSettings !== undefined ? (
              <button
                type="button"
                data-testid="run-empty-setup-cta"
                style={ctaButtonStyle}
                onClick={onOpenModelSettings}
              >
                Set up your model
              </button>
            ) : null}
          </div>
        ) : null}

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
            disabled={composerLocked}
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
          {error !== null && error !== undefined ? (
            <div
              data-testid="run-empty-error"
              style={errorBlockStyle}
              role="alert"
            >
              <p
                data-testid="run-empty-error-message"
                style={errorMessageStyle}
              >
                {error.message}
              </p>
              {error.code === "configuration_error" &&
              onOpenModelSettings !== undefined ? (
                <button
                  type="button"
                  data-testid="run-empty-error-cta"
                  style={ctaButtonStyle}
                  onClick={onOpenModelSettings}
                >
                  Add a provider key
                </button>
              ) : null}
              {error.correlationId !== undefined ||
              (error.raw !== undefined &&
                error.raw !== "" &&
                error.raw !== error.message) ? (
                <details style={detailsStyle}>
                  <summary
                    data-testid="run-empty-error-details-toggle"
                    style={detailsSummaryStyle}
                  >
                    Show details
                  </summary>
                  <pre
                    data-testid="run-empty-error-details"
                    style={detailsPreStyle}
                  >
                    {error.correlationId !== undefined
                      ? `Reference: ${error.correlationId}\n`
                      : ""}
                    {error.raw ?? ""}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}
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

const errorBlockStyle: CSSProperties = {
  margin: "12px 0 0",
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 8,
};

const errorMessageStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.45,
  color: "var(--color-danger, #e5678a)",
};

// Setup notice — the honest "no model configured yet" state (readiness gate).
const setupNoticeStyle: CSSProperties = {
  marginTop: 4,
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 10,
  padding: "12px 14px",
  borderRadius: 10,
  background: "var(--color-bg, #0e1015)",
  border: "1px solid var(--color-border, #2a2d31)",
};

const setupTextStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.5,
  color: "var(--color-text, #f4f5f6)",
};

// Actionable CTA — the accent-filled button for "Set up your model" /
// "Add a provider key". Shares the accent look with the primary Start button.
const ctaButtonStyle: CSSProperties = {
  alignSelf: "flex-start",
  background: "var(--color-accent, #5fb2ec)",
  color: "var(--color-accent-contrast, #08131d)",
  border: "1px solid var(--color-accent, #5fb2ec)",
  borderRadius: 8,
  padding: "6px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const detailsStyle: CSSProperties = {
  width: "100%",
};

const detailsSummaryStyle: CSSProperties = {
  cursor: "pointer",
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  fontFamily: "var(--font-mono)",
};

const detailsPreStyle: CSSProperties = {
  margin: "6px 0 0",
  padding: "8px 10px",
  maxHeight: 160,
  overflow: "auto",
  borderRadius: 8,
  background: "var(--color-bg, #0e1015)",
  border: "1px solid var(--color-border, #2a2d31)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  lineHeight: 1.4,
  color: "var(--color-text-muted, #9aa0a6)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
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
