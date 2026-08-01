// ONE projector, multiple consumers.
//
// Source: chats-canvas-prd.md §3.2 + §3.8 + §4.2 (binding 2026-05-17).
// The ThreadCanvas mounts ONCE; the four consumers (chat list, swimlane
// timeline, mini-timeline scrubber, surface mount) all read derived state
// from THIS projector. A second projection of `RuntimeEventEnvelope[]`
// elsewhere is a bug — converge here.
//
// Single source of truth. Modes are presentation slots; the projector is
// invariant across modes.
//
// Design constraints:
//
// 1. **Append-only**. Events arrive monotonically by `sequence_no`. The
//    projector is given the full ordered list; downstream consumers may
//    memoize via `useMemo(…, [events.length, lastSequenceNo])`.
// 2. **Idempotent on replay**. Re-projecting the same events MUST yield
//    the same `ProjectedState`. SSE-reconnect resends some events; the
//    projector deduplicates by `event_id`.
// 3. **Time-travel via slice**. `projectAt(events, sequenceNo)` projects
//    only the prefix `events.filter(e => e.sequence_no <= sequenceNo)`.
//    No backend snapshot call — purely client-side reducer per Phase 1
//    Q1 decision (impl-plan §3).
// 4. **Approval-aware**. `approval_requested` payloads flow through
//    `extractApproval()` and land in `approvals[]` indexed by id; the
//    state defaults to `pending` until an `approval_resolved` event flips
//    it. While P1-A's wire `approvals.ts` is in flight, we synthesize
//    Approvals from the runtime event payload using the local stub.
//    TODO(merge): when P1-A ships, swap `_approvals-stub` import to
//    `@0x-copilot/api-types`.
// 5. **Surface-spec merge (PRD-04 / D4)**. Surfaces stream in via the
//    `payload.surface` envelope (`{surface_uri, archetype, state:{spec?,data}}`)
//    on `tool_result` / `draft_updated`; a spec may arrive LATER via
//    `surface_spec_generated` and is merged into `surfaceState[uri].spec` by
//    URI. The merge only ever writes the `spec` key, so a late spec never
//    clobbers newer `data`. `surfaceTabs` is a pure derivation over the same
//    single pass — NOT a second subscription. Legacy flat payloads
//    (`payload.surface_uri` + `payload.state`) are still accepted unchanged.

import type { RuntimeEventEnvelope, SurfaceSpec } from "@0x-copilot/api-types";

// TODO(merge): replace import from "./_approvals-stub" with "@0x-copilot/api-types"
import type { SurfaceHue } from "../surfaces/surfaceHue";
import type { Approval, ApprovalState } from "./_approvals-stub";

/**
 * The projected state every consumer reads from. A consumer picks the
 * slices it needs; it does NOT re-project from `events`.
 */
export interface ProjectedState {
  /** Chronological activity feed — every visible event in order. */
  readonly activity: readonly ActivityEntry[];
  /** Timeline beads — only state-changing events. */
  readonly beads: readonly TimelineBead[];
  /** Chat-side message bubbles + subagent cards in order. */
  readonly chat: readonly ChatEntry[];
  /** Pending + resolved approvals keyed by `Approval.id`. */
  readonly approvals: ReadonlyMap<string, Approval>;
  /** Per-surface latest state — surface-mount reads `surfaceState[uri]`. */
  readonly surfaceState: ReadonlyMap<string, SurfacePayload>;
  /**
   * Surface-tab strip data, ordered by last mutation (`lastSeq` desc) — the
   * cockpit tab strip binds to this. Pure derivation over the single pass; one
   * entry per surface URI (same-URI updates never duplicate). `archetype` is
   * best-effort from the surface envelope / spec; `title` best-effort from the
   * spec's `title_path` resolved against `data`, falling back to the URI tail.
   */
  readonly surfaceTabs: readonly SurfaceTab[];
  /** Highest `sequence_no` we've seen — useful for time-travel cursor. */
  readonly lastSequenceNo: number;
}

/**
 * One surface-tab descriptor. `lastSeq` is the highest `sequence_no` of any
 * event that mutated this surface (its data, spec, or spec-generation), which
 * is what the strip orders by (newest first).
 */
export interface SurfaceTab {
  readonly uri: string;
  readonly archetype?: string;
  readonly title?: string;
  readonly lastSeq: number;
  /**
   * An author-chosen identity hue. Absent for every ledger surface today —
   * only published artifacts carry an accent — so these tabs derive their hue
   * from the URI scheme. Declared here so the tab-list union has one shape at
   * the `TcTab` boundary rather than two that must be narrowed at each use.
   */
  readonly hue?: SurfaceHue;
}

/** Per-URI derivation metadata tracked alongside `surfaceState`. */
interface SurfaceMeta {
  readonly archetype?: string;
  readonly lastSeq: number;
}

/** One row in the right-rail Activity tab + the in-chat Activity entries. */
export interface ActivityEntry {
  readonly id: string;
  readonly sequenceNo: number;
  /** Backend-projected `activity_kind` (root CLAUDE.md rule: don't derive from event_type). */
  readonly kind: string;
  readonly title: string;
  readonly summary?: string;
  readonly status?: string;
  readonly createdAt: string;
  readonly subagentId?: string;
  readonly surfaceUri?: string;
}

/** One bead on the swimlane. */
export interface TimelineBead {
  readonly id: string;
  readonly sequenceNo: number;
  readonly atMs: number;
  readonly lane: string;
  readonly title: string;
  /** True for `approval_requested`-with-pending state; lights up the bead. */
  readonly pending: boolean;
}

/**
 * One chat-side card. Bubbles, streaming deltas, inline diffs, subagent
 * boundary cards all share this shape; the renderer picks the right
 * component via `kind`.
 */
