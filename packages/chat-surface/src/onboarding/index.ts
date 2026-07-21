// First-Run onboarding (FTUE) subtree barrel — the public surface of
// `packages/chat-surface/src/onboarding/`. Re-exported from the package root
// (`src/index.ts`) under the `=== First-Run onboarding (P1) ===` block. Hosts
// import the CSS separately: `@0x-copilot/chat-surface/src/onboarding/onboarding.css`.

export { FirstRunSurface } from "./FirstRunSurface";
export type {
  FirstRunSurfaceProps,
  FirstRunComposerCtx,
  FirstRunAckCtx,
  FirstRunLocalCardCtx,
} from "./FirstRunSurface";

export { Gate } from "./Gate";
export type { GateProps } from "./Gate";

export { KeyForm } from "./KeyForm";
export type { KeyFormProps, KeyFormConnected } from "./KeyForm";

export {
  FIRST_RUN_COPY,
  FIRST_RUN_KEY_PROVIDERS,
  checkFirstRunKeyFormat,
} from "./firstRun";
export type {
  FirstRunStage,
  FirstRunEngine,
  FirstRunStore,
  FirstRunKeyProvider,
  FirstRunCompleteReason,
} from "./firstRun";
