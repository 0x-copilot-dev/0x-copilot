// App-layer feature flags (PRD-05).
//
// This is APP code, not `@0x-copilot/chat-surface` (which is browser-primitive
// free and bans `localStorage`). A small localStorage-read constant is the
// sanctioned flag mechanism here — the app has no other flag registry yet, and
// the PRD explicitly permits it.
//
// `runCockpitWeb` gates the web `run` slug. **WC-P7: flipped ON by default** —
// the real `RunDestination` cockpit (`features/run/RunRoute`) is now the web
// `run` surface. It is the single-source-of-truth interaction layer shared with
// the desktop app, so converging web onto it collapses two run surfaces into
// one. The web-convergence program (WC-P0…P6) closed the 6 MUST-FIX parity gaps
// vs the legacy `ChatScreen` (turn-N composer, reopen/new-chat nav, cancel,
// MCP-OAuth mid-run, optimistic echo) before this flip.
//
// The legacy `ChatScreen` stays in the tree as an INSTANT ROLLBACK (AD-13):
// two independent, fail-safe OPT-OUTS force it back under the `run` slug —
//   - build-time: `VITE_RUN_COCKPIT_WEB=false` (statically inlined by Vite);
//   - runtime:    `localStorage["enterprise.flags.run-cockpit-web"] === "false"`.
// Anything other than an explicit "false" keeps the cockpit ON (so a storage
// failure or an absent value fails toward the new default, not the legacy path).

/** localStorage key for the runtime `runCockpitWeb` opt-out. */
export const RUN_COCKPIT_WEB_FLAG_KEY = "enterprise.flags.run-cockpit-web";

/** Build-time opt-out: `VITE_RUN_COCKPIT_WEB=false` rolls back to ChatScreen. */
function readEnvOptOut(): boolean {
  const value =
    typeof import.meta !== "undefined"
      ? import.meta.env?.VITE_RUN_COCKPIT_WEB
      : undefined;
  return value === "false" || value === false;
}

/** Runtime opt-out: the localStorage key set to the string "false". */
function readLocalStorageOptOut(key: string): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      window.localStorage.getItem(key) === "false"
    );
  } catch {
    // Private mode / storage disabled → no opt-out signal → cockpit stays ON.
    return false;
  }
}

/**
 * Whether the real Run cockpit (`RunDestination`) mounts under the web `run`
 * slug. **Default ON (WC-P7).** Read live (not a module constant) so a devtools
 * opt-out toggle or a per-test seed takes effect on the next `CopilotApp` mount
 * without a rebuild. Only an explicit "false" (env or localStorage) rolls back
 * to the legacy `ChatScreen`.
 */
export function isRunCockpitWebEnabled(): boolean {
  return !readEnvOptOut() && !readLocalStorageOptOut(RUN_COCKPIT_WEB_FLAG_KEY);
}

// ---------------------------------------------------------------------------
// Generative Surfaces v2 canvas (PRD-B1) — the CLIENT side of the runtime
// `SURFACES_V2` flag. **PRD-E3 flipped it ON by default** (opt-out), matching
// the server default flip and the `runCockpitWeb` fail-toward-ON pattern: v2 now
// owns surface emission (the v1 `result["surface"]` appendage was retired), so
// the canvas mounts unless explicitly disabled. Two independent, fail-safe
// OPT-OUTS force it off:
//   - build-time: `VITE_SURFACES_V2=false` (statically inlined by Vite);
//   - runtime:    `localStorage["enterprise.flags.surfaces-v2"] === "false"`.
// Anything other than an explicit "false" keeps the canvas ON (so a stale opt-in
// value, garbage, or a storage failure fails toward the new default).
// ---------------------------------------------------------------------------

/** localStorage key for the runtime `surfacesV2` opt-out. */
export const SURFACES_V2_FLAG_KEY = "enterprise.flags.surfaces-v2";

/** Build-time opt-out: `VITE_SURFACES_V2=false` rolls back to no v2 canvas. */
function readEnvSurfacesV2OptOut(): boolean {
  const value =
    typeof import.meta !== "undefined"
      ? import.meta.env?.VITE_SURFACES_V2
      : undefined;
  return value === "false" || value === false;
}

/**
 * Whether the Generative Surfaces v2 canvas mounts in the web Run cockpit.
 * **Default ON (E3).** OFF iff `VITE_SURFACES_V2 === "false"` OR
 * `localStorage["enterprise.flags.surfaces-v2"] === "false"`. Read live (not a
 * module constant) so a devtools opt-out toggle / per-test seed takes effect on
 * the next `RunDestination` mount without a rebuild. Reuses the shared
 * `readLocalStorageOptOut` (the same fail-toward-ON reader `runCockpitWeb` uses).
 */
export function isSurfacesV2CanvasEnabled(): boolean {
  return (
    !readEnvSurfacesV2OptOut() && !readLocalStorageOptOut(SURFACES_V2_FLAG_KEY)
  );
}
