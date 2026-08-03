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
    expect(messages[0].parts).toMatchObject([
      {
        type: "text",
        text: "Hello there",
        status: { type: "running" },
        // Anchored at the seq the part OPENED at, not the latest delta.
        seq: 1,
      },
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
    expect(messages[0].parts).toMatchObject([
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
    // Visible text means the model stopped thinking: the reasoning part is
    // CLOSED by the first text delta, matching the web reducer's
    // `closeReasoningIfRunning`. Only the tail part is still running.
    expect(messages[0].parts).toMatchObject([
      { type: "reasoning", text: "thinking…", status: { type: "complete" } },
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

  // Regression guard: the REAL runtime wire shape. model_delta carries the
  // chunk under `delta` (+ a duplicate `message`), NOT `text` — the original
  // tests used `{text}`, which is why dropped streaming went unnoticed. A
  // catalog run has ~300 of these; reading only `.text` folded every one to "".
  it("streams model_delta from the real `{delta, message}` payload shape", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Hel", message: "Hel" },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 2,
        payload: { delta: "lo", message: "lo" },
      }),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0].parts).toMatchObject([
      { type: "text", text: "Hello", status: { type: "running" } },
    ]);
  });

  it("streams reasoning from the real `{summary, delta}` payload shape", () => {
    const messages = projectChatMessages([
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 1,
        payload: { summary: "Plan: ", delta: "Plan: " },
      }),
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 2,
        payload: { summary: "Plan: step 1", delta: "step 1" },
      }),
      ev({
        event_type: "model_delta",
        sequence_no: 3,
        payload: { delta: "Done", message: "Done" },
      }),
    ]);
    expect(messages[0].parts).toMatchObject([
      { type: "reasoning", text: "Plan: step 1", status: { type: "complete" } },
      { type: "text", text: "Done", status: { type: "running" } },
    ]);
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

