// formatActivityMeta — the SINGLE composer for the Activity row's meta line
// ("4 apps · 7 steps · awaiting 1 approval"), PRD-08 D1.
//
// The counters ride the wire as INTEGERS on `RunHistoryEntry`
// (connector_count / step_count / pending_approval_count); the STRING is built
// here, once, in the shared package so both hosts (web `activityApi`, desktop
// `destinationBinders`) — through the shared `activityProjection` — produce a
// byte-identical line. `api-types/src/activity.ts` states the rule for
// `started_at` ("never pre-formatted on the wire"); the same rule governs the
// meta counters.
//
// Two shapes exist in the design fixture (copilot-data.jsx:600-660): a COUNTER
// TRIPLE (`<apps> · <N> steps · <outcome>`) for healthy rows and a PROSE reason
// for interrupted ones. Only the counter triple is computable from persisted
// fields — the trailing free-text outcome clause ("balanced", "saved to Local
// files", "you rejected 2 of 6 payouts") has no persisted source and is
// explicitly out of scope (PRD-08 Non-goals). This composer therefore emits the
// counter part only; the outcome clause revisits with a real run-summary
// capability.
//
// `null` is NOT `0`: a run recorded before the tool-invocation writer existed
// (D1b) reports `connector_count`/`step_count` as `null` (unknown), and the
// clause is OMITTED rather than asserting "0 steps" about a run that did seven.
// `pending_approval_count` is a plain int (approvals persisted since `0001`), so
// `0` there is a fact — no "awaiting 0 approvals" clause. An all-empty result is
// `""`, so `<Row sub>` renders nothing (never "0 apps · 0 steps").

/** The three integer counters `formatActivityMeta` composes into the line. */
export interface ActivityMetaCounts {
  /** DISTINCT connectors the run called; `null` = unknown (pre-writer run). */
  readonly connector_count: number | null;
  /** Tool invocations attributed to the run; `null` = unknown (pre-writer). */
  readonly step_count: number | null;
  /** Approvals still awaiting a human. A plain int — `0` is a fact, not unknown. */
  readonly pending_approval_count: number;
}

/**
 * Compose the counter triple into the design's meta string, joined by " · ".
 * Empty clauses are omitted; an all-empty result is `""` (the caller renders no
 * sub-line). See the module header for the null-vs-zero contract.
 *
 * Examples (from the design fixture, copilot-data.jsx:606):
 * - `{connector_count:4, step_count:7, pending_approval_count:1}` →
 *   `"4 apps · 7 steps · awaiting 1 approval"`
 * - `{connector_count:null, step_count:null, pending_approval_count:0}` → `""`
 */
export function formatActivityMeta(counts: ActivityMetaCounts): string {
  const { connector_count, step_count, pending_approval_count } = counts;
  const clauses: string[] = [];

  // "N apps" — only when the run touched at least one connector. A resolved 0
  // (a run that used only native, connector-less tools) contributes no "apps"
  // clause: it did steps, not apps — the design's own apps-vs-steps distinction.
  if (connector_count != null && connector_count > 0) {
    clauses.push(
      `${connector_count} ${connector_count === 1 ? "app" : "apps"}`,
    );
  }

  // "N steps" — every tool invocation, retries + sub-agent calls included. `0`
  // is a real (rare) value here and still reads as "0 steps"; `null` (unknown)
  // is omitted.
  if (step_count != null) {
    clauses.push(`${step_count} ${step_count === 1 ? "step" : "steps"}`);
  }

  // "awaiting N approval(s)" — only when a human is currently blocking the run.
  if (pending_approval_count > 0) {
    clauses.push(
      `awaiting ${pending_approval_count} approval${pending_approval_count === 1 ? "" : "s"}`,
    );
  }

  return clauses.join(" · ");
}
