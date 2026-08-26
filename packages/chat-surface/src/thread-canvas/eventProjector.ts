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
// 6. **Spec generation is UNDERWAY (`surface_spec_requested`)**. The runtime
//    emits that progress signal immediately before the spec-generation model
//    call starts, and `surface_spec_generated` (5) remains the terminal event.
//    It folds into `surfaceSpecGeneration` in the SAME pass, so the surface can
//    say what is happening instead of shimmering at a blank. Nothing may depend
//    on it arriving: a runtime that never emits it leaves the map empty, and an
//    empty map is what every consumer already sees today.

import type { RuntimeEventEnvelope, SurfaceSpec } from "@0x-copilot/api-types";

// TODO(merge): replace import from "./_approvals-stub" with "@0x-copilot/api-types"
import type { SurfaceHue } from "../surfaces/surfaceHue";
// The SSOT for the parked-write id prefix. A VALUE import, and safe for the
// same reason `TcChat`'s is: `approvalProjection` imports only `approvals/` +
// `workspace/`, so the edge never runs back into `thread-canvas/`.
import { WRITE_GATE_APPROVAL_PREFIX } from "../destinations/run/approvalProjection";
import type { Approval, ApprovalState } from "./_approvals-stub";
import { resolveToolArgs } from "./streamedToolArgs";

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
  /**
   * Surfaces whose spec generation is UNDERWAY right now, keyed by the same
   * surface identity `surfaceState` uses (a surface's mount/tab URI IS its
   * `surface_id` — see the identity note in `ledgerProjection.ts`).
   *
   * Membership is the whole signal: an entry appears on `surface_spec_requested`
   * and is removed by its terminal `surface_spec_generated` (or by the run
   * ending). So `surfaceSpecGeneration.get(uri)` answers "is a model choosing
   * this surface's layout at this instant", which is a claim the tier cannot
   * make — `pending` only ever meant "no `view.derived` yet", which is equally
   * true of a generation that never started and of one that died.
   */
  readonly surfaceSpecGeneration: ReadonlyMap<string, SurfaceSpecGeneration>;
  /** Highest `sequence_no` we've seen — useful for time-travel cursor. */
  readonly lastSequenceNo: number;
}

/**
 * One in-flight surface-spec generation. `modelId` is the runtime's `model_id`
 * (`null` when it did not name one) and is display-only — it is echoed to the
 * reader, never matched against a model catalog.
 */
