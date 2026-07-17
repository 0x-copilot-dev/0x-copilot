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

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

// TODO(merge): replace import from "./_approvals-stub" with "@0x-copilot/api-types"
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
  /** Highest `sequence_no` we've seen — useful for time-travel cursor. */
  readonly lastSequenceNo: number;
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
 * Minimal surface payload — opaque to the projector; the surface
 * renderer (sheet, email, slide, …) unpacks it. Today's mock-grade
 * renderers carry a flat `{ key: value }` record; richer renderers in
 * Phase 2 may extend without changing this contract.
 */
export type SurfacePayload = Record<string, unknown>;

const EMPTY_STATE: ProjectedState = {
  activity: [],
  beads: [],
  chat: [],
  approvals: new Map(),
  surfaceState: new Map(),
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
  let lastSequenceNo = -1;

  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    if (event.sequence_no > lastSequenceNo) {
      lastSequenceNo = event.sequence_no;
    }
    reduceEvent(event, { activity, beads, chat, approvals, surfaceState });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
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
    reduceEvent(event, { activity, beads, chat, approvals, surfaceState });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
    lastSequenceNo,
  };
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

  // Surface state — `tool_result` and presentation/draft updates carry
  // the new surface payload. We merge into the per-uri record.
  if (surfaceUri !== undefined && isSurfaceMutation(event)) {
    const merged: SurfacePayload = {
      ...(state.surfaceState.get(surfaceUri) ?? {}),
      ...(extractSurfacePayload(event) ?? {}),
    };
    state.surfaceState.set(surfaceUri, merged);
  }
}

function isVisibleToUser(event: RuntimeEventEnvelope): boolean {
  if (event.visibility === "internal" || event.visibility === "audit") {
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
  const candidate = event.payload?.["surface_uri"];
  if (typeof candidate === "string") {
    return candidate;
  }
  return undefined;
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

function schemeOf(uri: string): string {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : "system";
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
