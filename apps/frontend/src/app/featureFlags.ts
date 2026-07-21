// App-layer feature flags (PRD-05).
//
// This is APP code, not `@0x-copilot/chat-surface` (which is browser-primitive
// free and bans `localStorage`). A small localStorage-read constant is the
// sanctioned Wave-1 flag mechanism here — the app has no other flag registry
// yet, and the PRD explicitly permits it.
//
// `runCockpitWeb` gates the web `run` slug: OFF (default) keeps the legacy
// `ChatScreen`; ON mounts the real `RunDestination` cockpit (`features/run/
// RunRoute`). Default OFF — flipping it is a product decision after Wave-1
// verification (PRD-05 §Non-goals). Two independent opt-ins, both fail-safe:
//   - build-time: `VITE_RUN_COCKPIT_WEB=true` (statically inlined by Vite);
//   - runtime:    `localStorage["enterprise.flags.run-cockpit-web"] === "true"`.

/** localStorage key for the runtime `runCockpitWeb` opt-in. */
export const RUN_COCKPIT_WEB_FLAG_KEY = "enterprise.flags.run-cockpit-web";

function readEnvFlag(): boolean {
  const value =
    typeof import.meta !== "undefined"
      ? import.meta.env?.VITE_RUN_COCKPIT_WEB
      : undefined;
  return value === "true" || value === true;
}

function readLocalStorageFlag(key: string): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      window.localStorage.getItem(key) === "true"
    );
  } catch {
    // Private mode / storage disabled → treat the flag as off (fail-safe).
    return false;
  }
}

/**
 * Whether the real Run cockpit (`RunDestination`) should replace the legacy
 * `ChatScreen` under the web `run` slug. Read live (not a module constant) so a
 * devtools toggle or a per-test seed takes effect on the next `CopilotApp`
 * mount without a rebuild.
 */
export function isRunCockpitWebEnabled(): boolean {
  return readEnvFlag() || readLocalStorageFlag(RUN_COCKPIT_WEB_FLAG_KEY);
}