export interface SurfaceSpecGeneration {
  readonly modelId: string | null;
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
 * Why a stopped tool call is stopped — and the whole point of the type is that
 * the two arms are NOT flavours of one another.
 *
 * Both draw as a card that has stopped moving, and their remedies are opposite.
 * Both were observed live on the same tool, one path apart:
 *
 * * `write_file("/random.csv")` — outside every attached folder, so
 *   `HostFilesystemRules.build`'s rule 5 (`host_filesystem.py`) is
 *   `mode: "deny"`. deepagents refuses in the tool layer and returns an ordinary
 *   failed `tool_result` whose `output.content` reads
 *   `"Error: permission denied for write on /random.csv"`. The call is OVER; the
 *   remedy is to attach a writable folder.
 * * `write_file(<inside an attached writable root>)` — rule 3 under the default
 *   Manual bypass is `mode: "interrupt"`, so the SAME rule engine parks the run
 *   on a LangGraph interrupt instead. There is NO `tool_result` at all; an
 *   `approval_requested` lands beside the still-open call. The call has not
 *   failed and never will until someone answers; the remedy is a click.
 *
 * That is the wire-level difference: a refusal TERMINATES the call, a gate
 * leaves it open forever and emits an approval next to it. The card could not
 * tell them apart, so a run that was simply waiting read as broken.
 */
export type ToolCallBlock =
  | {
      readonly kind: "decision";
      /** The pending approval — the id `TcWriteGateRow` decides in this run. */
      readonly approvalId: string;
      /** The server's own question ("Allow writing to /a/b.csv?"); null when
       *  the payload carried none, which no lane should but replay can. */
      readonly ask: string | null;
    }
  | {
      readonly kind: "permission";
      /**
       * Which authority was withheld, because that is what decides the remedy.
       * Derived from what the runtime FACTUALLY supplies — `provenance` (only
       * MCP calls carry it) and the deepagents tool name — never from the
       * refusal prose, which is deliberately coarse (see `PdpReason`).
       */
      readonly lane: "filesystem" | "connector";
    };

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
   * Why this call is stopped, when "stopped" is not the same as "broken".
   * Absent for every ordinary call, including an ordinary failure — see
   * `ToolCallBlock` for the two states it separates and why it must.
   */
  readonly blockedBy?: ToolCallBlock;
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
  /**
   * The run this call belongs to — WHICH seq space `sequenceNo` lives in.
   *
   * Without it `sequenceNo` is not an address, it is an offset into an unnamed
   * origin: every run numbers its events from 0, so run A's seq 3 and run B's
   * seq 3 are different moments that sort as the same one. The renderer used to
   * merge every card into a single seq order on that basis, which piled every
   * run's cards onto whichever turn was active — the same collision the message
   * side has always guarded against with `TcChatMessage.run_id`.
   *
   * Null only when the frame carried no `run_id`, which no runtime event should.
   */
  readonly runId: string | null;
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
  surfaceSpecGeneration: new Map(),
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
  const surfaceSpecGeneration = new Map<string, SurfaceSpecGeneration>();
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
      surfaceSpecGeneration,
    });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
    surfaceTabs: buildSurfaceTabs(surfaceState, surfaceMeta),
    surfaceSpecGeneration,
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
  const surfaceSpecGeneration = new Map<string, SurfaceSpecGeneration>();
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
      surfaceSpecGeneration,
    });
  }

  return {
    activity,
    beads,
    chat,
    approvals,
    surfaceState,
    surfaceTabs: buildSurfaceTabs(surfaceState, surfaceMeta),
    surfaceSpecGeneration,
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
  // The strip shows tabs, not progress, so this fold's generation half is
  // discarded. It is still passed rather than made optional: one signature for
  // `applySurfaceEvent` is what keeps this selector byte-identical to
  // `project().surfaceTabs`, which is the property the parity test pins.
  const surfaceSpecGeneration = new Map<string, SurfaceSpecGeneration>();
  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    applySurfaceEvent(event, surfaceState, surfaceMeta, surfaceSpecGeneration);
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
  // Approvals ride the SAME array (FR-3.3 — one projection, one event source),
  // so the gate that parked a call is already here; it was simply never read on
  // this pass.
  const asks: PendingAsk[] = [];
  const settled = new Set<string>();
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
    } else if (event.event_type === "approval_requested") {
      const ask = readPendingAsk(event);
      if (ask !== null) {
        asks.push(ask);
      }
    } else if (event.event_type === "approval_resolved") {
      const approvalId = pickString(event.payload, "approval_id");
      if (approvalId !== null) {
        settled.add(approvalId);
      }
    }
  }
  if (order.length === 0) {
    return EMPTY_TOOL_CALLS;
  }
  // AFTER the reduce, never inside it. "Is this call still open?" is a verdict
  // over the whole stream — a result frame later in the same array settles a
  // call that was open when the approval was requested, and binding mid-loop
  // would park a card the run has since moved past.
  bindPendingDecisions(asks, settled, byCall);
  return order.map((key) => buildToolCall(byCall.get(key)!));
}

/** One `approval_requested` frame, reduced to what can bind it to a call. */
interface PendingAsk {
  readonly approvalId: string;
  /** `payload.tool_name` — the filesystem lane's only handle on the call. */
  readonly toolName: string | null;
  readonly ask: string | null;
}

function readPendingAsk(event: RuntimeEventEnvelope): PendingAsk | null {
  const approvalId =
    pickString(event.payload, "approval_id") ??
    pickString(event.payload, "action_id");
  if (approvalId === null) {
    return null;
  }
  return {
    approvalId,
    toolName: pickString(event.payload, "tool_name"),
    // `message` is what both lanes spell ("Allow writing to /a/b.csv?");
    // `question` is the write gate's copy of it, since a parked write rides the
    // `ask_a_question` wire shape.
    ask:
      pickString(event.payload, "message") ??
      pickString(event.payload, "question"),
  };
}