export interface ChatEntry {
  readonly id: string;
  readonly sequenceNo: number;
  readonly kind:
    | "user_message"
    | "assistant_message"
    | "stream_delta"
    | "tool_call"
    | "approval"
    | "subagent_started"
    | "subagent_completed"
    | "system";
  readonly text?: string;
  readonly title?: string;
  readonly approvalId?: string;
  readonly subagentId?: string;
  readonly surfaceUri?: string;
  readonly status?: string;
  readonly createdAt: string;
}

/**
 * One MAIN-AGENT tool call, projected off the SINGLE run event stream for the
 * inline tool-call card in `TcChat`. Collapsed per `call_id`: the
 * `tool_call_started` frame seeds it (`running` + initial args); later
 * `tool_call_delta` frames update the streamed arguments. The matching
 * `tool_result` / `tool_call_completed` frame flips it to `complete` / `error`
 * and attaches the output. Subagent tool calls are excluded — they belong to
 * the subagent views (FR-3.17), not the main transcript.
 */
export interface ToolCallEntry {
  /** Stable id across the started→result pair — the `call_id`, else `event_id`. */
  readonly id: string;
  /** The invoked tool (`web_search`, `get_issue`, …). */
  readonly toolName: string;
  /** Display label — backend `display_title`, falling back to `toolName`. */
  readonly title: string;
  /** Lifecycle: `running` until a result frame lands, then `complete`/`error`. */
  readonly status: "running" | "complete" | "error" | "unavailable";
  /** Latest streamed call arguments, when present. */
  readonly args?: Record<string, unknown>;
  /** Result output from the `tool_result` payload, when present. */
  readonly result?: Record<string, unknown>;
  /** Backend one-line summary, when present. */
  readonly summary?: string;
  /** Safe error message on a failed/timed-out/cancelled result. */
  readonly errorMessage?: string;
  /**
   * Factually supplied tool origin. Today the runtime emits only MCP origin;
   * absence means unknown and must not be inferred from the tool name.
   */
  readonly provenance?: ToolCallProvenance;
  /** Safe authority mode supplied by the runtime, never inferred locally. */
  readonly accessMode?: "read" | "read_act" | "off";
  /** Measured duration from the completed runtime frame, in milliseconds. */
  readonly durationMs?: number;
  /** Task ids factually correlated to work delegated by this tool call. */
  readonly subagentTaskIds?: readonly string[];
  /** Anchor: `sequence_no` of the first (started) frame. */
  readonly sequenceNo: number;
  /** Anchor timestamp (epoch ms) for interleave; null if unparseable. */
  readonly createdAtMs: number | null;
}

export type RunTodoStatus = "pending" | "in_progress" | "completed";

/** One row of the agent's checklist, exactly as it wrote it. */
export interface RunTodoEntry {
  readonly content: string;
  readonly status: RunTodoStatus;
}

/**
 * The checklist the todo panel renders — the newest `todo_list_updated`
 * snapshot, with the two counts every consumer would otherwise recompute.
 */
export interface RunTodosProjection {
  /** Stable across revisions of one list; changes when the agent starts a new one. */
  readonly listId: string;
  /** 1-based. `> 1` means an earlier list in this run was finished first. */
  readonly generation: number;
  readonly todos: readonly RunTodoEntry[];
  readonly completedCount: number;
  /** True only when the list has rows and every one is done. */
  readonly isComplete: boolean;
  /** `sequence_no` of the snapshot, so a later one always wins. */
  readonly sequenceNo: number;
}

/** Safe, display-ready origin projected from `payload.provenance`. */
export interface ToolCallProvenance {
  readonly source: "mcp";
  readonly serverName: string;
}

/**
 * Minimal surface payload — opaque to the projector; the surface
 * renderer (sheet, email, slide, …) unpacks it. Today's mock-grade
 * renderers carry a flat `{ key: value }` record; richer renderers in
 * Phase 2 may extend without changing this contract.
 */
export type SurfacePayload = Record<string, unknown>;

const EMPTY_SURFACE_TABS: readonly SurfaceTab[] = [];

const EMPTY_TOOL_CALLS: readonly ToolCallEntry[] = [];

const EMPTY_STATE: ProjectedState = {
  activity: [],
  beads: [],
  chat: [],
  approvals: new Map(),
  surfaceState: new Map(),
  surfaceTabs: EMPTY_SURFACE_TABS,
  lastSequenceNo: -1,
};

/**
 * Project an ordered list of envelopes into `ProjectedState`.
 *
 * Stable on replay: re-projecting the same events yields the same state.
 * Deduplicates by `event_id`. Callers should pass events sorted by
 * `sequence_no` ascending; we DO NOT sort defensively to keep the hot
 * path cheap. The Transport-fed callers (Swimlanes today, ThreadCanvas
 * tomorrow) already sort upstream.
 */
export function project(
  events: readonly RuntimeEventEnvelope[],
): ProjectedState {
  if (events.length === 0) {
    return EMPTY_STATE;
  }
  const seen = new Set<string>();
  const activity: ActivityEntry[] = [];
  const beads: TimelineBead[] = [];
  const chat: ChatEntry[] = [];
  const approvals = new Map<string, Approval>();
  const surfaceState = new Map<string, SurfacePayload>();
  const surfaceMeta = new Map<string, SurfaceMeta>();
  let lastSequenceNo = -1;

  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    if (event.sequence_no > lastSequenceNo) {
      lastSequenceNo = event.sequence_no;
    }
    reduceEvent(event, {
      activity,
      beads,
      chat,
      approvals,
      surfaceState,
      surfaceMeta,
    });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
    surfaceTabs: buildSurfaceTabs(surfaceState, surfaceMeta),
    lastSequenceNo,
  };
}

/**
 * Time-travel projection. Equivalent to
 * `project(events.filter(e => e.sequence_no <= sequenceNo))` but avoids
 * the intermediate array allocation. Used by `TcSurfaceMount.reduceTo`
 * for client-side time-travel (sub-PRD §4.3 Q1 decision).
 */
