// Activity destination — public surface (desktop redesign, Phase 4 · PR-4.5).
//
// Presentational shell only: the host binder (PR-4.6) composes the run
// history and passes it in as a `SectionResult<ActivityRunRow[]>`. Activity
// is a CONSUMER of the `"run"` ItemRef resolver (owned by the Run
// destination, Phase 3) — it registers no resolver of its own.
//
// Wire types (`ActivityRunRow`, `ActivityRunStatus`, `ACTIVITY_RUN_STATUSES`)
// live in `@0x-copilot/api-types` (PR-4.1) and are re-exported here for a
// single import site — the types themselves are NOT redeclared (FR-4.33).

export {
  ActivityDestination,
  activityStatusTone,
  groupActivityByDay,
  ACTIVITY_LEAD_COPY,
  ACTIVITY_RETENTION_LINK_COPY,
  type ActivityDayGroup,
  type ActivityDestinationProps,
} from "./ActivityDestination";

// The shared wire→view-model projection both hosts compose (PRD-04 Seam C).
export {
  projectActivityRows,
  buildMetaIndex,
  mapRunStatus,
} from "./activityProjection";

export type { ActivityRunRow, ActivityRunStatus } from "@0x-copilot/api-types";
export { ACTIVITY_RUN_STATUSES } from "@0x-copilot/api-types";