/**
 * Mark the call each still-pending approval is holding up.
 *
 * The run-wide "something is pending" signal already existed (`TcChat` passes
 * `parked` to every card). What it cannot say is WHICH call the decision is
 * about, so a gated call and a call merely stalled behind someone else's
 * decision read identically. This is that missing per-call fact.
 *
 * Two joins, because the two lanes address the call differently:
 *
 * 1. **Exact.** `PolicyToolMiddleware._approval_id` parks a write on
 *    `mcp_write:<run_id>:<tool_call_id>` (policy_tool.py), deterministically so
 *    the id survives LangGraph's node replay. That trailing segment IS the
 *    `call_id` this projection keys cards on, so the join is identity.
 * 2. **By tool name, and only when unambiguous.** The filesystem lane's payload
 *    (`_FilesystemApproval.payload`, `runtime_worker/stream_events.py`) carries
 *    `tool_name`, `path` and `arguments` but NO call id at all, so the only
 *    handle is "the open call to that tool". With two of them open we DECLINE
 *    to guess and leave the card on the run-wide signal: naming the wrong call
 *    as the one awaiting a decision is worse than naming none, because the
 *    reader would approve believing it settles a different write.
 *
 * And the join that LOOKS available but is not, since the next reader will find
 * it before they find this comment: `source_tool_call_id` is in the approval
 * allow-list (`_approval_requested_payload`) and names a call exactly. It can
 * never be set on a gate. `_source_tool_call_id_for_payload` reads it off a TOOL
 * RESULT message, and a parked call has not produced one — that is the whole
 * definition of parked. It is also stamped only on the explicit-payload branch,
 * which neither lane above takes. Its real subject is `mcp_auth_required`, a
 * result that asks for OAuth — a different stopped state, not this one.
 */
function bindPendingDecisions(
  asks: readonly PendingAsk[],
  settled: ReadonlySet<string>,
  byCall: Map<string, MutableToolCall>,
): void {
  for (const ask of asks) {
    if (settled.has(ask.approvalId)) {
      continue;
    }
    const call = matchAskToCall(ask, byCall);
    // Only an OPEN call can be parked on a decision; a settled one is history,
    // exactly as `ToolCallCard` already reasons about `parked`.
    if (call === undefined || call.status !== "running") {
      continue;
    }
    if (call.blockedBy !== undefined) {
      continue;
    }
    call.blockedBy = {
      kind: "decision",
      approvalId: ask.approvalId,
      ask: ask.ask,
    };
  }
}

function matchAskToCall(
  ask: PendingAsk,
  byCall: Map<string, MutableToolCall>,
): MutableToolCall | undefined {
  const callId = writeGateCallId(ask.approvalId);
  if (callId !== null) {
    return byCall.get(callId);
  }
  if (ask.toolName === null) {
    return undefined;
  }
  let match: MutableToolCall | undefined;
  for (const call of byCall.values()) {
    if (call.status !== "running" || call.toolName !== ask.toolName) {
      continue;
    }
    if (match !== undefined) {
      return undefined;
    }
    match = call;
  }
  return match;
}

/** The `tool_call_id` half of `mcp_write:<run_id>:<tool_call_id>`, or null.
 *
 *  Split on the FIRST colon after the prefix rather than the last: the run id
 *  never contains one, but nothing promises that of LangChain's call id, and
 *  `lastIndexOf` would silently truncate the key it is meant to reproduce. */
