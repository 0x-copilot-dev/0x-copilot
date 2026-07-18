// Phase 1 (PR-1.5) subagent / fleet presentation family.
//
// Hoisted from apps/frontend so web and desktop render multi-agent runs
// identically. Pure presentation + a substrate-portable view-model adapter;
// the host keeps the data-binding (reducers, activity builders, fleet
// context, jump-to-approval wiring) and passes normalised data in as props.

export { SubagentCard, type SubagentCardProps } from "./SubagentCard";
export {
  FleetSubagentRow,
  type FleetSubagentRowProps,
} from "./FleetSubagentRow";
export {
  SubagentFleetCard,
  type SubagentFleetCardProps,
} from "./SubagentFleetCard";
// PR-3.8 — pure selector projecting subagent + fleet state off the single
// run event stream (FR-3.17 / FR-3.3). No subscription, no second projector.
export {
  projectSubagents,
  type FleetProjection,
  type SubagentProjection,
} from "./subagentProjection";
export {
  subagentCardFromArgs,
  subagentCardFromEntry,
  type SubagentCardStatus,
  type SubagentCardViewModel,
  type SubagentPauseReason,
} from "./subagentCardViewModel";
export {
  formatSubagentDuration,
  pauseAriaLabel,
  pauseFullLabel,
  pauseJumpLabel,
  pauseShortLabel,
} from "./labels";
export { ActivityStatusIcon } from "./ActivityStatusIcon";
export { SubagentActivityList } from "./SubagentActivityList";
export { useElapsedSeconds } from "./useElapsedSeconds";
// The status/text helper functions (statusClassification, activityTitle, …)
// stay internal to the family — components import them directly from
// `./subagentHelpers`. Only the record TYPE is re-exported (desktop consumers
// and host callsites shape their activity arrays against it).
export { type SubagentActivityRecord } from "./subagentHelpers";