export function projectAt(
  events: readonly RuntimeEventEnvelope[],
  sequenceNo: number,
): ProjectedState {
  if (events.length === 0) {
    return EMPTY_STATE;
  }
  const seen = new Set<string>();
  const activity: ActivityEntry[] = [];
  const beads: TimelineBead[] = [];
  const chat: ChatEntry[] = [];
  const approvals = new Map<string, Approval>();
  const surfaceState = new Map<string, SurfacePayload>();
  const surfaceMeta = new Map<string, SurfaceMeta>();
  let lastSequenceNo = -1;

  for (const event of events) {
    if (event.sequence_no > sequenceNo) {
      continue;
    }
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    if (event.sequence_no > lastSequenceNo) {
      lastSequenceNo = event.sequence_no;
    }
    reduceEvent(event, {
      activity,
      beads,
      chat,
      approvals,
      surfaceState,
      surfaceMeta,
    });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
    surfaceTabs: buildSurfaceTabs(surfaceState, surfaceMeta),
    lastSequenceNo,
  };
}

/**
 * Pure selector: surface-tab strip for the cockpit, derived off the SAME
 * canonical `RuntimeEventEnvelope[]` the single projection reads (FR-3.3). It
 * is a focused surface-only pass — NOT a second `project()` / SSE subscription —
 * mirroring `projectSubagents` / `projectApprovals`. `RunDestination` memoizes
 * it against `session.events`; the ordering + shape match `project().surfaceTabs`
 * exactly (both reuse `applySurfaceEvent` + `buildSurfaceTabs`).
 */
export function projectSurfaceTabs(
  events: readonly RuntimeEventEnvelope[],
): readonly SurfaceTab[] {
  if (events.length === 0) {
    return EMPTY_SURFACE_TABS;
  }
  const seen = new Set<string>();
  const surfaceState = new Map<string, SurfacePayload>();
  const surfaceMeta = new Map<string, SurfaceMeta>();
  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    applySurfaceEvent(event, surfaceState, surfaceMeta);
  }
  return buildSurfaceTabs(surfaceState, surfaceMeta);
}

/**
 * Pure selector: the MAIN-AGENT tool-call cards for the transcript, projected
 * off the SAME canonical `RuntimeEventEnvelope[]` the single projection reads
 * (FR-3.3). A focused surface-only pass — NOT a second `project()` / SSE
 * subscription — mirroring `projectSubagents` / `projectApprovals` /
 * `projectSurfaceTabs`. `RunDestination` memoizes it against `session.events`
 * and threads the result into `TcChat`, where each entry interleaves into the
 * transcript at the point its tool ran.
 *
 * Collapsed per `call_id`: `tool_call_started` seeds a `running` card carrying
 * the initial args, then `tool_call_delta` frames keep its streamed args fresh.
 * The matching `tool_result` / `tool_call_completed` flips it to `complete` /
 * `error` and attaches the output. Subagent tool calls (`subagent_id` set) are
 * skipped — they render inside the subagent views, not the main transcript.
 * Idempotent on replay (deduplicates by `event_id`); ordered by the anchor
 * (started) `sequence_no` ascending.
 */
export function projectToolCalls(
  events: readonly RuntimeEventEnvelope[],
): readonly ToolCallEntry[] {
  if (events.length === 0) {
    return EMPTY_TOOL_CALLS;
  }
  const seen = new Set<string>();
  const byCall = new Map<string, MutableToolCall>();
  const order: string[] = [];
  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    // Honour the server's visibility call, exactly as `project()` does. This
    // pass had never checked it, so every tool the backend classifies as
    // INTERNAL still rendered a card here: `write_todos` (whose checklist the
    // todo panel owns) and `ask_a_question` (whose approval card is the real
    // surface) both showed a raw args/result tile beside the surface meant to
    // replace them.
    if (!isVisibleToUser(event)) {
      continue;
    }
    // Main-agent only — a subagent's tool calls render inside the subagent
    // views (FR-3.17), never the main transcript.
    if (event.subagent_id) {
      continue;
    }
    if (event.event_type === "tool_call_started") {
      reduceToolStarted(event, byCall, order);
    } else if (event.event_type === "tool_call_delta") {
      reduceToolDelta(event, byCall, order);
    } else if (
      event.event_type === "tool_result" ||
      event.event_type === "tool_call_completed"
    ) {
      reduceToolResult(event, byCall, order);
    }
  }
  if (order.length === 0) {
    return EMPTY_TOOL_CALLS;
  }
  return order.map((key) => buildToolCall(byCall.get(key)!));
}

/**
 * Pure selector: the agent's working checklist, off the SAME canonical
 * `RuntimeEventEnvelope[]` every other cockpit consumer reads (FR-3.3).
 *
 * `write_todos` replaces the whole list per call, and the server resolves each
 * write into a `todo_list_updated` snapshot carrying list identity, so the
 * newest snapshot IS the state — this walks to the last one rather than folding
 * a sequence of diffs. Main-agent only, matching `projectToolCalls`: a
 * subagent's checklist belongs to the subagent views, not the main transcript.
 *
 * Returns `null` when the run has no checklist, which is the common case — most
 * requests are too small for the agent to open one, and the panel must not
 * appear until it does.
 */
export function projectRunTodos(
  events: readonly RuntimeEventEnvelope[],
): RunTodosProjection | null {
  let latest: RunTodosProjection | null = null;
  for (const event of events) {
    if (event.event_type !== "todo_list_updated" || event.subagent_id) {
      continue;
    }
    const snapshot = readTodoSnapshot(event);
    // Keep the highest sequence rather than the last seen: replay and a live
    // tail can interleave, and a stale snapshot arriving late must not roll the
    // panel backwards.
    if (
      snapshot !== null &&
      (latest === null || snapshot.sequenceNo >= latest.sequenceNo)
    ) {
      latest = snapshot;
    }
  }
  return latest;
}