function writeGateCallId(approvalId: string): string | null {
  if (!approvalId.startsWith(WRITE_GATE_APPROVAL_PREFIX)) {
    return null;
  }
  const afterPrefix = approvalId.slice(WRITE_GATE_APPROVAL_PREFIX.length);
  const runIdEnd = afterPrefix.indexOf(":");
  if (runIdEnd < 0) {
    return null;
  }
  const callId = afterPrefix.slice(runIdEnd + 1);
  return callId === "" ? null : callId;
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
  readonly surfaceSpecGeneration: Map<string, SurfaceSpecGeneration>;
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
  // `surface_spec_requested` / `surface_spec_generated` open and close a spec
  // generation, and the latter also merges the late spec by URI. Handled in one
  // place so `project()` and the `projectSurfaceTabs` selector stay
  // byte-identical.
  applySurfaceEvent(
    event,
    state.surfaceState,
    state.surfaceMeta,
    state.surfaceSpecGeneration,
  );
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
      // Same contract point as `chatProjection.payloadText`: the worker writes
      // the answer to `payload.message` (RuntimeTextPayload declares `message`,
      // never `text`) and mirrors it into `summary`. Reading only `text` meant
      // this always fell through to the summary fallback.
      const text =
        pickString(event.payload, "text") ??
        pickString(event.payload, "message") ??
        event.summary ??
        "";
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
 * The runtime's "spec generation has started" progress signal.
 *
 * A plain union comparison. This briefly needed a bare-string constant and a
 * widened read, because the literal was added to the Python enum without being
 * mirrored into `RuntimeApiEventType` — and that gap was not cosmetic: the
 * runtime type guard (`isRuntimeApiEventType` → `isRuntimeEventEnvelope`) drops
 * any envelope whose type is not in the closed union, so the event was thrown
 * away by every client BEFORE the projector ever saw it. The workaround
 * typechecked and its tests passed only because they feed `project()` directly,
 * downstream of the guard. The mirror is what makes the feature reachable;
 * this comparison is just what it looks like afterwards.
 */
function isSpecGenerationRequested(event: RuntimeEventEnvelope): boolean {
  return event.event_type === "surface_spec_requested";
}

/** Run terminals — the outer bound on every in-flight model call in the run. */
function isRunTerminal(event: RuntimeEventEnvelope): boolean {
  return (
    event.event_type === "run_completed" ||
    event.event_type === "run_failed" ||
    event.event_type === "run_cancelled"
  );
}

/**
 * Apply one event's surface effect into `surfaceState` + `surfaceMeta` +
 * `surfaceSpecGeneration`.
 *
 * - `tool_result` / `draft_updated` / `presentation_updated`: merge the surface
 *   payload (`{spec?, data}` from the envelope, or a legacy flat state object).
 * - `surface_spec_requested`: open a generation for the surface. Purely a
 *   progress signal — an absent event is indistinguishable from today (the map
 *   simply stays empty), so nothing downstream may require it.
 * - `surface_spec_generated`: close that generation AND merge ONLY the `spec`
 *   key, so a late spec upgrades the surface in place and never clobbers newer
 *   `data` (D4). Replay-idempotent because the caller deduplicates by
 *   `event_id` and the writes are keyed.
 * - a run terminal: close every generation still open. A model call that dies
 *   with its run never emits its own terminal event, and a "generating…" state
 *   that outlives the run is a claim the reader cannot dismiss.
 */
function applySurfaceEvent(
  event: RuntimeEventEnvelope,
  surfaceState: Map<string, SurfacePayload>,
  surfaceMeta: Map<string, SurfaceMeta>,
  surfaceSpecGeneration: Map<string, SurfaceSpecGeneration>,
): void {
  if (isSpecGenerationRequested(event)) {
    // The contract's key is `surface_id`; `extractSurfaceUri` is the fallback
    // because the two are the SAME identity — a surface's mount/tab URI IS its
    // `surface_id` (the identity note in `ledgerProjection.ts`), which is also
    // why this can share a map key with `surface_spec_generated`'s `surface_uri`.
    // Unattributed (`null`) ⇒ record nothing: no surface can honestly claim a
    // generation the runtime declined to name, and inventing a run-wide flag
    // would light up whichever surface happened to be open.
    const named = pickString(event.payload, "surface_id");
    const uri =
      named !== null && named !== "" ? named : extractSurfaceUri(event);
    if (uri === undefined || uri === "") {
      return;
    }
    surfaceSpecGeneration.set(uri, {
      modelId: pickString(event.payload, "model_id"),
    });
    return;
  }

  if (isRunTerminal(event)) {
    surfaceSpecGeneration.clear();
    return;
  }

  if (event.event_type === "surface_spec_generated") {
    const uri = extractSurfaceUri(event);
    if (uri === undefined) {
      return;
    }
    // Close the generation BEFORE validating the spec: the model call is over
    // either way, and only the upgrade is in doubt. Closing after the guard
    // would leave a malformed spec showing "generating…" for the rest of the run.
    surfaceSpecGeneration.delete(uri);
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
  runId: string | null;
  createdAtMs: number | null;
  /**
   * Set by `bindPendingDecisions` only, which runs once every tool frame has
   * been reduced — which is why none of the three reducers below carries it
   * forward. Nothing rebuilds this entry after the bind, so there is nothing
   * for a `byCall.set` to overwrite.
   */
  blockedBy?: ToolCallBlock;
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
    // Same unwrapping as the delta path. A `tool_call_started` frame normally
    // carries `{}` (streamed tools) or the real arguments (`read_file` in the
    // capture emitted no deltas at all), and both pass through unchanged — but
    // routing them through one function keeps a started frame that ever carries
    // the streaming envelope from re-introducing the escaped-JSON card.
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
    // The started frame is the earliest, so it wins the anchor when present.
    sequenceNo: prior?.sequenceNo ?? event.sequence_no,
    // Pinned to the frame that OPENED the call, like `sequenceNo` — a later
    // result frame cannot move a card into another run.
    runId: prior?.runId ?? event.run_id ?? null,
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
    // Pinned to the frame that OPENED the call, like `sequenceNo` — a later
    // result frame cannot move a card into another run.
    runId: prior?.runId ?? event.run_id ?? null,
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
      plainTextToolError(event) ??
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
    // Pinned to the frame that OPENED the call, like `sequenceNo` — a later
    // result frame cannot move a card into another run.
    runId: prior?.runId ?? event.run_id ?? null,
    createdAtMs: prior?.createdAtMs ?? parseMs(event.created_at),
  });
}

