// Web host surface-renderer registration (PRD-05).
//
// Mirrors the desktop bootstrap (`apps/desktop/renderer/bootstrap.tsx`), which
// registers the SAME renderer stack at app init:
//
//   1. `registerGenericStructuredDiff()` — the tier-3, always-matches generic
//      structured-diff fallback. Any surface whose scheme no tier-1/archetype
//      adapter claims still renders (never a raw JSON dump).
//   2. `registerSurfaceRenderers()` (`surface-renderers` `registerAll`) — the
//      tier-1 SaaS adapters (email / salesforce / sheets / slides) PLUS the
//      PRD-03 archetype adapters (record / table / message / doc / board).
//
// Deliberately NO Tier2Bridge: sandboxed tier-2 adapters are a desktop-only IPC
// concern today (main → renderer install/uninstall/mark-broken pushes); web
// tier-2 arrives in PRD-10. Wiring the desktop bridge here would drag Electron
// IPC into the web bundle — the exact asymmetry this module does NOT reintroduce.
//
// Idempotent by construction: `SurfaceRegistry.registerAdapter` REPLACES an
// existing entry of the same `{scheme, version}` in place (the tier-2 hot-swap
// path), so a double invocation — React StrictMode's double-mount, a Vite HMR
// re-import — leaves exactly one adapter per scheme, never a duplicate.

import { registerGenericStructuredDiff } from "@0x-copilot/chat-surface";
import { registerAll as registerSurfaceRenderers } from "@0x-copilot/surface-renderers";

/**
 * Register the web host's full surface-renderer stack (tier-3 generic + tier-1
 * SaaS + PRD-03 archetypes). Call once at app init; safe to call again (registry
 * replace semantics). See the module comment for why there is no Tier2Bridge.
 */
export function registerSurfaces(): void {
  registerGenericStructuredDiff();
  registerSurfaceRenderers();
}
