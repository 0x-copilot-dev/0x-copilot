import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { projectCompactionNotices } from "./compactionProjection";

function noteEvent(
  payload: Record<string, unknown>,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: "evt-note",
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: 12,
    event_type: "compression_note" as RuntimeApiEventType,
    activity_kind: "note",
    display_title: "Compacted 8.6k tokens of read_file output",
    created_at: "2026-08-14T10:00:00.000Z",
    payload,
    ...overrides,
  } as RuntimeEventEnvelope;
}

const MATERIAL = {
  before_tokens: 8900,
  after_tokens: 300,
  tokens_saved: 8600,
  strategy: "offload",
  trigger: "token_threshold",
  tool_name: "read_file",
};

describe("projectCompactionNotices", () => {
  it("projects the note the runtime emits, keeping the SERVER's title verbatim", () => {
    const [notice] = projectCompactionNotices([noteEvent(MATERIAL)]);
    // The label is derived server-side from the same counts the producer
    // validated. Re-wording it here is how the sentence and the numbers beside
    // it start to disagree.
    expect(notice.label).toBe("Compacted 8.6k tokens of read_file output");
    expect(notice.seq).toBe(12);
    expect(notice.eventId).toBe("evt-note");
    expect(notice.runId).toBe("run-1");
    expect(notice.tokensSaved).toBe(8600);
    expect(notice.beforeTokens).toBe(8900);
    expect(notice.afterTokens).toBe(300);
    expect(notice.toolName).toBe("read_file");
  });

  it("ignores every event that is not a compaction note", () => {
    expect(
      projectCompactionNotices([
        noteEvent(MATERIAL, {
          event_id: "evt-tool",
          event_type: "tool_result" as RuntimeApiEventType,
        }),
      ]),
    ).toEqual([]);
  });

  it("drops a note that compacted nothing, rather than drawing a boundary over it", () => {
    // `CompactionNotice.is_material` refuses to emit one of these, so this is
    // about REPLAYED history: a divider announcing that nothing happened is
    // worse than no divider, because the reader has to account for it.
    expect(
      projectCompactionNotices([
        noteEvent({ ...MATERIAL, tokens_saved: 0, after_tokens: 8900 }),
      ]),
    ).toEqual([]);
  });

  it("derives the saving from the two counts when `tokens_saved` is absent", () => {
    const [notice] = projectCompactionNotices([
      noteEvent({
        before_tokens: 1200,
        after_tokens: 200,
        strategy: "offload",
      }),
    ]);
    expect(notice.tokensSaved).toBe(1000);
  });

  it("refuses to invent a saving from one count alone", () => {
    expect(
      projectCompactionNotices([
        noteEvent({ before_tokens: 1200, strategy: "offload" }),
      ]),
    ).toEqual([]);
  });

  it("falls back to a neutral label when the server projected no title", () => {
    const [notice] = projectCompactionNotices([
      noteEvent(MATERIAL, { display_title: null }),
    ]);
    expect(notice.label).toBe("Compacted tool output");
  });

  it("omits a count the wire did not carry rather than guessing at it", () => {
    const [notice] = projectCompactionNotices([
      noteEvent({ tokens_saved: 900, strategy: "summarize" }),
    ]);
    expect(notice.beforeTokens).toBeNull();
    expect(notice.afterTokens).toBeNull();
    expect(notice.toolName).toBeNull();
  });

  it("emits ONE divider per compaction when a replay tail repeats the envelope", () => {
    const event = noteEvent(MATERIAL);
    expect(projectCompactionNotices([event, event, event])).toHaveLength(1);
  });

  it("orders boundaries by `sequence_no`, whatever order they arrived in", () => {
    const late = noteEvent(MATERIAL, { event_id: "evt-b", sequence_no: 40 });
    const early = noteEvent(MATERIAL, { event_id: "evt-a", sequence_no: 4 });
    expect(
      projectCompactionNotices([late, early]).map((notice) => notice.seq),
    ).toEqual([4, 40]);
  });

  it("rejects a non-integer / booleanish count instead of printing it", () => {
    // `true` is a number in JS only if you ask carelessly; a JSON payload that
    // carried a boolean here would otherwise render "1 → 0".
    expect(
      projectCompactionNotices([
        noteEvent({
          before_tokens: true,
          after_tokens: false,
          tokens_saved: 1.5,
        } as unknown as Record<string, unknown>),
      ]),
    ).toEqual([]);
  });
});