function buildToolCall(m: MutableToolCall): ToolCallEntry {
  // A decision is bound only to an OPEN call and a denial only to an errored
  // one, so the two arms cannot both apply; the `??` states that rather than
  // relying on it.
  const blockedBy = m.blockedBy ?? permissionBlock(m);
  return {
    id: m.key,
    toolName: m.toolName,
    title: m.title ?? m.toolName,
    status: m.status,
    ...(m.args !== undefined ? { args: m.args } : {}),
    ...(m.result !== undefined ? { result: m.result } : {}),
    ...(m.summary !== undefined ? { summary: m.summary } : {}),
    ...(m.errorMessage !== undefined ? { errorMessage: m.errorMessage } : {}),
    ...(blockedBy !== undefined ? { blockedBy } : {}),
    ...(m.provenance !== undefined ? { provenance: m.provenance } : {}),
    ...(m.accessMode !== undefined ? { accessMode: m.accessMode } : {}),
    ...(m.durationMs !== undefined ? { durationMs: m.durationMs } : {}),
    ...(m.subagentTaskIds !== undefined
      ? { subagentTaskIds: m.subagentTaskIds }
      : {}),
    sequenceNo: m.sequenceNo,
    runId: m.runId,
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
 * Intermediate tool-call deltas carry `args` as a streaming-JSON envelope —
 * `{delta: "<the argument JSON so far>"}` — not as the arguments themselves.
 * `resolveToolArgs` unwraps it once the accumulated string parses and holds the
 * last good parse while it is still a prefix; see `streamedToolArgs.ts` for the
 * captured frame sequence this is written against.
 *
 * This only ever affected a call IN FLIGHT: the runtime's final delta carries
 * the parsed object, so a settled card already read `file_path` /
 * `old_string` / `new_string` correctly. Storing the envelope verbatim is what
 * made a streaming call render escaped JSON nested in a JSON string.
 *
 * `args_delta` is still accepted for forward-compatible partial updates.
 */
function updatedToolArgs(
  event: RuntimeEventEnvelope,
  prior: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  const snapshot = readRecord(event.payload?.["args"]);
  if (snapshot !== undefined) {
    return resolveToolArgs(snapshot, prior);
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

/**
 * The failure sentence a tool wrote as PLAIN TEXT in `output.content`.
 *
 * `readStructuredToolError` above covers the two structured shapes — an
 * `output.error` record, and a JSON string under `output.content` carrying one.
 * A tool that simply returns `"Error: permission denied for write on /x.csv"`
 * matches neither, so its card had no error line at all and fell back to the
 * backend's generic summary ("0xCopilot couldn't complete this step").
 *
 * That was measured, not theorised: a live run's `tool_result` frame carried
 * exactly that sentence with `error_message` and `safe_message` both null, and
 * the one line that would have told the reader they needed a folder grant never
 * reached the screen.
 *
 * SAFETY. This is a last resort, and it is deliberately narrow:
 *   • only on a FAILED result — a success `content` is the tool's ANSWER, and
 *     printing it on the error line would invent a failure;
 *   • only when nothing structured was found, so a curated `safe_message`
 *     always wins and this can never override redacted copy with raw text;
 *   • bounded, and single-line — the header is one row and a paragraph would
 *     push the card's own controls out of reach.
 *
 * It discloses nothing new: the same string already renders verbatim in the
 * card's raw-payload disclosure. The bug was that it was buried there.
 */
function plainTextToolError(event: RuntimeEventEnvelope): string | undefined {
  if (!isFailedToolResult(event)) return undefined;
  const content = readRecord(event.payload?.["output"])?.["content"];
  if (typeof content !== "string") return undefined;
  const line = content.trim().split("\n", 1)[0]?.trim() ?? "";
  if (line === "") return undefined;
  // The card already renders this in the error style, so the tool's own
  // "Error:" prefix is a second label for the same fact.
  const stripped = line.replace(/^error:\s*/i, "").trim();
  const text = stripped === "" ? line : stripped;
  return text.length > PLAIN_TOOL_ERROR_CAP
    ? `${text.slice(0, PLAIN_TOOL_ERROR_CAP)}…`
    : text;
}

const PLAIN_TOOL_ERROR_CAP = 200;

/** A terminal tool frame the runtime marked as failed. */
function isFailedToolResult(event: RuntimeEventEnvelope): boolean {
  const status =
    pickString(event.payload, "status") ??
    (typeof event.status === "string" ? event.status : null);
  return status === "failed" || status === "error";
}

function toolErrorMessage(error: Record<string, unknown>): string | undefined {
  return (
    pickString(error, "safe_message") ??
    pickString(error, "message") ??
    undefined
  );
}

/** deepagents' built-in filesystem tools — the ONLY ones a `FilesystemPermission`
 *  rule can refuse. Kept verbatim from the backend's own list
 *  (`_FilesystemApproval.TOOL_OPERATIONS`, `runtime_worker/stream_events.py`);
 *  a tool outside it that says "permission denied" was refused by something
 *  else, and telling that reader to attach a folder would be a wrong answer
 *  delivered confidently. */
const DEEPAGENTS_FILESYSTEM_TOOLS: ReadonlySet<string> = new Set([
  "ls",
  "read_file",
  "glob",
  "grep",
  "write_file",
  "edit_file",
]);

/**
 * A refusal-for-want-of-authority, told apart from an ordinary failure.
 *
 * The test is the PROSE, and that is a deliberate, narrow exception to this
 * file's rule of reading declared fields. No typed field carries it: a
 * deepagents `mode: "deny"` refusal is built by the library itself as
 * `ToolMessage(content=f"Error: permission denied for write on {path}",
 * status="error")` (`deepagents/middleware/filesystem.py`, the same literal in
 * every read and write arm), so it reaches us as a plain failed `tool_result`
 * whose `output.content` is one English sentence — the fixture in
 * `plainToolError.test.ts` is transcribed from a real packaged run and has no
 * `error_code` and no `safe_message`. The PDP then deliberately COARSENS every
 * scope-miss, allowlist-miss and workspace BLOCK into the same two words
 * (`PdpReason`, `capabilities/mcp/middleware/policy_tool.py`). So the two words
 * are the signal the wire actually has.
 *
 * `error` only, never `unavailable`. A capability DECLINED by policy is already
 * a well-formed answer carrying its own instruction ("Create an artifact or
 * download instead"; `WorkspacePolicyAnswers`), and the runtime classifies it
 * `unavailable` precisely to keep it out of the failure taxonomy. Appending a
 * second, coarser remedy to a sentence that already has the right one would
 * make the better copy read like a footnote to the worse.
 *
 * What is NOT guessed from prose is the remedy: `lane` comes from `provenance`
 * (which only an MCP call carries) and the tool name. A denial we cannot place
 * on a lane yields nothing, and the card keeps showing the reason alone —
 * strictly what it does today.
 */
function permissionBlock(m: MutableToolCall): ToolCallBlock | undefined {
  if (m.status !== "error" || m.errorMessage === undefined) {
    return undefined;
  }
  if (!PERMISSION_DENIAL.test(m.errorMessage)) {
    return undefined;
  }
  if (m.provenance !== undefined) {
    return { kind: "permission", lane: "connector" };
  }
  if (DEEPAGENTS_FILESYSTEM_TOOLS.has(m.toolName)) {
    return { kind: "permission", lane: "filesystem" };
  }
  return undefined;
}

/** No `g` flag: a global regex carries `lastIndex` between calls, so the same
 *  message would match and then miss on the next card. */
const PERMISSION_DENIAL = /permission denied/i;

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
