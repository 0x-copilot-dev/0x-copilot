// Section-component barrel for the Home destination (Phase 9 rewrite).
//
// Each section file is the canonical home for its presentation logic.
// `<HomeDestination>` composes these in fixed §3.1 order.

export { HomeGreeting, type HomeGreetingProps } from "./HomeGreeting";
export { TriageStrip, type TriageStripProps } from "./TriageStrip";
export { TodayTimeline, type TodayTimelineProps } from "./TodayTimeline";
export { WhatsNewDigest, type WhatsNewDigestProps } from "./WhatsNewDigest";
export { InFlightStrip, type InFlightStripProps } from "./InFlightStrip";
export {
  LiveActivityRail,
  type LiveActivityRailProps,
} from "./LiveActivityRail";
export {
  HomeQuickActionsSection,
  type HomeQuickActionsSectionProps,
} from "./HomeQuickActionsSection";
