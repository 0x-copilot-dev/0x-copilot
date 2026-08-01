// Filesystem bypass — the client half of the three-tier decision (PRD-FS-10 §4.3).
//
// The AUTHORITY lives in the backend: `agent_runtime.execution.filesystem_bypass`
// folds master ▸ run ▸ message and seals the answer onto the run. Nothing here
// can grant anything. What lives here is (a) the wire vocabulary, which must
// stay byte-identical to the Python `StrEnum` values, and (b) the two questions
// the composer has to answer locally:
//
//   * may the control be OFFERED at all (master switch);
//   * after a send, does the selection persist or is it spent (scope).
//
// The second one is the only place "run" and "message" scope actually differ in
// this runtime — a run executes exactly one user turn, so both bind the same
// run's effects. `message` is one-shot; `run` is sticky until the user changes
// it. Keeping that in ONE function stops each host inventing its own idea of
// when the pill resets.

/** Wire vocabulary — mirrors Python `FilesystemBypassMode`. */
export type FilesystemBypassMode = "manual" | "bypass";

/** Wire vocabulary — mirrors Python `FilesystemBypassScope`. */
export type FilesystemBypassScope = "message" | "run";

/**
 * What the composer sends. Both tiers in one object, `undefined` meaning "not
 * selected" — which is deliberately NOT the same as `"manual"`, because an
 * explicit per-message Manual has to be able to override a sticky run bypass.
 */
export interface FilesystemBypassSelection {
  readonly run?: FilesystemBypassMode;
  readonly message?: FilesystemBypassMode;
}

/** The composer's live bypass state: what the user picked, and at what scope. */
export interface FilesystemBypassState {
  readonly mode: FilesystemBypassMode;
  readonly scope: FilesystemBypassScope;
}

/** The state a composer starts in, and returns to when a selection is spent. */
export const MANUAL_BYPASS_STATE: FilesystemBypassState = {
  mode: "manual",
  scope: "message",
};

/**
 * Map the composer's state onto the run-create wire field.
 *
 * Returns `undefined` when there is nothing to send — master off, or Manual at
 * message scope (the default posture). Omitting rather than sending
 * `{message:"manual"}` keeps a plain send byte-identical to what it was before
 * bypass existed, so a host that never surfaces the pill changes no payload.
 *
 * An explicit Manual at RUN scope IS sent: "this run does not bypass" is a real
 * statement when the alternative is a sticky run bypass, and the backend
 * distinguishes it from absence.
 */
export function bypassSelectionForSend(
  state: FilesystemBypassState,
  { masterEnabled }: { readonly masterEnabled: boolean },
): FilesystemBypassSelection | undefined {
  if (!masterEnabled) return undefined;
  if (state.mode === "manual" && state.scope === "message") return undefined;
  return state.scope === "run" ? { run: state.mode } : { message: state.mode };
}

/**
 * The state the composer should hold AFTER a successful send.
 *
 * Message scope is spent — the pill returns to Manual so a one-turn bypass
 * cannot silently become a standing one. Run scope persists. This is the whole
 * observable difference between the two scopes on the client, and it is the
 * reason the pill offers a scope at all.
 */
export function bypassStateAfterSend(
  state: FilesystemBypassState,
): FilesystemBypassState {
  return state.scope === "run" ? state : MANUAL_BYPASS_STATE;
}
