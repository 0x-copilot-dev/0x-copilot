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
 * Minimal surface payload — opaque to the projector; the surface
 * renderer (sheet, email, slide, …) unpacks it. Today's mock-grade
 * renderers carry a flat `{ key: value }` record; richer renderers in
 * Phase 2 may extend without changing this contract.
 */
export type SurfacePayload = Record<string, unknown>;

const EMPTY_SURFACE_TABS: readonly SurfaceTab[] = [];

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