function readTodoSnapshot(
  event: RuntimeEventEnvelope,
): RunTodosProjection | null {
  const payload = readRecord(event.payload);
  const rawTodos = payload?.todos;
  const listId = pickString(payload, "list_id");
  if (listId === null || !Array.isArray(rawTodos)) {
    return null;
  }
  const todos: RunTodoEntry[] = [];
  for (const item of rawTodos) {
    const row = readRecord(item);
    const content = pickString(row, "content");
    const status = row?.status;
    // Drop the snapshot whole on an unreadable row. A row the client cannot
    // place would render as pending and read as work still to come.
    if (content === null || !isRunTodoStatus(status)) {
      return null;
    }
    todos.push({ content, status });
  }
  const generation =
    typeof payload?.generation === "number" ? payload.generation : 1;
  const completedCount = todos.filter(
    (todo) => todo.status === "completed",
  ).length;
  return {
    listId,
    generation,
    todos,
    completedCount,
    isComplete: todos.length > 0 && completedCount === todos.length,
    sequenceNo: event.sequence_no,
  };
}

function isRunTodoStatus(value: unknown): value is RunTodoStatus {
  return (
    value === "pending" || value === "in_progress" || value === "completed"
  );
}

/**
 * Selector helpers — give consumers what they actually need. They keep
 * the consumer code free of the projector's internal shape.
 */
export const selectors = {
  pendingApprovals(state: ProjectedState): readonly Approval[] {
    const out: Approval[] = [];
    for (const approval of state.approvals.values()) {
      if (approval.state === "pending") {
        out.push(approval);
      }
    }
    return out;
  },

  activityFeed(state: ProjectedState): readonly ActivityEntry[] {
    return state.activity;
  },

  beadsForLane(state: ProjectedState, lane: string): readonly TimelineBead[] {
    return state.beads.filter((b) => b.lane === lane);
  },

  chatEntries(state: ProjectedState): readonly ChatEntry[] {
    return state.chat;
  },

  surfaceFor(state: ProjectedState, uri: string): SurfacePayload | undefined {
    return state.surfaceState.get(uri);
  },
} as const;

// --- Internals -------------------------------------------------------------

interface MutableState {
  readonly activity: ActivityEntry[];
  readonly beads: TimelineBead[];
  readonly chat: ChatEntry[];
  readonly approvals: Map<string, Approval>;
  readonly surfaceState: Map<string, SurfacePayload>;
  readonly surfaceMeta: Map<string, SurfaceMeta>;
}

/**
 * Per-event reducer — pure function of the event into the mutable state
 * buckets. Branches by `event_type` per chats-canvas-prd §4.2 mapping
 * table. Backend-projected `activity_kind` / `display_title` / `summary`
 * / `status` are the visible labels — we don't derive them from the
 * event_type (root CLAUDE.md backend rule).
 */
function reduceEvent(event: RuntimeEventEnvelope, state: MutableState): void {
  const surfaceUri = extractSurfaceUri(event);
  const subagentId = event.subagent_id ?? undefined;
  const createdAt = event.created_at;

  // Activity — every visible event makes one entry. The activity tab is
  // a flat chronological stream; the renderer is responsible for
  // collapsing chatty rows (think / streaming) into groups.
  if (isVisibleToUser(event)) {
    state.activity.push({
      id: event.event_id,
      sequenceNo: event.sequence_no,
      kind: event.activity_kind ?? "system",
      title: event.display_title ?? event.event_type,
      summary: event.summary ?? undefined,
      status: event.status ?? undefined,
      createdAt,
      subagentId,
      surfaceUri,
    });
  }

  // Beads — only state-changing events. The bead title comes from the
  // backend's projection; the lane is the surface scheme or "system".
  if (isStateChanging(event)) {
    const parsed = Date.parse(createdAt);
    state.beads.push({
      id: event.event_id,
      sequenceNo: event.sequence_no,
      atMs: Number.isNaN(parsed) ? event.sequence_no : parsed,
      lane: surfaceUri ? schemeOf(surfaceUri) : "system",
      title:
        event.display_title ?? event.presentation?.title ?? event.event_type,
      pending: event.event_type === "approval_requested",
    });
  }

  // Chat-side projections. The chat shows: user messages, assistant
  // streaming + finalised messages, tool-call cards, approval cards,
  // subagent boundary cards.
  const chatEntry = projectChatEntry(event, surfaceUri, subagentId);
  if (chatEntry !== null) {
    state.chat.push(chatEntry);
  }

  // Approvals — synthesize / mutate from the runtime event payload while
  // P1-A's wire shape is in flight. TODO(merge): once P1-A's approval
  // events emit a fully-shaped `Approval` payload, replace `extractApproval`
  // with `payload as Approval` (still goes through validation).
  if (event.event_type === "approval_requested") {
    const approval = extractApproval(event);
    if (approval !== null) {
      state.approvals.set(approval.id, approval);
    }
  } else if (event.event_type === "approval_resolved") {
    const approvalId = pickString(event.payload, "approval_id");
    if (approvalId !== null) {
      const prior = state.approvals.get(approvalId);
      if (prior !== undefined) {
        const nextState = nextApprovalState(event);
        state.approvals.set(approvalId, {
          ...prior,
          state: nextState,
          resolved_at: createdAt,
        });
      }
    }
  }

  // Surface state — `tool_result` / draft / presentation carry the new
  // surface payload (legacy flat OR the PRD-01 `payload.surface` envelope);
  // `surface_spec_generated` merges a late spec by URI. Handled in one place so
  // `project()` and the `projectSurfaceTabs` selector stay byte-identical.
  applySurfaceEvent(event, state.surfaceState, state.surfaceMeta);
}