// ===========================================================================
// The interleaving invariant — a turn is ordered, not bucketed
// ===========================================================================
//
// The fold used to keep ONE accumulator per KIND, so a turn shaped
// `text → tools → text` collapsed to a single text blob that `final_response`
// then overwrote outright. Anything the model said before it acted was
// destroyed, and the surviving blob carried one anchor so every mid-turn card
// sorted after it. These tests pin the ordered model that replaced it.
describe("ordered turn parts", () => {
  const toolStart = (seq: number) =>
    ev({ event_type: "tool_call_started", sequence_no: seq });

  it("opens a NEW text part after a tool call instead of appending to the first", () => {
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Checking the deploy." },
      }),
      toolStart(2),
      ev({ event_type: "tool_result", sequence_no: 3 }),
      ev({
        event_type: "model_delta",
        sequence_no: 4,
        payload: { delta: "It shipped." },
      }),
    ]);
    expect(msg.parts).toMatchObject([
      { type: "text", text: "Checking the deploy.", seq: 1 },
      { type: "text", text: "It shipped.", seq: 4 },
    ]);
  });

  it("does NOT let final_response overwrite text emitted before the tool calls", () => {
    // This is the bug verbatim: `text = payloadText(event) || summary || text`
    // replaced the whole accumulator, so the pre-tool sentence vanished.
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Let me look that up." },
      }),
      toolStart(2),
      ev({
        event_type: "model_delta",
        sequence_no: 3,
        payload: { delta: "It shipped" },
      }),
      ev({
        event_type: "final_response",
        sequence_no: 4,
        payload: { message: "It shipped at 09:14." },
      }),
    ]);
    expect(msg.parts.map((p) => p.text)).toEqual([
      "Let me look that up.",
      "It shipped at 09:14.",
    ]);
    expect(msg.parts.every((p) => p.status?.type === "complete")).toBe(true);
  });

  it("gives final_response its own part when the run ends right after a tool call", () => {
    // The narrow version of the same overwrite: with no text streamed after the
    // tool, the tail text part is still the PRE-tool sentence, so reconciling
    // into it destroys exactly what this whole change exists to preserve.
    // A differential against a second implementation cannot catch this — both
    // folds were wrong identically — so it is pinned by value here.
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Checking." },
      }),
      toolStart(2),
      ev({
        event_type: "final_response",
        sequence_no: 3,
        payload: { message: "Done." },
      }),
    ]);
    expect(msg.parts).toMatchObject([
      { type: "text", text: "Checking.", seq: 1 },
      { type: "text", text: "Done.", seq: 3 },
    ]);
  });

  it("still reconciles into the tail part when text streamed after the tool", () => {
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Checking." },
      }),
      toolStart(2),
      ev({
        event_type: "model_delta",
        sequence_no: 3,
        payload: { delta: "Ship" },
      }),
      ev({
        event_type: "final_response",
        sequence_no: 4,
        payload: { message: "Shipped at 09:14." },
      }),
    ]);
    // Two parts, not three: the streamed tail settles INTO its own part rather
    // than appending a duplicate of the same sentence.
    expect(msg.parts).toMatchObject([
      { type: "text", text: "Checking.", seq: 1 },
      { type: "text", text: "Shipped at 09:14.", seq: 3 },
    ]);
  });

  it("keeps every thinking span, in place, across two tool batches", () => {
    const [msg] = projectChatMessages([
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 1,
        payload: { delta: "First I check CI." },
      }),
      toolStart(2),
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 3,
        payload: { delta: "CI is green, now the deploy log." },
      }),
      toolStart(4),
      ev({
        event_type: "model_delta",
        sequence_no: 5,
        payload: { delta: "All good." },
      }),
    ]);
    expect(msg.parts).toMatchObject([
      { type: "reasoning", text: "First I check CI.", seq: 1 },
      { type: "reasoning", text: "CI is green, now the deploy log.", seq: 3 },
      { type: "text", text: "All good.", seq: 5 },
    ]);
  });

  it("caps the OPEN reasoning span, never the first one in the turn", () => {
    // `appendReasoning` used `findIndex(isReasoningPart)` with replace-on-cap,
    // so span #2's cap deleted span #1's text.
    const [msg] = projectChatMessages([
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 1,
        payload: { delta: "span one" },
      }),
      toolStart(2),
      ev({
        event_type: "reasoning_summary_delta",
        sequence_no: 3,
        payload: { delta: "span two" },
      }),
      ev({
        event_type: "reasoning_summary",
        sequence_no: 4,
        payload: { summary: "span two, capped" },
      }),
    ]);
    expect(msg.parts).toMatchObject([
      { type: "reasoning", text: "span one", seq: 1 },
      { type: "reasoning", text: "span two, capped", seq: 3 },
    ]);
  });

  it("does not split a part on incidental frames", () => {
    // Only events that render as their own card break a part. Splitting on a
    // heartbeat or a todo snapshot would tear a sentence — or a GFM table —
    // across two markdown parts that each parse as half a document.
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "| a | b |\n" },
      }),
      ev({ event_type: "todo_list_updated", sequence_no: 2 }),
      ev({
        event_type: "model_delta",
        sequence_no: 3,
        payload: { delta: "| - | - |" },
      }),
    ]);
    expect(msg.parts).toHaveLength(1);
    expect(msg.parts[0].text).toBe("| a | b |\n| - | - |");
  });

  it("does not split a part on an INTERNAL tool frame (it renders no card)", () => {
    // `write_todos` is stamped internal and filtered out of the timeline, so a
    // break here would leave a gap with nothing between the halves.
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "Let me plan this out" },
      }),
      ev({
        event_type: "tool_call_started",
        sequence_no: 2,
        visibility: "internal",
      }),
      ev({ event_type: "tool_result", sequence_no: 3, visibility: "internal" }),
      ev({
        event_type: "model_delta",
        sequence_no: 4,
        payload: { delta: " before I start." },
      }),
    ]);
    expect(msg.parts).toHaveLength(1);
    expect(msg.parts[0].text).toBe("Let me plan this out before I start.");
  });

  it("orders parts by sequence_no even if events arrive out of order", () => {
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 4,
        payload: { delta: "second" },
      }),
      toolStart(2),
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "first" },
      }),
    ]);
    expect(msg.parts.map((p) => p.text)).toEqual(["first", "second"]);
  });

  it("carries the run_id so the renderer can scope the seq merge", () => {
    const [msg] = projectChatMessages([
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { delta: "hi" },
        run_id: "run-42",
      }),
    ]);
    expect(msg.run_id).toBe("run-42");
  });
});

// ===========================================================================
// Payload-contract regression (found while investigating PRD-03)
// ===========================================================================
//
// `RuntimeTextPayload` declares `message` / `delta` / `summary` — never `text`.
// The worker writes the answer to BOTH `payload.message` and the event summary
// (`runtime_worker/handlers/run.py:839-863`), so reading only `text` fell
// through to the summary fallback on every single run. It rendered the right
// string, which is exactly why nobody noticed: the fallback was load-bearing
// and the declared field was unread.
describe("final_response payload contract", () => {
  const finalEvent = (payload: Record<string, unknown>, summary?: string) =>
    ({
      event_id: "e1",
      sequence_no: 1,
      event_type: "final_response",
      created_at: "2026-08-01T10:00:00Z",
      payload,
      summary,
    }) as unknown as Parameters<typeof projectChatMessages>[0][number];

  it("reads the answer from payload.message, not just the summary fallback", () => {
    const [msg] = projectChatMessages([
      finalEvent({ message: "the real answer" }),
    ]);
    expect(msg?.parts.at(-1)?.text).toBe("the real answer");
  });

  it("still honours summary when a payload carries no message", () => {
    const [msg] = projectChatMessages([finalEvent({}, "summary only")]);
    expect(msg?.parts.at(-1)?.text).toBe("summary only");
  });

  it("prefers the payload over the summary when they disagree", () => {
    // They are the same string in production. If they ever diverge, the
    // payload is the declared carrier and must win.
    const [msg] = projectChatMessages([
      finalEvent({ message: "payload wins" }, "stale summary"),
    ]);
    expect(msg?.parts.at(-1)?.text).toBe("payload wins");
  });
});
