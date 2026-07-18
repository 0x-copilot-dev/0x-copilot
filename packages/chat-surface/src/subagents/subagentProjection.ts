// PR-3.8 — parallel-subagent projection off the SINGLE run event stream.
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md FR-3.17 (parallel
// subagents render as (a) inline `SubagentFleetCard`, (b) one live timeline
// lane per subagent, (c) live Agents-tab count — all from ONE projection) +
// FR-3.3 (single projection, no second subscription).
//
// This is a PURE selector over the canonical `RuntimeEventEnvelope[]` that
// `useRunSession` owns (the same array `ThreadCanvas` feeds to
// `useEventProjector`). It opens NO SSE subscription and instantiates NO
// second `useEventProjector`; `RunDestination` memoizes it against
// `session.events` and threads the result into the two consumers that live
// OUTSIDE `ThreadCanvas` — the inline fleet card in `TcChat` (a) and the
// Agents-tab count in `RunWorkspaceRail` (c). The per-subagent timeline lanes
// (b) come from `TcSwimlanes`' own incremental stream (PRD §5 / risk R4), so
// they are not re-derived here.
//
// Behavior parity: the per-task reduction mirrors the host-owned
// `apps/frontend/.../chatModel/subagentReducer.ts` (kept host-side per
// FR-1.25); the small status helpers it needs are already reproduced
// substrate-portably in `./subagentHelpers`. The fleet grouping mirrors the
// host's `SubagentFleetTool` head-count derivation (live child state is
// authoritative; `agent_ids.length` is the advisory fallback total).

import type {
  RuntimeEventEnvelope,
  SubagentEntry,
  SubagentLifecycleStatus,
} from "@0x-copilot/api-types";

import {
  isTerminalStatus,
  normaliseLifecycleStatus,
  stringValue,
} from "./subagentHelpers";

/** Conversation-scoped subagent snapshot keyed by `task_id`. Structurally
 *  identical to `workspace`'s `SubagentSnapshotMap` so it flows straight into
 *  `RunWorkspaceRail.subagents` without a cross-family import. */
export type SubagentSnapshotMap = ReadonlyMap<string, SubagentEntry>;

/** One dispatched parallel batch, projected for the inline `SubagentFleetCard`. */
export interface FleetProjection {
  readonly fleetId: string;
  readonly title: string;
  readonly sub: string | null;
  /** Declared child agent ids from the `subagent_fleet_started` payload. */
  readonly agentIds: readonly string[];
  /** Head count: live children when we have them, else the declared count. */
  readonly total: number;
  /** In-flight children (queued / running / paused — i.e. not terminal). */
  readonly running: number;
  /** Terminal children. */
  readonly done: number;
  /** Wall-clock elapsed, from `subagent_fleet_finished`; null while running. */
  readonly elapsed: string | null;
  /** True once `subagent_fleet_finished` has arrived for this fleet. */
  readonly finished: boolean;
  /** `sequence_no` of the dispatch event — the fleet's conversation anchor. */
  readonly sequenceNo: number;
  /** `created_at` of the dispatch event in epoch ms (null if unparseable). */
  readonly createdAtMs: number | null;
  /** Child subagents grouped under this fleet, in dispatch order. */
  readonly children: readonly SubagentEntry[];
}

export interface SubagentProjection {
  /** Every subagent seen on the stream (fleet children AND standalone). */
  readonly subagents: SubagentSnapshotMap;
  /** Dispatched fleets, in the order their `subagent_fleet_started` arrived. */
  readonly fleets: readonly FleetProjection[];
}

const EMPTY_PROJECTION: SubagentProjection = {
  subagents: new Map(),
  fleets: [],
};

const SUBAGENT_FLEET_STARTED = "subagent_fleet_started";
const SUBAGENT_FLEET_FINISHED = "subagent_fleet_finished";
const SUBAGENT_STARTED = "subagent_started";
const SUBAGENT_PROGRESS = "subagent_progress";
const SUBAGENT_COMPLETED = "subagent_completed";
const SUBAGENT_PAUSED = "subagent_paused";
const SUBAGENT_RESUMED = "subagent_resumed";

interface MutableFleet {
  fleetId: string;
  title: string;
  sub: string | null;
  agentIds: readonly string[];
  elapsed: string | null;
  finished: boolean;
  sequenceNo: number;
  createdAtMs: number | null;
}

/**
 * Reduce the ordered run event list into subagent + fleet state.
 *
 * Idempotent on replay (deduplicates by `event_id`). Callers pass events in
 * ascending `sequence_no` order — the same append-only array `useRunSession`
 * exposes — so a single `useMemo(() => projectSubagents(events), [events])`
 * recomputes only when the stream grows.
 */
export function projectSubagents(
  events: readonly RuntimeEventEnvelope[],
): SubagentProjection {
  if (events.length === 0) {
    return EMPTY_PROJECTION;
  }

  const seen = new Set<string>();
  const subagents = new Map<string, SubagentEntry>();
  const parentFleetByTask = new Map<string, string>();
  const fleetHeads = new Map<string, MutableFleet>();
  const fleetOrder: string[] = [];

  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);

    switch (event.event_type) {
      case SUBAGENT_FLEET_STARTED:
        reduceFleetStarted(event, fleetHeads, fleetOrder);
        break;
      case SUBAGENT_FLEET_FINISHED:
        reduceFleetFinished(event, fleetHeads);
        break;
      case SUBAGENT_STARTED:
      case SUBAGENT_PROGRESS:
      case SUBAGENT_COMPLETED:
      case SUBAGENT_PAUSED:
      case SUBAGENT_RESUMED:
        reduceChild(event, subagents, parentFleetByTask);
        break;
      default:
        break;
    }
  }

  const fleets = fleetOrder.map((fleetId) =>
    buildFleet(fleetHeads.get(fleetId)!, subagents, parentFleetByTask),
  );

  return { subagents, fleets };
}

