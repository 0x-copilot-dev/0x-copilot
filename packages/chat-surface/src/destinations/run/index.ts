// Run destination — module barrel.
//
// The Run cockpit lives in `packages/chat-surface/src/destinations/run/` and is
// consumed by `apps/desktop` (and, later, web) through the package root
// (`@0x-copilot/chat-surface`). This barrel is the module's single public
// surface: the composition shell (`RunDestination` + `RunHeader`, PR-3.5) and
// the host hooks (`useRunSession` PR-3.3, `useRunMode` PR-3.4) it builds on.

// === PR-3.5 — cockpit shell ===
export { RunDestination, type RunDestinationProps } from "./RunDestination";
export { RunHeader, type RunHeaderProps } from "./RunHeader";

// === PR-3.3 — live run session host hook ===
export {
  useRunSession,
  type RunSession,
  type RunSessionStatus,
  type RunListItem,
  type UseRunSessionOptions,
} from "./useRunSession";

// === PR-3.4 — Studio/Focus mode owner + ⌘M ===
export {
  useRunMode,
  readRunMode,
  writeRunMode,
  runModeKey,
  DEFAULT_RUN_MODE,
  type RunMode,
  type UseRunModeOptions,
  type UseRunModeResult,
} from "./useRunMode";
