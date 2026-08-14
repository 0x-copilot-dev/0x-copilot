// projectSteerNotes — the user's mid-run interjections, projected off the SAME
// `session.events` every other cockpit selector reads (FR-3.3: one event source,
// pure selectors over it, no second subscription or projector).
//
// WHAT THIS EXISTS FOR
// --------------------
// `POST /v1/agent/runs/{run_id}/steer` is a durable queued command: it is
// accepted while the run is executing, claimed even when every execution slot is
// busy, and delivered at the next model step (never mid-tool). Acceptance and
// delivery are therefore separated in time, and the ONLY durable record that the
// user intervened at all is the `run_steered` event the coordinator appends
// inside the run's causal prefix.
//
// Without this selector that record exists on the wire and nowhere on screen:
// the user's own words, sent into their own run, absent from their own
// transcript. The steer still lands — the agent visibly changes course a beat
// later — but the transcript reads as the agent spontaneously changing its mind.
//
// THE SHAPE IS THE SERVER'S CHOICE, NOT OURS
// ------------------------------------------
// The runtime classifies this event as `activity_kind: "note"` and says why, at
// `runtime_api/schemas/events.py`: routing a user interjection through the prose
// lane "would render the user's words as something the agent said", and an
// `event` is "a state merge with no place on the timeline, which is the one
// thing this event must have: the record has to show *when* the user intervened,
// in line, between the beats it changed."
//
// So this is an INLINE IN-THREAD LINE, not a card and not a chat bubble. It is
// interleaved by `sequence_no` like every other family, which puts it exactly at
// the beat it was accepted at.
//
// THE LABEL IS THE SERVER'S
// -------------------------
// `summary` is set at the emit site (`RunCoordinator.steer_run` →
// `Messages.Event.RUN_STEERED`), the same way the compaction sibling takes its
// `display_title` from the presentation boundary. No client derives a timeline
// label from an event-name prefix; we read the sentence the server wrote.
//
// CONTENTLESSNESS IS RE-CHECKED HERE
// ----------------------------------
// `_run_steered_payload` already refuses to record a note whose payload lost the
// steer, and deliberately raises instead of swallowing it — "a steer note that
// loses its payload is an inline 'you steered Atlas' line with nothing in it".
// The guard is repeated on this side because this projection also runs over
// replayed history, and a row that predates that rule would otherwise draw a
// line in the transcript announcing an interjection whose words are gone.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

/** The wire event this selector folds. */
const STEER_EVENT_TYPE = "run_steered";

/**
 * Neutral fallback for a note whose envelope carried no server summary. The
 * user's own text still renders beside it, so the row is never contentless —
 * this only replaces the sentence that frames it.
 */
const FALLBACK_LABEL = "You steered this run.";

/** One accepted steer, ready to interleave into the transcript. */
export interface SteerNoteEntry {
  /** The `run_steered` event id — the row's stable key. */
  readonly eventId: string;
  /** The run the steer was addressed to; `null` when the envelope named none. */
  readonly runId: string | null;
  /**
   * `sequence_no` of the note. The interleave is ordered on this and nothing
   * else: the coordinator appends the note before it enqueues the command, so
   * this seq is the exact beat in the run at which the user intervened — not
   * where the model eventually acted on it.
   */
  readonly seq: number;
  /** The server-written sentence framing the row. Never re-derived here. */
  readonly label: string;
  /** The user's own words. Always non-empty — an empty steer is dropped. */
  readonly text: string;
  /**
   * The server's id for this steer. Carried so a client holding an optimistic
   * echo can reconcile it against the durable note without a refetch, exactly
   * as `SteerRunResponse.steer_id` is documented to allow.
   */
  readonly steerId: string | null;
}

/** Read a non-empty string off a payload, else `null`. */
function readText(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * Project every steer note on `events`, in `sequence_no` order.
 *
 * Deduped by `event_id`: a replay tail stitched onto a live subscription can
 * deliver the same envelope twice, and two rows for one steer reads as the user
 * having said the same thing twice.
 */
export function projectSteerNotes(
  events: readonly RuntimeEventEnvelope[],
): readonly SteerNoteEntry[] {
  const byEventId = new Map<string, SteerNoteEntry>();
  for (const event of events) {
    if (event.event_type !== STEER_EVENT_TYPE) continue;
    if (byEventId.has(event.event_id)) continue;
    const payload =
      event.payload !== null && typeof event.payload === "object"
        ? (event.payload as Record<string, unknown>)
        : {};
    // `SteerNotePayload` nests the message under `steer` — the SAME object the
    // queued command carries, "not a transcript-only copy". Read it there and
    // nowhere else: a flattened `payload.text` would be a shape this producer
    // never emits, and accepting it would let a future producer bug render as
    // a working row.
    const steer = payload.steer;
    if (steer === null || typeof steer !== "object") continue;
    const message = steer as Record<string, unknown>;
    const text = readText(message, "text");
    if (text === null) continue;
    byEventId.set(event.event_id, {
      eventId: event.event_id,
      runId: event.run_id ?? null,
      seq: event.sequence_no,
      label: event.summary ?? FALLBACK_LABEL,
      text,
      steerId: readText(message, "steer_id"),
    });
  }
  return [...byEventId.values()].sort((left, right) => left.seq - right.seq);
}
