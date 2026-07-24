// Desktop renderer feature flags.
//
// The renderer is APP code (not `@0x-copilot/chat-surface`, which bans
// `localStorage`), so a small localStorage read is the sanctioned flag
// mechanism here — mirroring `apps/frontend/src/app/featureFlags.ts`.

/** localStorage key for the Generative Surfaces v2 opt-out (shared with web). */
export const SURFACES_V2_FLAG_KEY = "enterprise.flags.surfaces-v2";

/**
 * Whether the Generative Surfaces v2 canvas mounts in the desktop Run cockpit
 * (PRD-B1). **PRD-E3 flipped it ON by default** (opt-out), matching the server
 * default flip: v2 now owns surface emission. OFF iff
 * `localStorage["enterprise.flags.surfaces-v2"] === "false"` — the explicit kill
 * switch / rollback. Read live so a devtools toggle takes effect on the next
 * mount; anything other than an explicit "false" (stale value, garbage, storage
 * error) fails toward the new ON default.
 */
export function isSurfacesV2Enabled(): boolean {
  try {
    return globalThis.localStorage?.getItem(SURFACES_V2_FLAG_KEY) !== "false";
  } catch {
    return true;
  }
}