// --- fleet bookends --------------------------------------------------------

function reduceFleetStarted(
  event: RuntimeEventEnvelope,
  heads: Map<string, MutableFleet>,
  order: string[],
): void {
  const fleetId = stringValue(event.payload.fleet_id);
  if (fleetId === null) {
    return;
  }
  const existing = heads.get(fleetId);
  if (existing === undefined) {
    order.push(fleetId);
  }
  heads.set(fleetId, {
    fleetId,
    title:
      stringValue(event.payload.title) ??
      existing?.title ??
      event.display_title ??
      "Subagents working in parallel",
    sub: stringValue(event.payload.sub) ?? existing?.sub ?? null,
    agentIds:
      readStringArray(event.payload.agent_ids) ?? existing?.agentIds ?? [],
    elapsed: existing?.elapsed ?? null,
    finished: existing?.finished ?? false,
    sequenceNo: existing?.sequenceNo ?? event.sequence_no,
    createdAtMs: existing?.createdAtMs ?? parseMs(event.created_at),
  });
}

function reduceFleetFinished(
  event: RuntimeEventEnvelope,
  heads: Map<string, MutableFleet>,
): void {
  const fleetId = stringValue(event.payload.fleet_id);
  if (fleetId === null) {
    return;
  }
  const existing = heads.get(fleetId);
  if (existing === undefined) {
    return;
  }
  heads.set(fleetId, {
    ...existing,
    elapsed: stringValue(event.payload.elapsed) ?? existing.elapsed,
    finished: true,
  });
}

