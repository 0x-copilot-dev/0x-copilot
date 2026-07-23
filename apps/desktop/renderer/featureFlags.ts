// Desktop renderer feature flags.
//
// The renderer is APP code (not `@0x-copilot/chat-surface`, which bans
// `localStorage`), so a small localStorage read is the sanctioned flag
// mechanism here — mirroring `apps/frontend/src/app/featureFlags.ts`.

/** localStorage key for the Generative Surfaces v2 opt-in (shared with web). */
export const SURFACES_V2_FLAG_KEY = "enterprise.flags.surfaces-v2";

/**
 * Whether the Generative Surfaces v2 canvas mounts in the desktop Run cockpit
 * (PRD-B1). **Default OFF.** ON iff
 * `localStorage["enterprise.flags.surfaces-v2"] === "true"` — a Wave-B
 * dev/preview toggle, enabled together with the runtime `SURFACES_V2` flag.
 * Read live so a devtools toggle takes effect on the next mount; any storage
 * error fails safe to OFF.
 */
export function isSurfacesV2Enabled(): boolean {
  try {
    return globalThis.localStorage?.getItem(SURFACES_V2_FLAG_KEY) === "true";
  } catch {
    return false;
  }
}
