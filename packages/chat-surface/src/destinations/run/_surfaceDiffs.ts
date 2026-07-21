// PRD-04 — surface-diff projection off the SINGLE run event stream.
//
// Source: docs/plan/generative-ui/PRD-04-cockpit-wiring.md (Scope + Behavior),
// PRD-01 `SurfaceEnvelope` (`packages/api-types`), and the one-projection rule
// (`packages/chat-surface/CLAUDE.md` §"The Run cockpit").
//
// A PURE selector over the canonical `RuntimeEventEnvelope[]` that
// `useRunSession` owns — the SAME array `ThreadCanvas` feeds `useEventProjector`
// and `projectApprovals` / `projectSubagents` read. It opens NO SSE subscription
// and instantiates NO second projector; `RunDestination` memoizes it against
// `session.events` and threads the active surface's diff into `TcSurfaceMount`
// (via `ThreadCanvas.pendingDiff`) so the on-surface Approve / Reject controls
// render over the proposed change.
//
// Shape (mirrors `projectApprovals`): an `approval_requested` whose payload
// carries a `surface` envelope WITH a `diff` opens a pending surface diff keyed
// by its `approval_id`; the matching `approval_resolved` settles it. The
// consumer overlays optimistic local decisions (`localDecisions`, keyed by the
// same `approval_id === diffId`) so a just-approved diff clears before the
// trailing SSE frame — the decision path is the EXISTING `resolveApproval`
// machinery in `RunDestination`, not a fork.
//
// TODO(merge): the `payload.surface` envelope is the frozen PRD-01 contract
// (`SurfaceEnvelope`); we read it defensively here (untrusted tool output) and
// keep this local until a shared reducer is warranted — same discipline as
// `approvalProjection` / `_approvals-stub`.

import type { RuntimeEventEnvelope, SurfaceDiff } from "@0x-copilot/api-types";

/**
 * One pending, unresolved surface diff. `diffId` IS the approval id — the
 * on-surface Approve / Reject fires `resolveApproval(diffId, …)`, the same POST
 * the in-chat `ApprovalCard` uses.
 */
export interface SurfaceDiffEntry {
  readonly diffId: string;
  readonly uri: string;
  readonly diff: SurfaceDiff;
  /** Verb-first label for the pending change (best-effort from the payload). */
  readonly title: string;
  /** Where the change originates (server name / uri scheme); display-only. */
  readonly provenance: string;
  /** `sequence_no` of the requesting event — orders latest-wins. */
  readonly sequenceNo: number;
}

export interface SurfaceDiffProjection {
  /**
   * Latest UNRESOLVED diff per surface URI, newest first (`sequenceNo` desc).
   * At most one entry per URI — a newer proposal supersedes an older one on the
   * same surface.
   */
  readonly diffs: readonly SurfaceDiffEntry[];
}

const EMPTY_PROJECTION: SurfaceDiffProjection = { diffs: [] };

const APPROVAL_REQUESTED = "approval_requested";
const APPROVAL_RESOLVED = "approval_resolved";

interface MutableEntry {
  diffId: string;
  uri: string;
  diff: SurfaceDiff;
  title: string;
  provenance: string;
  sequenceNo: number;
  resolved: boolean;
}

/**
 * Reduce the ordered run event list into pending surface diffs.
 *
 * Idempotent on replay (deduplicates by `event_id`). Callers pass events in
 * ascending `sequence_no` order — the append-only array `useRunSession`
 * exposes — so a single `useMemo(() => projectSurfaceDiffs(events), [events])`
 * recomputes only when the stream grows.
 */
