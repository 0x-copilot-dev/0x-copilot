// Pure adapter functions for the Memory destination data binder
// (P12-C). Wire envelopes / wire rows in — view shapes out, no React.

import type {
  MemoryItem,
  MemoryItemId,
  MemoryProposal,
  MemoryStreamEnvelope,
} from "@enterprise-search/api-types";

// ---------------------------------------------------------------------------
// Stream merge (memory items)
// ---------------------------------------------------------------------------

/**
 * Apply a `MemoryStreamEnvelope` to the in-memory items list. Pure;
 * returns the next array (or the same reference when the event is a
 * heartbeat / unknown shape). Mirrors `applyTeamEnvelope`.
 */
export function applyMemoryEnvelope(
  current: ReadonlyArray<MemoryItem>,
  envelope: MemoryStreamEnvelope,
): ReadonlyArray<MemoryItem> {
  switch (envelope.event_type) {
    case "memory.created":
    case "memory.updated": {
      if (envelope.item === undefined) {
        return current;
      }
      const idx = current.findIndex((m) => m.id === envelope.item?.id);
      if (idx === -1) {
        return [envelope.item, ...current];
      }
      const next = current.slice();
      next[idx] = envelope.item;
      return next;
    }
    case "memory.deleted": {
      if (envelope.deleted_id === undefined) {
        return current;
      }
      return current.filter((m) => m.id !== envelope.deleted_id);
    }
    case "memory.proposal_appended":
    case "memory.proposal_decided":
    case "heartbeat":
      return current;
  }
}

// ---------------------------------------------------------------------------
// Stream merge (proposals)
// ---------------------------------------------------------------------------

export function applyProposalEnvelope(
  current: ReadonlyArray<MemoryProposal>,
  envelope: MemoryStreamEnvelope,
): ReadonlyArray<MemoryProposal> {
  switch (envelope.event_type) {
    case "memory.proposal_appended": {
      if (envelope.proposal === undefined) return current;
      const exists = current.some((p) => p.id === envelope.proposal?.id);
      if (exists) return current;
      return [envelope.proposal, ...current];
    }
    case "memory.proposal_decided": {
      if (envelope.proposal === undefined) return current;
      const idx = current.findIndex((p) => p.id === envelope.proposal?.id);
      if (idx === -1) return current;
      const next = current.slice();
      next[idx] = envelope.proposal;
      return next;
    }
    case "memory.created":
    case "memory.updated":
    case "memory.deleted":
    case "heartbeat":
      return current;
  }
}

// ---------------------------------------------------------------------------
// List row projection
// ---------------------------------------------------------------------------

export interface MemoryListRow {
  readonly id: MemoryItemId;
  readonly title: string;
  readonly kind: MemoryItem["kind"];
  readonly scope: MemoryItem["scope"];
  readonly tags: ReadonlyArray<string>;
  readonly last_used_at: string | null;
  readonly created_at: string;
}

export function memoryToListRow(m: MemoryItem): MemoryListRow {
  return {
    id: m.id,
    title: m.title,
    kind: m.kind,
    scope: m.scope,
    tags: m.tags,
    last_used_at: m.last_used_at,
    created_at: m.created_at,
  };
}
