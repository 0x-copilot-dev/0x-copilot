// Pure adapter functions for the Team destination data binder
// (P12-C). Wire types in, presentation row shapes out — no React,
// no side effects.

import type { Person, TeamStreamEnvelope, UserId } from "@0x-copilot/api-types";

// ---------------------------------------------------------------------------
// Stream merge
// ---------------------------------------------------------------------------

/**
 * Apply a single `TeamStreamEnvelope` to the in-memory people list. Pure;
 * returns the next array (or the same reference when the event is a
 * heartbeat / unknown shape). Mirrors `applyToolEnvelope` /
 * `applyConnectorEnvelope`.
 */
export function applyTeamEnvelope(
  current: ReadonlyArray<Person>,
  envelope: TeamStreamEnvelope,
): ReadonlyArray<Person> {
  if (envelope.event_type === "heartbeat") {
    return current;
  }
  if (envelope.person === undefined) {
    return current;
  }
  const next = current.slice();
  const idx = next.findIndex((p) => p.id === envelope.person?.id);
  if (idx === -1) {
    if (envelope.event_type === "team.offboarded") {
      // Offboarded user; not in list — ignore.
      return current;
    }
    return [envelope.person, ...next];
  }
  if (envelope.event_type === "team.offboarded") {
    // Drop the row from the catalog — keep the rest.
    next.splice(idx, 1);
    return next;
  }
  next[idx] = envelope.person;
  return next;
}

// ---------------------------------------------------------------------------
// List row projection
// ---------------------------------------------------------------------------

export interface TeamListRow {
  readonly id: UserId;
  readonly name: string;
  readonly email: string;
  readonly role: Person["role"];
  readonly presence: Person["presence"];
  readonly last_seen_label: string | null;
  readonly is_self: boolean;
}

export function personToListRow(p: Person): TeamListRow {
  return {
    id: p.id,
    name: p.display_name,
    email: p.email,
    role: p.role,
    presence: p.presence,
    last_seen_label: p.last_seen_at,
    is_self: p.is_self,
  };
}
