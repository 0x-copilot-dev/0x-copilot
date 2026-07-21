import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectChatMessages } from "./chatProjection";

/** Minimal envelope factory — only the fields projectChatMessages reads. */
function ev(
  partial: Partial<RuntimeEventEnvelope> & {
    event_type: string;
    sequence_no: number;
  },
): RuntimeEventEnvelope {
  const { event_id, sequence_no, event_type, created_at, payload, ...rest } =
    partial;
  return {
    event_id: event_id ?? `e${sequence_no}`,
    sequence_no,
    event_type,
    created_at:
      created_at ??
      new Date(1_700_000_000_000 + sequence_no * 1000).toISOString(),
    payload: payload ?? {},
    ...rest,
  } as RuntimeEventEnvelope;
}

describe("projectChatMessages", () => {
  it("returns nothing before any assistant output", () => {
    expect(projectChatMessages([])).toEqual([]);
    expect(
      projectChatMessages([ev({ event_type: "run_started", sequence_no: 1 })]),
    ).toEqual([]);
  });

  it("coalesces model_delta tokens into one running assistant message", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { text: "Hel" },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 2,
        payload: { text: "lo " },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 3,
        payload: { text: "there" },
      }),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("assistant");
    expect(messages[0].parts).toEqual([
      { type: "text", text: "Hello there", status: { type: "running" } },
    ]);
  });

  it("finalizes to complete on final_response, using its canonical text", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { text: "Hi" },
      }),
      ev({
        event_type: "final_response",
        sequence_no: 2,
        event_id: "final-1",
        payload: { text: "Hi — done." },
      }),
    ]);
    expect(messages[0].message_id).toBe("final-1");
    expect(messages[0].parts).toEqual([
      { type: "text", text: "Hi — done.", status: { type: "complete" } },
    ]);
  });

  it("falls back to summary when final_response carries no payload text", () => {
    const messages = projectChatMessages([
      ev({ event_type: "model_delta", sequence_no: 1, payload: { text: "x" } }),
      ev({
        event_type: "final_response",
        sequence_no: 2,
        payload: {},
        summary: "Summarised reply",
      }),
    ]);
    expect(messages[0].parts[0].text).toBe("Summarised reply");
  });

  it("keeps reasoning as a separate part from the reply text", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 1,
        payload: { text: "thinking…" },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 2,
        payload: { text: "Answer" },
      }),
    ]);
    expect(messages[0].parts).toEqual([
      { type: "reasoning", text: "thinking…", status: { type: "running" } },
      { type: "text", text: "Answer", status: { type: "running" } },
    ]);
  });

  it("ignores subagent deltas (they belong to the Agents tab)", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        subagent_id: "sub-1",
        payload: { text: "subagent chatter" },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 2,
        payload: { text: "main" },
      }),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0].parts[0].text).toBe("main");
  });

  it("dedupes by event_id (safe on replay)", () => {
    const dup = ev({
      event_type: "model_delta",
      sequence_no: 1,
      event_id: "d1",
      payload: { text: "once" },
    });
    expect(projectChatMessages([dup, dup])[0].parts[0].text).toBe("once");
  });
});
