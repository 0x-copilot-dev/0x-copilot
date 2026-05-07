// PR 1.5 — subagent reducer for the Workspace pane Agents tab.
//
// Subagents are conversation-scoped (the Agents tab spans every run). A
// snapshot is keyed by `task_id`, seeded from `GET .../subagents` on
// conversation open, and incrementally updated when a `SUBAGENT_*` event
// arrives on the live SSE stream.

import type {
  RuntimeEventEnvelope,
  SubagentEntry,
  SubagentLifecycleStatus,
} from "@enterprise-search/api-types";

export type SubagentSnapshotMap = ReadonlyMap<string, SubagentEntry>;

const SUBAGENT_STARTED = "subagent_started";
const SUBAGENT_PROGRESS = "subagent_progress";
const SUBAGENT_COMPLETED = "subagent_completed";
const SUBAGENT_PAUSED = "subagent_paused";
const SUBAGENT_RESUMED = "subagent_resumed";

// `paused` is intentionally NOT a running state — fleet-row "is anything
// running" checks should classify a paused subagent as not running so the
// progress bar freezes and the chrome flips to amber.
const RUNNING_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "running",
]);

// Statuses that a `subagent_resumed` event is allowed to flip back to
// `running`. Terminal states win — a completed/cancelled/failed subagent
// stays terminal even if a stray resume arrives (e.g. mid-replay).
const RESUMABLE_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "paused",
]);

export function emptySubagentMap(): SubagentSnapshotMap {
  return new Map();
}

export function seedSubagentMap(
  entries: readonly SubagentEntry[],
): SubagentSnapshotMap {
  return new Map(entries.map((entry) => [entry.task_id, entry]));
}

export function applySubagentEvent(
  current: SubagentSnapshotMap,
  event: RuntimeEventEnvelope,
): SubagentSnapshotMap {
  const taskId = event.task_id;
  if (!taskId) {
    return current;
  }
  if (event.source !== "subagent") {
    return current;
  }
  const projected = projectEvent(current.get(taskId), event);
  if (projected === undefined) {
    return current;
  }
  if (projected === current.get(taskId)) {
    return current;
  }
  const next = new Map(current);
  next.set(taskId, projected);
  return next;
}

export function isRunningStatus(status: SubagentLifecycleStatus): boolean {
  return RUNNING_STATES.has(status);
}

export function subagentsByRecency(
  current: SubagentSnapshotMap,
): readonly SubagentEntry[] {
  return [...current.values()].sort(byRecency);
}

function projectEvent(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry | undefined {
  switch (event.event_type) {
    case SUBAGENT_STARTED:
      return onStarted(current, event);
    case SUBAGENT_PROGRESS:
      return onProgress(current, event);
    case SUBAGENT_COMPLETED:
      return onCompleted(current, event);
    case SUBAGENT_PAUSED:
      return onPaused(current, event);
    case SUBAGENT_RESUMED:
      return onResumed(current, event);
    default:
      return undefined;
  }
}

function onStarted(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
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
): SubagentEntry {
  if (current === undefined) {
    return {
      ...seedFromEvent(event),
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
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
  const completedAt = event.created_at;
  const startedAt = base.started_at;
  const duration =
    payloadDurationMs(event) ?? durationFromStarted(startedAt, completedAt);
  return {
    ...base,
    status: terminalStatus(event),
    completed_at: completedAt,
    duration_ms: duration,
    result_summary: event.summary ?? base.result_summary,
    // PR 3.2.7 — terminal wins over paused. Clear the pause hints so a
    // cancelled-from-paused row doesn't render amber chrome.
    pause_reason: null,
    pause_source_event_id: null,
  };
}

function onPaused(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
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
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
  if (
    base.status === "running" &&
    !base.pause_reason &&
    !base.pause_source_event_id
  ) {
    return base;
  }
  if (!RESUMABLE_STATES.has(base.status)) {
    return base;
  }
  return {
    ...base,
    status: "running",
    pause_reason: null,
    pause_source_event_id: null,
  };
}

function seedFromEvent(event: RuntimeEventEnvelope): SubagentEntry {
  return {
    task_id: event.task_id ?? "",
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
    // Token usage is rolled up server-side from runtime_model_call_usage on
    // the seed read; the live event projection has no access to per-call
    // usage rows, so it stays null until the next conversation re-seed.
    token_usage: null,
    pause_reason: null,
    pause_source_event_id: null,
  };
}

const PAUSE_REASONS: ReadonlySet<string> = new Set([
  "approval",
  "mcp_auth",
  "ask_a_question",
]);

function pauseReasonFromPayload(
  event: RuntimeEventEnvelope,
): SubagentEntry["pause_reason"] {
  const payload = event.payload;
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return null;
  }
  const raw = (payload as Record<string, unknown>).reason;
  if (typeof raw !== "string" || !PAUSE_REASONS.has(raw)) {
    return null;
  }
  return raw as Exclude<SubagentEntry["pause_reason"], null | undefined>;
}

function pauseSourceEventIdFromPayload(
  event: RuntimeEventEnvelope,
): string | null {
  const payload = event.payload;
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return null;
  }
  const raw = (payload as Record<string, unknown>).source_event_id;
  if (typeof raw !== "string" || raw.trim().length === 0) {
    return null;
  }
  return raw;
}

function subagentName(event: RuntimeEventEnvelope): string | null {
  if (event.subagent_id && event.subagent_id.trim()) {
    return event.subagent_id.trim();
  }
  const payload = event.payload;
  if (
    payload &&
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    typeof (payload as Record<string, unknown>).subagent_name === "string"
  ) {
    const raw = (payload as Record<string, string>).subagent_name.trim();
    return raw.length > 0 ? raw : null;
  }
  return null;
}

function terminalStatus(event: RuntimeEventEnvelope): SubagentLifecycleStatus {
  const raw = (event.status ?? "").toLowerCase();
  if (raw === "cancelled") {
    return "cancelled";
  }
  if (raw === "failed") {
    return "failed";
  }
  return "completed";
}

function payloadDurationMs(event: RuntimeEventEnvelope): number | null {
  const payload = event.payload;
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return null;
  }
  const candidate = (payload as Record<string, unknown>).duration_ms;
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

function byRecency(left: SubagentEntry, right: SubagentEntry): number {
  return recencyValue(right) - recencyValue(left);
}

function recencyValue(entry: SubagentEntry): number {
  const completed = entry.completed_at ? Date.parse(entry.completed_at) : NaN;
  if (!Number.isNaN(completed)) {
    return completed;
  }
  const started = entry.started_at ? Date.parse(entry.started_at) : NaN;
  if (!Number.isNaN(started)) {
    return started;
  }
  return -Number.MAX_SAFE_INTEGER;
}