function isVisibleToUser(event: RuntimeEventEnvelope): boolean {
  if (event.visibility === "internal" || event.visibility === "audit") {
    return false;
  }
  // Receipts are full-run completion artifacts and are surfaced by the dedicated
  // receipt surface; they should not appear in the activity timeline.
  if (event.event_type === "receipt.emitted") {
    return false;
  }
  // model_delta / reasoning_summary_delta are streaming entries — they
  // belong in activity but the renderer should batch them. The projector
  // includes them; throttling lives at the consumer (TcChat 3s flush).
  return true;
}

function isStateChanging(event: RuntimeEventEnvelope): boolean {
  switch (event.event_type) {
    case "tool_result":
    case "approval_requested":
    case "approval_resolved":
    case "final_response":
    case "run_completed":
    case "run_started":
    case "run_cancelled":
    case "run_failed":
    case "subagent_started":
    case "subagent_completed":
    case "presentation_updated":
    case "draft_updated":
    case "adapter_generated":
      return true;
    default:
      return false;
  }
}

function isSurfaceMutation(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "tool_result" ||
    event.event_type === "presentation_updated" ||
    event.event_type === "draft_updated"
  );
}

function projectChatEntry(
  event: RuntimeEventEnvelope,
  surfaceUri: string | undefined,
  subagentId: string | undefined,
): ChatEntry | null {
  const createdAt = event.created_at;
  switch (event.event_type) {
    case "final_response": {
      const text = pickString(event.payload, "text") ?? event.summary ?? "";
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "assistant_message",
        text,
        createdAt,
        subagentId,
      };
    }
    case "model_delta":
    case "reasoning_summary_delta": {
      const text = pickString(event.payload, "text") ?? "";
      if (text === "") {
        return null;
      }
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "stream_delta",
        text,
        createdAt,
        subagentId,
      };
    }
    case "tool_call_started":
    case "tool_call_completed":
    case "tool_result": {
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "tool_call",
        title:
          event.display_title ?? event.presentation?.title ?? event.event_type,
        status: event.status ?? event.presentation?.status_label ?? undefined,
        createdAt,
        subagentId,
        surfaceUri,
      };
    }
    case "approval_requested": {
      const approvalId = pickString(event.payload, "approval_id");
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "approval",
        approvalId: approvalId ?? undefined,
        title:
          event.display_title ??
          event.presentation?.title ??
          "Approval requested",
        createdAt,
        subagentId,
        surfaceUri,
      };
    }
    case "subagent_started":
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "subagent_started",
        title: event.display_title ?? "Subagent started",
        createdAt,
        subagentId,
      };
    case "subagent_completed":
      return {
        id: event.event_id,
        sequenceNo: event.sequence_no,
        kind: "subagent_completed",
        title: event.display_title ?? "Subagent completed",
        createdAt,
        subagentId,
      };
    default:
      return null;
  }
}

function extractApproval(event: RuntimeEventEnvelope): Approval | null {
  const approvalId = pickString(event.payload, "approval_id");
  if (approvalId === null) {
    return null;
  }
  const requester = pickString(event.payload, "requester_user_id") ?? "system";
  const targetUserId = pickString(event.payload, "target_user_id");
  const kind = pickString(event.payload, "kind") ?? "approval";
  const tenantId = pickString(event.payload, "tenant_id") ?? "";
  // Brands are erased at runtime — these casts are safe at the trust
  // boundary (per `brands.ts` documentation).
  return {
    id: approvalId as Approval["id"],
    run_id: event.run_id as Approval["run_id"],
    conversation_id: event.conversation_id as Approval["conversation_id"],
    tenant_id: tenantId as Approval["tenant_id"],
    requester: requester as Approval["requester"],
    target_user_id: (targetUserId ?? null) as Approval["target_user_id"],
    kind,
    payload: event.payload,
    state: "pending",
    created_at: event.created_at,
    context: {
      conversation_id: event.conversation_id as Approval["conversation_id"],
      run_id: event.run_id as Approval["run_id"],
      sequence_no: event.sequence_no,
    },
  };
}

function nextApprovalState(event: RuntimeEventEnvelope): ApprovalState {
  const decision = pickString(event.payload, "decision");
  if (decision === "reject") {
    return "rejected";
  }
  if (decision === "suggest_edit") {
    return "edited";
  }
  return "accepted";
}

function extractSurfaceUri(event: RuntimeEventEnvelope): string | undefined {
  const flat = event.payload?.["surface_uri"];
  if (typeof flat === "string") {
    return flat;
  }
  // PRD-01 envelope: the uri rides under `payload.surface.surface_uri`.
  const nested = readSurfaceEnvelope(event)?.uri;
  return nested;
}

function extractSurfacePayload(
  event: RuntimeEventEnvelope,
): SurfacePayload | undefined {
  const state = event.payload?.["state"];
  if (state && typeof state === "object") {
    return state as SurfacePayload;
  }
  const result = event.payload?.["result"];
  if (result && typeof result === "object") {
    return result as SurfacePayload;
  }
  return undefined;
}

// --- Surface projection (PRD-04) ------------------------------------------

/** Read the PRD-01 `payload.surface` envelope, if present (else `undefined`). */
function readSurfaceEnvelope(
  event: RuntimeEventEnvelope,
): { uri?: string; archetype?: string; state?: SurfacePayload } | undefined {
  const surface = event.payload?.["surface"];
  if (!surface || typeof surface !== "object") {
    return undefined;
  }
  const record = surface as Record<string, unknown>;
  const uri = record["surface_uri"];
  const archetype = record["archetype"];
  const state = record["state"];
  return {
    uri: typeof uri === "string" ? uri : undefined,
    archetype: typeof archetype === "string" ? archetype : undefined,
    state:
      state && typeof state === "object"
        ? (state as SurfacePayload)
        : undefined,
  };
}

/**
 * Apply one event's surface effect into `surfaceState` + `surfaceMeta`.
 *
 * - `tool_result` / `draft_updated` / `presentation_updated`: merge the surface
 *   payload (`{spec?, data}` from the envelope, or a legacy flat state object).
 * - `surface_spec_generated`: merge ONLY the `spec` key so a late spec upgrades
 *   the surface in place and never clobbers newer `data` (D4). Replay-idempotent
 *   because the caller deduplicates by `event_id` and the writes are keyed.
 */
