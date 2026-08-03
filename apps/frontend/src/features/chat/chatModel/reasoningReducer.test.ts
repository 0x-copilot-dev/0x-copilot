import { describe, expect, it } from "vitest";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { applyRuntimeEvent } from "./eventReducer";
import { assistantMessageId } from "./recordHelpers";
import type { ChatItem, ThreadMessageContent } from "./types";

const RUN_ID = "run_reason_1";
const CONVERSATION_ID = "conv_reason_1";

function event(
  overrides: Partial<RuntimeEventEnvelope> &
    Pick<RuntimeEventEnvelope, "event_type" | "payload">,
): RuntimeEventEnvelope {
  return {
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    activity_kind: "reasoning",
    created_at: "2026-05-04T12:00:00Z",
    ...overrides,
  } as RuntimeEventEnvelope;
}

function reasoningPart(content: ThreadMessageContent):
  | {
      text?: string;
      status?: { type: string };
      startedAtMs?: number;
      updatedAtMs?: number;
    }
  | undefined {
  return content.find((part) => part.type === "reasoning") as
    | {
        text?: string;
        status?: { type: string };
        startedAtMs?: number;
        updatedAtMs?: number;
      }
    | undefined;
}

function assistantContent(items: ChatItem[]): ThreadMessageContent {
  const id = assistantMessageId(RUN_ID);
  const message = items.find(
    (item): item is Extract<ChatItem, { kind: "message" }> =>
      item.kind === "message" && item.id === id,
  );
  return message?.content ?? [];
}

describe("eventReducer reasoning case (PR 3.6)", () => {
  it("stamps startedAtMs and updatedAtMs from event.created_at", () => {
    const items = applyRuntimeEvent(
      [],
      event({
        event_type: "reasoning_summary_delta",
        payload: { delta: "weighing options ", summary: "weighing options " },
        created_at: "2026-05-04T12:00:01.000Z",
      }),
    );
    const part = reasoningPart(assistantContent(items));
    expect(part?.text).toBe("weighing options ");
    expect(part?.status?.type).toBe("running");
    expect(part?.startedAtMs).toBe(Date.parse("2026-05-04T12:00:01.000Z"));
    expect(part?.updatedAtMs).toBe(Date.parse("2026-05-04T12:00:01.000Z"));
  });

  it("appends delta text and advances updatedAtMs only", () => {
    let items = applyRuntimeEvent(
      [],
      event({
        event_type: "reasoning_summary_delta",
        payload: { delta: "weighing ", summary: "weighing " },
        created_at: "2026-05-04T12:00:01.000Z",
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_type: "reasoning_summary_delta",
        payload: { delta: "options", summary: "options" },
        created_at: "2026-05-04T12:00:04.500Z",
      }),
    );
    const part = reasoningPart(assistantContent(items));
    expect(part?.text).toBe("weighing options");
    expect(part?.startedAtMs).toBe(Date.parse("2026-05-04T12:00:01.000Z"));
    expect(part?.updatedAtMs).toBe(Date.parse("2026-05-04T12:00:04.500Z"));
  });

  it("flips part status to complete on the final reasoning_summary cap", () => {
    let items = applyRuntimeEvent(
      [],
      event({
        event_type: "reasoning_summary_delta",
        payload: { delta: "weighing ", summary: "weighing " },
        created_at: "2026-05-04T12:00:01.000Z",
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_type: "reasoning_summary",
        payload: { summary: "weighing options" },
        created_at: "2026-05-04T12:00:05.000Z",
      }),
    );
    const part = reasoningPart(assistantContent(items));
    expect(part?.text).toBe("weighing options");
    expect(part?.status?.type).toBe("complete");
  });

  it("closes a running reasoning span when text arrives without an explicit cap", () => {
    let items = applyRuntimeEvent(
      [],
      event({
        event_type: "reasoning_summary_delta",
        payload: { delta: "weighing ", summary: "weighing " },
        created_at: "2026-05-04T12:00:01.000Z",
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        sequence_no: 2,
        activity_kind: "message",
        event_type: "model_delta",
        payload: { delta: "Here's the announcement." },
        created_at: "2026-05-04T12:00:06.000Z",
      }),
    );
    const part = reasoningPart(assistantContent(items));
    expect(part?.status?.type).toBe("complete");
    const content = assistantContent(items);
    const text = content.find((p) => p.type === "text") as
      | { text: string }
      | undefined;
    expect(text?.text).toBe("Here's the announcement.");
  });

  // The reported bug: thinking that happens BETWEEN two batches of tool calls.
  // `appendReasoning` used to target `findIndex(isReasoningPart)` — the FIRST
  // reasoning part of the turn — so span #2 appended into span #1, and the
  // `reasoning_summary` cap (which replaces) deleted span #1 outright.
  it("keeps each thinking span as its own part across tool batches", () => {
    let items: ChatItem[] = [];
    const push = (overrides: Parameters<typeof event>[0]) => {
      items = applyRuntimeEvent(items, event(overrides));
    };

    push({
      sequence_no: 1,
      event_type: "reasoning_summary_delta",
      payload: { delta: "First I check CI." },
    });
    push({
      sequence_no: 2,
      activity_kind: "tool",
      event_type: "tool_call_started",
      payload: { tool_call_id: "call_1", tool_name: "read_ci" },
    });
    push({
      sequence_no: 3,
      event_type: "reasoning_summary_delta",
      payload: { delta: "CI is green; now the deploy log." },
    });
    push({
      sequence_no: 4,
      event_type: "reasoning_summary",
      payload: { summary: "CI is green; now the deploy log." },
    });

    const spans = assistantContent(items).filter(
      (part) => part.type === "reasoning",
    ) as Array<{ text?: string }>;
    expect(spans.map((span) => span.text)).toEqual([
      "First I check CI.",
      "CI is green; now the deploy log.",
    ]);
  });
});