export function projectSurfaceDiffs(
  events: readonly RuntimeEventEnvelope[],
): SurfaceDiffProjection {
  if (events.length === 0) {
    return EMPTY_PROJECTION;
  }

  const seen = new Set<string>();
  const byId = new Map<string, MutableEntry>();
  const order: string[] = [];

  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);

    if (event.event_type === APPROVAL_REQUESTED) {
      reduceRequested(event, byId, order);
    } else if (event.event_type === APPROVAL_RESOLVED) {
      reduceResolved(event, byId);
    }
  }

  if (order.length === 0) {
    return EMPTY_PROJECTION;
  }

  // Latest unresolved per URI.
  const latestPerUri = new Map<string, MutableEntry>();
  for (const id of order) {
    const entry = byId.get(id);
    if (entry === undefined || entry.resolved) {
      continue;
    }
    const prior = latestPerUri.get(entry.uri);
    if (prior === undefined || entry.sequenceNo >= prior.sequenceNo) {
      latestPerUri.set(entry.uri, entry);
    }
  }

  const diffs = [...latestPerUri.values()]
    .sort((a, b) => b.sequenceNo - a.sequenceNo)
    .map(freeze);
  return diffs.length === 0 ? EMPTY_PROJECTION : { diffs };
}

// --- reducers --------------------------------------------------------------

function reduceRequested(
  event: RuntimeEventEnvelope,
  byId: Map<string, MutableEntry>,
  order: string[],
): void {
  const approvalId = stringField(event.payload.approval_id);
  if (approvalId === null) {
    return;
  }
  const surface = readSurfaceDiff(event);
  if (surface === null) {
    return;
  }
  if (!byId.has(approvalId)) {
    order.push(approvalId);
  }
  const existing = byId.get(approvalId);
  byId.set(approvalId, {
    diffId: approvalId,
    uri: surface.uri,
    diff: surface.diff,
    title:
      stringField(event.payload.display_name) ??
      stringField(event.payload.tool_name) ??
      event.display_title ??
      existing?.title ??
      "Proposed changes",
    provenance:
      stringField(event.payload.server_name) ??
      stringField(event.payload.server_id) ??
      schemeOf(surface.uri) ??
      existing?.provenance ??
      "agent",
    sequenceNo: existing?.sequenceNo ?? event.sequence_no,
    resolved: existing?.resolved ?? false,
  });
}

function reduceResolved(
  event: RuntimeEventEnvelope,
  byId: Map<string, MutableEntry>,
): void {
  const approvalId = stringField(event.payload.approval_id);
  if (approvalId === null) {
    return;
  }
  const existing = byId.get(approvalId);
  if (existing !== undefined) {
    existing.resolved = true;
  }
}

// --- shaping ---------------------------------------------------------------

function freeze(entry: MutableEntry): SurfaceDiffEntry {
  return {
    diffId: entry.diffId,
    uri: entry.uri,
    diff: entry.diff,
    title: entry.title,
    provenance: entry.provenance,
    sequenceNo: entry.sequenceNo,
  };
}

// --- payload readers -------------------------------------------------------

/**
 * Read the PRD-01 `payload.surface` envelope and pull out a `{uri, diff}` pair,
 * but ONLY when the envelope actually carries a diff (`changes` present).
 * Returns `null` otherwise — an approval without a surface diff is out of scope
 * here (the in-chat `ApprovalCard` handles it via `projectApprovals`).
 */
function readSurfaceDiff(
  event: RuntimeEventEnvelope,
): { uri: string; diff: SurfaceDiff } | null {
  const surface = event.payload.surface;
  if (surface === null || typeof surface !== "object") {
    return null;
  }
  const record = surface as Record<string, unknown>;
  const uri = stringField(record.surface_uri);
  if (uri === null) {
    return null;
  }
  const diff = record.diff;
  if (diff === null || typeof diff !== "object") {
    return null;
  }
  const changes = (diff as Record<string, unknown>).changes;
  if (!Array.isArray(changes)) {
    return null;
  }
  return { uri, diff: diff as SurfaceDiff };
}

function schemeOf(uri: string): string | null {
  const idx = uri.indexOf("://");
  return idx > 0 ? uri.slice(0, idx) : null;
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}