function applySurfaceEvent(
  event: RuntimeEventEnvelope,
  surfaceState: Map<string, SurfacePayload>,
  surfaceMeta: Map<string, SurfaceMeta>,
): void {
  if (event.event_type === "surface_spec_generated") {
    const uri = extractSurfaceUri(event);
    if (uri === undefined) {
      return;
    }
    const spec = event.payload?.["spec"];
    if (!spec || typeof spec !== "object") {
      return;
    }
    const prior = surfaceState.get(uri) ?? {};
    // Spec merge only — `data` (if any) is preserved untouched.
    surfaceState.set(uri, { ...prior, spec });
    bumpSurfaceMeta(
      surfaceMeta,
      uri,
      event.sequence_no,
      pickString(event.payload, "archetype") ?? undefined,
    );
    return;
  }

  if (!isSurfaceMutation(event)) {
    return;
  }
  const envelope = readSurfaceEnvelope(event);
  const uri = envelope?.uri ?? extractSurfaceUri(event);
  if (uri === undefined) {
    return;
  }
  const incoming = envelope?.state ?? extractSurfacePayload(event);
  const prior = surfaceState.get(uri) ?? {};
  surfaceState.set(uri, { ...prior, ...(incoming ?? {}) });
  bumpSurfaceMeta(surfaceMeta, uri, event.sequence_no, envelope?.archetype);
}

function bumpSurfaceMeta(
  meta: Map<string, SurfaceMeta>,
  uri: string,
  sequenceNo: number,
  archetype: string | undefined,
): void {
  const prior = meta.get(uri);
  meta.set(uri, {
    lastSeq:
      prior === undefined ? sequenceNo : Math.max(prior.lastSeq, sequenceNo),
    archetype: archetype ?? prior?.archetype,
  });
}

/** Build the ordered surface-tab strip from the per-URI state + metadata. */
function buildSurfaceTabs(
  surfaceState: ReadonlyMap<string, SurfacePayload>,
  surfaceMeta: ReadonlyMap<string, SurfaceMeta>,
): readonly SurfaceTab[] {
  if (surfaceState.size === 0) {
    return EMPTY_SURFACE_TABS;
  }
  const tabs: SurfaceTab[] = [];
  for (const [uri, payload] of surfaceState) {
    const meta = surfaceMeta.get(uri);
    tabs.push({
      uri,
      archetype: meta?.archetype,
      title: surfaceTabTitle(uri, payload),
      lastSeq: meta?.lastSeq ?? -1,
    });
  }
  // Newest mutation first; ties keep insertion order (ES sort is stable).
  tabs.sort((a, b) => b.lastSeq - a.lastSeq);
  return tabs;
}

/**
 * Best-effort tab title: resolve the spec's `title_path` against `data`; fall
 * back to the URI tail. Never throws — this is display-only, over untrusted data.
 */
function surfaceTabTitle(uri: string, payload: SurfacePayload): string {
  const spec = payload["spec"];
  if (spec && typeof spec === "object") {
    const titlePath = (spec as Partial<SurfaceSpec>).title_path;
    if (typeof titlePath === "string" && titlePath !== "") {
      const resolved = resolvePath(payload["data"], titlePath);
      if (typeof resolved === "string" && resolved.trim() !== "") {
        return resolved;
      }
      if (typeof resolved === "number" && Number.isFinite(resolved)) {
        return String(resolved);
      }
    }
  }
  return uriTail(uri);
}

/** Resolve a dot-path (`a.b.0.c`) against a value. Identifiers + indices only. */
function resolvePath(data: unknown, path: string): unknown {
  let cursor: unknown = data;
  for (const segment of path.split(".")) {
    if (cursor === null || typeof cursor !== "object") {
      return undefined;
    }
    cursor = (cursor as Record<string, unknown>)[segment];
  }
  return cursor;
}

/** `record://seed/get_issue/42` → `seed/get_issue/42`; degrades to the raw uri. */
function uriTail(uri: string): string {
  const sep = uri.indexOf("://");
  if (sep < 0) {
    return uri;
  }
  return uri.slice(sep + 3) || uri;
}

function schemeOf(uri: string): string {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : "system";
}

// --- Tool-call projection --------------------------------------------------

interface MutableToolCall {
  key: string;
  toolName: string;
  title: string | null;
  status: "running" | "complete" | "error" | "unavailable";
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  summary?: string;
  errorMessage?: string;
  provenance?: ToolCallProvenance;
  accessMode?: "read" | "read_act" | "off";
  durationMs?: number;
  subagentTaskIds?: readonly string[];
  sequenceNo: number;
  createdAtMs: number | null;
}

function reduceToolStarted(
  event: RuntimeEventEnvelope,
  byCall: Map<string, MutableToolCall>,
  order: string[],
): void {
  const key = toolCallKey(event);
  const prior = byCall.get(key);
  if (prior === undefined) {
    order.push(key);
  }
  byCall.set(key, {
    key,
    toolName:
      pickString(event.payload, "tool_name") ?? prior?.toolName ?? "tool",
    title:
      agentToolDisplayValue(event, "display_title", "_display_title") ??
      event.presentation?.title ??
      event.display_title ??
      prior?.title ??
      null,
    // A result may (on an out-of-order replay) have landed first — keep it.
    status: prior?.status ?? "running",
    args: readRecord(event.payload?.["args"]) ?? prior?.args,
    result: prior?.result,
    summary:
      agentToolDisplayValue(event, "display_summary", "_display_summary") ??
      event.summary ??
      event.presentation?.summary ??
      pickString(event.payload, "summary") ??
      prior?.summary ??
      undefined,
    errorMessage: prior?.errorMessage,
    provenance:
      readToolProvenance(event.payload?.["provenance"]) ?? prior?.provenance,
    accessMode:
      readToolAccessMode(event.payload?.["access_mode"]) ?? prior?.accessMode,
    durationMs:
      readToolDuration(event.payload?.["duration_ms"]) ?? prior?.durationMs,
    subagentTaskIds:
      readToolTaskIds(event.payload?.["subagent_task_ids"]) ??
      prior?.subagentTaskIds,
    // The started frame is the earliest, so it wins the anchor when present.
    sequenceNo: prior?.sequenceNo ?? event.sequence_no,
    createdAtMs: prior?.createdAtMs ?? parseMs(event.created_at),
  });
}

