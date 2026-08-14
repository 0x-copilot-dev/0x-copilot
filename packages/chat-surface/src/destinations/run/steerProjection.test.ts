import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { projectSteerNotes } from "./steerProjection";

/**
 * One `run_steered` envelope, shaped as the coordinator writes it:
 * `SteerNotePayload(steer=SteeringMessage(...))` nested under `steer`, with the
 * summary the emit site sets (`Messages.Event.RUN_STEERED`).
 */
function steerEvent(
  payload: Record<string, unknown>,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: "evt-steer",
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: 14,
    event_type: "run_steered" as RuntimeApiEventType,
    activity_kind: "note",
    summary: "You steered this run.",
    created_at: "2026-08-14T10:00:00.000Z",
    payload,
    ...overrides,
  } as RuntimeEventEnvelope;
}

const STEER = {
  steer: {
    steer_id: "steer_abc",
    text: "Use the staging table, not production.",
    requested_by_user_id: "user-1",
    created_at: "2026-08-14T10:00:00.000Z",
  },
};

describe("projectSteerNotes", () => {
  it("projects the note the coordinator appends, keeping the SERVER's sentence verbatim", () => {
    const [note] = projectSteerNotes([steerEvent(STEER)]);
    // `summary` is written at the emit site. No client derives a timeline label
    // from an event-name prefix — this reads the sentence the server wrote.
    expect(note.label).toBe("You steered this run.");
    expect(note.text).toBe("Use the staging table, not production.");
    expect(note.eventId).toBe("evt-steer");
    expect(note.runId).toBe("run-1");
    expect(note.steerId).toBe("steer_abc");
    // The note's OWN position: the coordinator appends it BEFORE it enqueues the
    // command, so this is the beat the user intervened at, not the beat the
    // model eventually acted on it.
    expect(note.seq).toBe(14);
  });

  it("ignores every event that is not a steer note", () => {
    expect(
      projectSteerNotes([
        steerEvent(STEER, {
          event_id: "evt-other",
          event_type: "model_delta" as RuntimeApiEventType,
        }),
      ]),
    ).toHaveLength(0);
  });

  it("reads the message under `steer` and nowhere else", () => {
    // A flattened `payload.text` is a shape this producer never emits. Accepting
    // one would let a future producer bug render as a working row.
    expect(projectSteerNotes([steerEvent({ text: "flattened" })])).toHaveLength(
      0,
    );
  });

  it("drops a note whose payload lost its text rather than draw an empty aside", () => {
    // `_run_steered_payload` already refuses to record one, and RAISES rather
    // than swallowing it. The guard is repeated here because this projection
    // also runs over replayed history: a row that predates that rule would
    // otherwise draw a line announcing an interjection whose words are gone.
    expect(
      projectSteerNotes([
        steerEvent({ steer: { steer_id: "s", requested_by_user_id: "u" } }),
        steerEvent(
          { steer: { ...STEER.steer, text: "" } },
          {
            event_id: "evt-empty",
          },
        ),
      ]),
    ).toHaveLength(0);
  });

  it("falls back to a neutral sentence when the envelope carried no summary", () => {
    const [note] = projectSteerNotes([
      steerEvent(STEER, { summary: null } as Partial<RuntimeEventEnvelope>),
    ]);
    // The user's own text still renders beside it, so the row is never
    // contentless — only the sentence framing it is replaced.
    expect(note.label).toBe("You steered this run.");
    expect(note.text).toBe("Use the staging table, not production.");
  });

  it("dedupes by event id — a replay tail stitched onto a live stream delivers twice", () => {
    const notes = projectSteerNotes([steerEvent(STEER), steerEvent(STEER)]);
    // Two rows for one steer reads as the user having said the same thing twice.
    expect(notes).toHaveLength(1);
  });

  it("orders on `sequence_no`, not arrival order", () => {
    const notes = projectSteerNotes([
      steerEvent(STEER, { event_id: "evt-b", sequence_no: 30 }),
      steerEvent(STEER, { event_id: "evt-a", sequence_no: 12 }),
    ]);
    expect(notes.map((note) => note.eventId)).toEqual(["evt-a", "evt-b"]);
  });
});
