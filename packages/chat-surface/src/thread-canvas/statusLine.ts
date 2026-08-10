// Status-strip selector (Generative Surfaces v2, PRD-B2 D6 / FR-F2).
//
// `projectStatusLine` answers ONE question over the run's events: is a surface
// still being built? Nothing else. It is a pure peer of `projectProvenance` over
// the same `session.events` array (one-projector invariant), deterministic and
// total — malformed payloads degrade, never throw.
//
// It used to mirror "the run's latest consequential ledger beat" as
// `event_type · connector.op · ledgerId`, which drew a settled surface as
//
//     view.derived · incidents.list_incidents · r252·010
//
// Three things were wrong with that, and they are why this file is now small.
// The op was already in the provenance footer one line above. The ledger id was
// already there too, one sequence earlier, and neither coordinate means anything
// to a reader. And `view.derived` — the only claim the line added — is what the
// Generic/Shaped toggle between them already says in English, better, because
// you can act on it. A settled surface has nothing left for this strip to say,
// so it now says nothing.
//
// What remains is the one state no other element covers: the gap between a
// surface being declared and its view landing.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

export interface StatusStripLine {
  readonly kind: "idle" | "assembling";
}

const IDLE: StatusStripLine = { kind: "idle" };
const ASSEMBLING: StatusStripLine = { kind: "assembling" };

function surfaceIdOf(event: RuntimeEventEnvelope): string {
  const payload = event.payload;
  if (payload === null || typeof payload !== "object") return "";
  const raw = (payload as Record<string, unknown>).surface_id;
  return typeof raw === "string" ? raw : "";
}

/**
 * `assembling` while any surface has been declared (`surface.created`) but has
 * not yet reached a view (`view.derived`); `idle` otherwise — including the
 * settled case, where the strip renders nothing at all.
 *
 * Order-independent by construction: this is set membership, not a fold over
 * the latest event, so the events array needs no sort and a late-arriving
 * `surface.created` cannot make a finished surface look unfinished.
 */
export function projectStatusLine(
  events: readonly RuntimeEventEnvelope[],
): StatusStripLine {
  const created = new Set<string>();
  const viewed = new Set<string>();

  for (const event of events) {
    const surfaceId = surfaceIdOf(event);
    if (surfaceId === "") continue;
    if (event.event_type === "surface.created") {
      created.add(surfaceId);
    } else if (event.event_type === "view.derived") {
      viewed.add(surfaceId);
    }
  }

  for (const surfaceId of created) {
    if (!viewed.has(surfaceId)) return ASSEMBLING;
  }
  return IDLE;
}