function reduceToolDelta(
  event: RuntimeEventEnvelope,
  byCall: Map<string, MutableToolCall>,
  order: string[],
): void {
  const key = toolCallKey(event);
  const prior = byCall.get(key);
  if (prior === undefined) {
    order.push(key);
  }
  byCall.set(key, {
    key,
    toolName:
      pickString(event.payload, "tool_name") ?? prior?.toolName ?? "tool",
    title:
      agentToolDisplayValue(event, "display_title", "_display_title") ??
      prior?.title ??
      event.presentation?.title ??
      event.display_title ??
      null,
    // Deltas can race with terminal frames on reconnect; argument updates must
    // never turn a completed/failed card back into a running one.
    status: prior?.status ?? "running",
    args: updatedToolArgs(event, prior?.args),
    result: prior?.result,
    summary:
      agentToolDisplayValue(event, "display_summary", "_display_summary") ??
      event.summary ??
      event.presentation?.summary ??
      pickString(event.payload, "summary") ??
      prior?.summary ??
      undefined,
    errorMessage: prior?.errorMessage,
    provenance:
      readToolProvenance(event.payload?.["provenance"]) ?? prior?.provenance,
    accessMode:
      readToolAccessMode(event.payload?.["access_mode"]) ?? prior?.accessMode,
    durationMs:
      readToolDuration(event.payload?.["duration_ms"]) ?? prior?.durationMs,
    subagentTaskIds:
      readToolTaskIds(event.payload?.["subagent_task_ids"]) ??
      prior?.subagentTaskIds,
    sequenceNo: prior?.sequenceNo ?? event.sequence_no,
    createdAtMs: prior?.createdAtMs ?? parseMs(event.created_at),
  });
}

function reduceToolResult(
  event: RuntimeEventEnvelope,
  byCall: Map<string, MutableToolCall>,
  order: string[],
): void {
  const key = toolCallKey(event);
  const prior = byCall.get(key);
  const structuredError = readStructuredToolError(event.payload?.["output"]);
  if (prior === undefined) {
    order.push(key);
  }
  byCall.set(key, {
    key,
    toolName:
      pickString(event.payload, "tool_name") ?? prior?.toolName ?? "tool",
    title:
      prior?.title ??
      agentToolDisplayValue(event, "display_title", "_display_title") ??
      event.presentation?.title ??
      event.display_title ??
      null,
    status: mapResultStatus(
      event,
      prior?.status,
      structuredError !== undefined,
    ),
    args: prior?.args,
    result:
      structuredError?.output ??
      readRecord(event.payload?.["output"]) ??
      prior?.result,
    summary:
      event.summary ??
      event.presentation?.summary ??
      pickString(event.payload, "summary") ??
      prior?.summary ??
      undefined,
    errorMessage:
      pickString(event.payload, "error_message") ??
      pickString(event.payload, "safe_message") ??
      structuredError?.safeMessage ??
      prior?.errorMessage,
    provenance:
      readToolProvenance(event.payload?.["provenance"]) ?? prior?.provenance,
    accessMode:
      readToolAccessMode(event.payload?.["access_mode"]) ?? prior?.accessMode,
    durationMs:
      readToolDuration(event.payload?.["duration_ms"]) ?? prior?.durationMs,
    subagentTaskIds:
      readToolTaskIds(event.payload?.["subagent_task_ids"]) ??
      prior?.subagentTaskIds,
    sequenceNo: prior?.sequenceNo ?? event.sequence_no,
    createdAtMs: prior?.createdAtMs ?? parseMs(event.created_at),
  });
}

function buildToolCall(m: MutableToolCall): ToolCallEntry {
  return {
    id: m.key,
    toolName: m.toolName,
    title: m.title ?? m.toolName,
    status: m.status,
    ...(m.args !== undefined ? { args: m.args } : {}),
    ...(m.result !== undefined ? { result: m.result } : {}),
    ...(m.summary !== undefined ? { summary: m.summary } : {}),
    ...(m.errorMessage !== undefined ? { errorMessage: m.errorMessage } : {}),
    ...(m.provenance !== undefined ? { provenance: m.provenance } : {}),
    ...(m.accessMode !== undefined ? { accessMode: m.accessMode } : {}),
    ...(m.durationMs !== undefined ? { durationMs: m.durationMs } : {}),
    ...(m.subagentTaskIds !== undefined
      ? { subagentTaskIds: m.subagentTaskIds }
      : {}),
    sequenceNo: m.sequenceNo,
    createdAtMs: m.createdAtMs,
  };
}

function agentToolDisplayValue(
  event: RuntimeEventEnvelope,
  fieldName: "display_title" | "display_summary",
  alias: "_display_title" | "_display_summary",
): string | undefined {
  const args = readRecord(event.payload?.["args"]);
  return args === undefined
    ? undefined
    : (pickString(args, fieldName) ?? pickString(args, alias) ?? undefined);
}

/** A completed result frame flips the card to `complete`; anything else (failed,
 *  timed_out, abandoned, cancelled, …) reads as `error`. The mere presence of a
 *  result frame with no status means the tool returned — treat as complete.
 *
 *  `unavailable` is its own outcome, not a flavour of either: the capability
 *  was declined by policy, so no work happened (`complete` would overstate it)
 *  and nothing broke (`error` would invent a fault). Collapsing it into `error`
 *  here is what kept a declined `ls` rendering as a failed step even after the
 *  backend had stopped calling it one. */