function buildFleet(
  head: MutableFleet,
  subagents: ReadonlyMap<string, SubagentEntry>,
  parentFleetByTask: ReadonlyMap<string, string>,
): FleetProjection {
  const children: SubagentEntry[] = [];
  for (const [taskId, fleetId] of parentFleetByTask) {
    if (fleetId !== head.fleetId) {
      continue;
    }
    const entry = subagents.get(taskId);
    if (entry !== undefined) {
      children.push(entry);
    }
  }
  children.sort(byStartOrder);

  let running = 0;
  let done = 0;
  for (const child of children) {
    if (isTerminalStatus(child.status)) {
      done += 1;
    } else {
      running += 1;
    }
  }
  // Live child state is authoritative (mirrors SubagentFleetTool AC-7); the
  // declared `agent_ids.length` is only the fallback before children arrive.
  const total = children.length > 0 ? children.length : head.agentIds.length;
  if (children.length === 0 && head.finished) {
    done = total;
  }

  return {
    fleetId: head.fleetId,
    title: head.title,
    sub: head.sub,
    agentIds: head.agentIds,
    total,
    running,
    done,
    elapsed: head.elapsed,
    finished: head.finished,
    sequenceNo: head.sequenceNo,
    createdAtMs: head.createdAtMs,
    children,
  };
}

function byStartOrder(left: SubagentEntry, right: SubagentEntry): number {
  const l = left.started_at ? Date.parse(left.started_at) : NaN;
  const r = right.started_at ? Date.parse(right.started_at) : NaN;
  const lv = Number.isNaN(l) ? Number.MAX_SAFE_INTEGER : l;
  const rv = Number.isNaN(r) ? Number.MAX_SAFE_INTEGER : r;
  return lv - rv;
}

// --- per-task lifecycle (parity with host subagentReducer) -----------------

function reduceChild(
  event: RuntimeEventEnvelope,
  subagents: Map<string, SubagentEntry>,
  parentFleetByTask: Map<string, string>,
): void {
  const taskId = stringValue(event.task_id);
  if (taskId === null) {
    return;
  }
  // The worker tags child lifecycle frames with `source: "subagent"`; drop
  // any look-alike from another source (parity with `applySubagentEvent`).
  if (event.source !== undefined && event.source !== "subagent") {
    return;
  }
  const fleetId = stringValue(event.payload.parent_fleet_id);
  if (fleetId !== null && !parentFleetByTask.has(taskId)) {
    // Preserve once-set: a late PROGRESS / COMPLETED without the field must
    // not blank the grouping (mirrors partFactories.ts).
    parentFleetByTask.set(taskId, fleetId);
  }

  const projected = projectChildEvent(subagents.get(taskId), event, taskId);
  if (projected !== undefined) {
    subagents.set(taskId, projected);
  }
}

