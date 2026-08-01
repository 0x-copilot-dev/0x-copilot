// workspaceDefaultsStore — one observable copy of `/v1/agent/workspace/defaults`.
//
// WHY THIS EXISTS. Seven call sites fetch that endpoint independently
// (SettingsMount ×5, useRunComposerBindings, useDesktopComposerBypass) and each
// keeps its own snapshot. Nothing tells the others when one of them WRITES, so
// a workspace default changed in Settings does not reach a live consumer until
// that consumer happens to remount.
//
// The live symptom, measured by the FS-D journey:
//
//     toggle_before: false -> toggle_after: true      (the setting persisted)
//     master_on_pill:  { disabled: true }             (the pill did not move)
//     master_reached_pill_via: "a renderer reload"
//
// Turning bypass on in Settings left the composer pill disabled until the
// renderer reloaded. `useDesktopComposerBypass` read the switch in a
// `useEffect` keyed on `[transport]` — i.e. exactly once per mount — while its
// own header claimed the switch is "re-read from the server rather than cached
// per-session". Read-once-per-mount IS cached per session; the comment
// described an intent the code did not implement.
//
// WHY NOT A REFETCH HOOK. Re-fetching on focus, or on an interval, fixes the
// bypass pill and leaves the next setting with the same bug — the defect is
// that a change has no way to be *announced*, not that one reader was lazy. So
// the write publishes and readers subscribe: adding a consumer means
// subscribing, not adding an eighth fetch.
//
// Deliberately NOT a React context. The producer (SettingsMount) and the
// consumer (a composer hook) sit in different subtrees and are mounted and
// unmounted independently, so a provider would have to wrap the whole app to
// join them — and the value is a single process-wide fact about this
// workspace, not per-subtree state.

import type { WorkspaceDefaultsResponse } from "@0x-copilot/api-types";

type Listener = (defaults: WorkspaceDefaultsResponse) => void;

const listeners = new Set<Listener>();

/** Last value anyone observed, so a late subscriber is not blind until the
 *  next write. `null` means "nobody has read the endpoint yet" — distinct from
 *  a read that returned defaults, which is why it is not an empty object. */
let latest: WorkspaceDefaultsResponse | null = null;

/**
 * Announce a fresh reading of the workspace defaults.
 *
 * Call this after ANY successful GET or PUT of the endpoint — a PUT response is
 * the most current reading there is, so publishing it is what closes the loop
 * between Settings and every live consumer.
 */
export function publishWorkspaceDefaults(
  defaults: WorkspaceDefaultsResponse,
): void {
  latest = defaults;
  // Copy before iterating: a listener may unsubscribe during dispatch (a
  // component unmounting in response to the very change being announced), and
  // mutating the live Set mid-iteration would silently skip its neighbour.
  for (const listener of [...listeners]) {
    try {
      listener(defaults);
    } catch {
      // One bad subscriber must not deny the rest their update. There is
      // nothing safe to log here — the payload is workspace configuration.
    }
  }
}

/** The most recent reading, or `null` if the endpoint has not been read yet. */
export function currentWorkspaceDefaults(): WorkspaceDefaultsResponse | null {
  return latest;
}

/**
 * Observe workspace defaults. Returns an unsubscribe function.
 *
 * If a value has already been observed the listener is called IMMEDIATELY with
 * it, so a component mounting after the read does not sit on a stale default
 * waiting for the next write that may never come.
 */
export function subscribeWorkspaceDefaults(listener: Listener): () => void {
  listeners.add(listener);
  if (latest !== null) listener(latest);
  return () => {
    listeners.delete(listener);
  };
}

/** Test seam: drop all state so one test cannot leak into the next. */
export function resetWorkspaceDefaultsStore(): void {
  listeners.clear();
  latest = null;
}