function mapResultStatus(
  event: RuntimeEventEnvelope,
  priorStatus: MutableToolCall["status"] | undefined,
  hasStructuredError = false,
): "complete" | "error" | "unavailable" {
  if (hasStructuredError) {
    return "error";
  }
  const raw =
    pickString(event.payload, "status") ??
    event.status ??
    event.presentation?.status_label ??
    null;
  if (raw === null) {
    // A follow-up `tool_call_completed` can be a bare lifecycle receipt. It
    // must not hide the explicit error on the preceding `tool_result`.
    if (priorStatus === "error") {
      return "error";
    }
    return "complete";
  }
  if (raw.toLowerCase() === "unavailable" || raw === "Not available") {
    return "unavailable";
  }
  if (
    raw.toLowerCase() === "completed" ||
    raw.toLowerCase() === "complete" ||
    raw.toLowerCase() === "success" ||
    raw.toLowerCase() === "succeeded" ||
    raw.toLowerCase() === "ok" ||
    raw.toLowerCase() === "done"
  ) {
    return "complete";
  }
  return "error";
}

function toolCallKey(event: RuntimeEventEnvelope): string {
  // `call_id` is the only key that collapses a started→result pair into ONE
  // card. Falling back to `event_id` looks harmless but is not: `event_id` is
  // unique per EVENT, so an op whose events carry no usable `call_id` renders
  // its start and its result as two separate cards — which is exactly the
  // duplicate `load_mcp_server` / `suggest_mcp_connector` cards users saw.
  //
  // The fallback is kept, because dropping the event entirely would be worse
  // than showing it twice, but it is now marked so the defect is visible as a
  // data problem rather than silently absorbed into the UI. The real fix is
  // upstream: every tool result must carry the id (see the tool_call_id
  // invariant in the runtime's display middleware).
  const callId = pickString(event.payload, "call_id");
  if (callId !== null && callId.length > 0) {
    return callId;
  }
  return `${UNKEYED_TOOL_CALL_PREFIX}${event.event_id}`;
}

/** Marks a card whose event carried no usable `call_id`. Greppable on purpose:
 *  a key with this prefix means an upstream op emitted an unusable id. */
export const UNKEYED_TOOL_CALL_PREFIX = "unkeyed:";

/**
 * Runtime tool-call deltas currently carry the latest accumulated `args`
 * snapshot. Accept `args_delta` too for forward-compatible partial updates.
 * A full snapshot intentionally replaces a stale placeholder such as `{}` or
 * `{ delta: "…" }` emitted before the streaming JSON became parseable.
 */
function updatedToolArgs(
  event: RuntimeEventEnvelope,
  prior: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  const snapshot = readRecord(event.payload?.["args"]);
  if (snapshot !== undefined) {
    return snapshot;
  }
  const delta = readRecord(event.payload?.["args_delta"]);
  if (delta === undefined) {
    return prior;
  }
  return { ...(prior ?? {}), ...delta };
}

function readRecord(value: unknown): Record<string, unknown> | undefined {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return undefined;
}

const EMBEDDED_TOOL_RESULT_PARSE_CAP = 64 * 1024;

/**
 * Some LangChain tools return a typed `{error: ...}` object normally. The
 * framework serialises that object into `ToolMessage.content` and still marks
 * the message as `success`, so older persisted events can contain a real
 * failure inside `payload.output.content`. Parse only that bounded, explicit
 * error envelope; arbitrary successful tool strings stay untouched.
 */
function readStructuredToolError(value: unknown):
  | {
      readonly output: Record<string, unknown>;
      readonly safeMessage?: string;
    }
  | undefined {
  const output = readRecord(value);
  if (output === undefined) return undefined;

  const directError = readRecord(output["error"]);
  if (directError !== undefined) {
    const safeMessage = toolErrorMessage(directError);
    return {
      output,
      ...(safeMessage !== undefined ? { safeMessage } : {}),
    };
  }

  const content = output["content"];
  if (
    typeof content !== "string" ||
    content.length === 0 ||
    content.length > EMBEDDED_TOOL_RESULT_PARSE_CAP
  ) {
    return undefined;
  }
  try {
    const parsed = readRecord(JSON.parse(content));
    const error = readRecord(parsed?.["error"]);
    if (parsed === undefined || error === undefined) return undefined;
    const safeMessage = toolErrorMessage(error);
    return {
      output: parsed,
      ...(safeMessage !== undefined ? { safeMessage } : {}),
    };
  } catch {
    return undefined;
  }
}

function toolErrorMessage(error: Record<string, unknown>): string | undefined {
  return (
    pickString(error, "safe_message") ??
    pickString(error, "message") ??
    undefined
  );
}

function readToolProvenance(value: unknown): ToolCallProvenance | undefined {
  const record = readRecord(value);
  if (record?.["source"] !== "mcp") return undefined;
  const serverName = record["server_name"];
  if (typeof serverName !== "string" || serverName.trim() === "") {
    return undefined;
  }
  return { source: "mcp", serverName };
}

function readToolAccessMode(
  value: unknown,
): "read" | "read_act" | "off" | undefined {
  return value === "read" || value === "read_act" || value === "off"
    ? value
    : undefined;
}

function readToolDuration(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function readToolTaskIds(value: unknown): readonly string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const ids = value.filter(
    (taskId): taskId is string =>
      typeof taskId === "string" && taskId.trim().length > 0,
  );
  return [...new Set(ids)];
}

function parseMs(iso: string): number | null {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : parsed;
}

function pickString(
  payload: Record<string, unknown> | undefined,
  key: string,
): string | null {
  if (!payload) {
    return null;
  }
  const value = payload[key];
  return typeof value === "string" ? value : null;
}