function projectChildEvent(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry | undefined {
  switch (event.event_type) {
    case SUBAGENT_STARTED:
      return onStarted(current, event, taskId);
    case SUBAGENT_PROGRESS:
      return onProgress(current, event, taskId);
    case SUBAGENT_COMPLETED:
      return onCompleted(current, event, taskId);
    case SUBAGENT_PAUSED:
      return onPaused(current, event, taskId);
    case SUBAGENT_RESUMED:
      return onResumed(current, event, taskId);
    default:
      return undefined;
  }
}

function onStarted(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  const base = current ?? seedFromEvent(event, taskId);
  return {
    ...base,
    subagent_name: subagentName(event) ?? base.subagent_name,
    status: "running",
    started_at: base.started_at ?? event.created_at,
    objective_summary: event.summary ?? base.objective_summary,
    display_title: event.display_title ?? base.display_title,
  };
}

function onProgress(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  if (current === undefined) {
    return {
      ...seedFromEvent(event, taskId),
      display_title: event.display_title ?? null,
    };
  }
  const nextDisplay = event.display_title ?? current.display_title;
  if (nextDisplay === current.display_title && current.status === "running") {
    return current;
  }
  return { ...current, display_title: nextDisplay, status: "running" };
}

function onCompleted(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  const base = current ?? seedFromEvent(event, taskId);
  const completedAt = event.created_at;
  const duration =
    payloadDurationMs(event) ??
    durationFromStarted(base.started_at, completedAt);
  return {
    ...base,
    status: normaliseTerminalStatus(event.status),
    completed_at: completedAt,
    duration_ms: duration,
    result_summary: event.summary ?? base.result_summary,
    // Terminal wins over paused — clear the pause hints.
    pause_reason: null,
    pause_source_event_id: null,
  };
}

function onPaused(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  const base = current ?? seedFromEvent(event, taskId);
  const reason = pauseReasonFromPayload(event);
  const sourceEventId = pauseSourceEventIdFromPayload(event);
  if (
    base.status === "paused" &&
    base.pause_reason === reason &&
    base.pause_source_event_id === sourceEventId
  ) {
    return base;
  }
  return {
    ...base,
    status: "paused",
    pause_reason: reason,
    pause_source_event_id: sourceEventId,
  };
}

function onResumed(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  const base = current ?? seedFromEvent(event, taskId);
  if (
    base.status === "running" &&
    !base.pause_reason &&
    !base.pause_source_event_id
  ) {
    return base;
  }
  if (!isResumableStatus(base.status)) {
    return base;
  }
  return {
    ...base,
    status: "running",
    pause_reason: null,
    pause_source_event_id: null,
  };
}

function seedFromEvent(
  event: RuntimeEventEnvelope,
  taskId: string,
): SubagentEntry {
  return {
    task_id: taskId,
    parent_run_id: event.run_id,
    subagent_name: subagentName(event) ?? "subagent",
    status: "running",
    display_title: event.display_title ?? null,
    objective_summary: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    safe_error_code: null,
    safe_error_message: null,
    // The live event projection has no per-call usage rows; usage stays null
    // until the next conversation re-seed (parity with the host reducer).
    token_usage: null,
    pause_reason: null,
    pause_source_event_id: null,
  };
}

// --- status helpers (parity with host subagentStatus) ----------------------

const RESUMABLE_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "paused",
]);

function isResumableStatus(status: SubagentLifecycleStatus): boolean {
  return RESUMABLE_STATES.has(status);
}

function normaliseTerminalStatus(
  raw: string | null | undefined,
): SubagentLifecycleStatus {
  return normaliseLifecycleStatus(raw, false, "completed");
}

// --- payload readers -------------------------------------------------------

const PAUSE_REASONS: ReadonlySet<string> = new Set([
  "approval",
  "mcp_auth",
  "ask_a_question",
]);

function pauseReasonFromPayload(
  event: RuntimeEventEnvelope,
): SubagentEntry["pause_reason"] {
  const raw = event.payload.reason;
  if (typeof raw !== "string" || !PAUSE_REASONS.has(raw)) {
    return null;
  }
  return raw as Exclude<SubagentEntry["pause_reason"], null | undefined>;
}

function pauseSourceEventIdFromPayload(
  event: RuntimeEventEnvelope,
): string | null {
  const raw = event.payload.source_event_id;
  if (typeof raw !== "string" || raw.trim().length === 0) {
    return null;
  }
  return raw;
}

function subagentName(event: RuntimeEventEnvelope): string | null {
  if (event.subagent_id && event.subagent_id.trim()) {
    return event.subagent_id.trim();
  }
  return stringValue(event.payload.subagent_name);
}

function payloadDurationMs(event: RuntimeEventEnvelope): number | null {
  const candidate = event.payload.duration_ms;
  if (
    typeof candidate === "number" &&
    Number.isFinite(candidate) &&
    candidate >= 0
  ) {
    return Math.round(candidate);
  }
  return null;
}

function durationFromStarted(
  startedAt: string | null,
  completedAt: string,
): number | null {
  if (startedAt === null) {
    return null;
  }
  const started = Date.parse(startedAt);
  const completed = Date.parse(completedAt);
  if (Number.isNaN(started) || Number.isNaN(completed)) {
    return null;
  }
  return Math.max(0, completed - started);
}

function readStringArray(value: unknown): readonly string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim().length > 0) {
      out.push(item);
    }
  }
  return out;
}

function parseMs(iso: string): number | null {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : parsed;
}
