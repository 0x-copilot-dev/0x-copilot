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

// === P2 — local-model card + curated preset (SSE download) ===
export { FirstRunLocalCard } from "./FirstRunLocalCard";
export type { FirstRunLocalCardProps } from "./FirstRunLocalCard";
export {
  useFirstRunLocalModel,
  type FirstRunLocalStatus,
  type UseFirstRunLocalModelResult,
  type UseFirstRunLocalModelArgs,
} from "./useFirstRunLocalModel";
export {
  createFirstRunLocalModelsPort,
  type FirstRunLocalModelsPort,
} from "./localModelsPort";
export {
  firstRunModelPillLabel,
  pullPercent,
  resolveInstalledTag,
} from "./localModelEngine";
// === Phase FTUE-P3 — onboarding composer + starter chips + ack + launch ===
export {
  OnboardingComposer,
  ONBOARDING_COMPOSER_COPY,
} from "./OnboardingComposer";
export type { OnboardingComposerProps } from "./OnboardingComposer";
export { SuggestionChips, FIRST_RUN_SUGGESTIONS } from "./SuggestionChips";
export type {
  FirstRunSuggestion,
  SuggestionChipsProps,
} from "./SuggestionChips";
export { Acknowledgment, FIRST_RUN_ACK_TITLES } from "./Acknowledgment";
export type {
  AcknowledgmentProps,
  AcknowledgmentVariant,
} from "./Acknowledgment";
export { firstRunAckLines } from "./firstRunAckLines";
export type {
  FirstRunAckEngine,
  FirstRunToolsState,
  FirstRunAckLines,
} from "./firstRunAckLines";
export { useFirstRunLaunch } from "./useFirstRunLaunch";
export type {
  FirstRunLaunchPhase,
  FirstRunLaunchPayload,
  UseFirstRunLaunch,
  UseFirstRunLaunchOptions,
} from "./useFirstRunLaunch";
export type {
  FirstRunRunsPort,
  FirstRunCreateRunInput,
  FirstRunLaunchResult,
} from "./ports/FirstRunRunsPort";
// === end Phase FTUE-P3 ===

// === First-Run onboarding (P4 — tools popover) ===
export { ToolsPopover, TOOLS_POPOVER_COPY } from "./ToolsPopover";
export type { ToolsPopoverProps } from "./ToolsPopover";
export {
  ComposerToolsButton,
  COMPOSER_TOOLS_BUTTON_COPY,
} from "./ComposerToolsButton";
export type { ComposerToolsButtonProps } from "./ComposerToolsButton";
export {
  projectFirstRunConnectors,
  firstRunActiveToolCount,
} from "./projectFirstRunConnectors";
export type {
  FirstRunConnectorProjection,
  FirstRunConnectedConnector,
  FirstRunInstallableConnector,
} from "./projectFirstRunConnectors";
export type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
// === end First-Run onboarding (P4 — tools popover) ===
