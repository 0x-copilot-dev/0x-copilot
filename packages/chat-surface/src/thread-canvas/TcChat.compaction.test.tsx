// The compaction boundary, where it actually lands.
//
// A component exported from the barrel that nothing mounts is not a surface —
// which is exactly what `compression_note` was: emitted on every oversized tool
// result, projected server-side with a validated title, and drawn by nobody. So
// these tests drive `TcChat` (not the divider in isolation) and assert the
// boundary reaches the transcript, in BOTH modes, at the right point in the
// order, and that it never becomes a card on the way.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import type { CompactionNoticeEntry } from "../destinations/run/compactionProjection";
import { TransportProvider } from "../providers/TransportProvider";
import type { ToolCallEntry } from "./eventProjector";
import { TcChat, type TcChatMessage, type TcChatMode } from "./TcChat";

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ messages: [] }) as Promise<TRes>,
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => {} }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

const NOTICE: CompactionNoticeEntry = {
  eventId: "evt-note-1",
  runId: "run-1",
  seq: 20,
  label: "Compacted 8.6k tokens of read_file output",
  tokensSaved: 8600,
  beforeTokens: 8900,
  afterTokens: 300,
  toolName: "read_file",
};

/** An assistant turn with prose on BOTH sides of the compaction seq. */
function turn(): TcChatMessage {
  return {
    message_id: "m-1",
    role: "assistant",
    run_id: "run-1",
    parts: [
      {
        type: "text",
        text: "Reading the file now.",
        status: { type: "complete" },
        seq: 10,
      },
      {
        type: "text",
        text: "Here is the summary.",
        status: { type: "complete" },
        seq: 30,
      },
    ],
    created_at_ms: 1716000000000,
  } as TcChatMessage;
}

function toolCall(): ToolCallEntry {
  return {
    id: "call-1",
    toolName: "read_file",
    title: "Read file",
    status: "complete",
    runId: "run-1",
    sequenceNo: 20,
    createdAtMs: 1716000001000,
  } as ToolCallEntry;
}

function renderChat(
  mode: TcChatMode,
  props: Partial<React.ComponentProps<typeof TcChat>> = {},
): void {
  render(
    <TransportProvider transport={makeTransport()}>
      <TcChat
        conversationId="c"
        mode={mode}
        messages={[turn()]}
        activeRunId="run-1"
        {...props}
      />
    </TransportProvider>,
  );
}

describe("TcChat — the context-compaction boundary", () => {
  it.each(["studio", "focus"] as const)(
    "draws the boundary in %s mode, with the server's own sentence",
    (mode) => {
      renderChat(mode, { compactionNotices: [NOTICE] });

      const divider = screen.getByTestId("tc-chat-compaction-evt-note-1");
      expect(
        within(divider).getByTestId("tc-chat-compaction-evt-note-1-label")
          .textContent,
      ).toBe("Compacted 8.6k tokens of read_file output");
      expect(
        within(divider).getByTestId("tc-chat-compaction-evt-note-1-counts")
          .textContent,
      ).toBe("8.9k → 300");
    },
  );

  it("renders NOTHING when the host wires no notices — the prop is safe to land unmounted", () => {
    renderChat("studio");
    expect(document.querySelector(".tc-compaction")).toBeNull();
    // …and the transcript it would have joined is otherwise untouched.
    expect(screen.getByText("Reading the file now.")).toBeTruthy();
    expect(screen.getByText("Here is the summary.")).toBeTruthy();
  });

  it("lands BETWEEN the prose on either side of it, not at the tail", () => {
    renderChat("studio", { compactionNotices: [NOTICE] });

    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    expect(list).not.toBeNull();
    const text = (list as Element).textContent ?? "";
    const before = text.indexOf("Reading the file now.");
    const divider = text.indexOf("Compacted 8.6k tokens");
    const after = text.indexOf("Here is the summary.");
    // The whole reason the interleave is ordered on `sequence_no`: a boundary
    // that drains to the bottom of the thread says the model narrowed its view
    // at the end of the turn, which is not what happened.
    expect(before).toBeGreaterThanOrEqual(0);
    expect(divider).toBeGreaterThan(before);
    expect(after).toBeGreaterThan(divider);
  });

  it("draws BELOW the tool card it describes when the two share a seq", async () => {
    renderChat("studio", {
      compactionNotices: [NOTICE],
      toolCalls: [toolCall()],
    });

    // The tool card mounts asynchronously (see TcChat.test.tsx:782). Querying
    // the rows synchronously races it, and a missing card reads as an ordering
    // failure rather than the timing one it is.
    await screen.findByTestId("tc-chat-tool-call-1");

    const tool = screen.getByTestId("tc-chat-tool-call-1");
    const divider = screen.getByTestId("tc-chat-compaction-evt-note-1");

    // Document order, not row index: which <li> each node lands under is a
    // structural detail of the stream, while the claim being pinned is purely
    // "the divider is painted after the card". Asserting on row index instead
    // made a card that renders outside the queried list read as a reversed
    // order, which is a different defect entirely.
    //
    // "Here is what the tool returned" then "here is where the model stopped
    // holding all of it". Reversed, the divider narrows something the reader has
    // not been shown yet.
    const relation = tool.compareDocumentPosition(divider);
    expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("stays a boundary in the transcript — its own peer row, and nothing to press", () => {
    renderChat("studio", {
      compactionNotices: [NOTICE],
      toolCalls: [toolCall()],
    });
    const divider = screen.getByTestId("tc-chat-compaction-evt-note-1");
    expect(divider.querySelectorAll("button")).toHaveLength(0);
    // `groupActivityStream` is opt-in (tool + fleet only), so a new stream kind
    // defaults to visible-and-ungrouped rather than swallowed. Asserted as a
    // structural fact — the divider's row is a DIRECT child of the transcript
    // list — because the failure mode is silent: a folded divider is simply not
    // there, and a negative on a wrapper testid nothing emits passes vacuously.
    const row = screen.getByTestId("tc-chat-compaction-item-evt-note-1");
    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    expect(row.parentElement).toBe(list);
    expect(row.contains(divider)).toBe(true);
  });

  it("draws one boundary per notice, in seq order", () => {
    renderChat("studio", {
      compactionNotices: [
        { ...NOTICE, eventId: "evt-b", seq: 25, label: "Compacted 2k tokens" },
        NOTICE,
      ],
    });
    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    const text = (list as Element).textContent ?? "";
    expect(text.indexOf("Compacted 8.6k tokens")).toBeLessThan(
      text.indexOf("Compacted 2k tokens"),
    );
  });
});
