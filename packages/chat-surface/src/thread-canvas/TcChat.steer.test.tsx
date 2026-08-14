// The mid-run steer, where it actually lands.
//
// A component exported from the barrel that nothing mounts is not a surface —
// which is exactly what `run_steered` was: appended inside every steered run's
// causal prefix, durable across replay, and drawn by nobody. So these tests
// drive `TcChat` (not the row in isolation) and assert the user's own words
// reach the transcript, in BOTH modes, at the beat they were sent, and that
// nothing on the way folds them away.

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

import type { SteerNoteEntry } from "../destinations/run/steerProjection";
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

const NOTE: SteerNoteEntry = {
  eventId: "evt-steer-1",
  runId: "run-1",
  seq: 20,
  label: "You steered this run.",
  text: "Use the staging table, not production.",
  steerId: "steer_abc",
};

/** An assistant turn with prose on BOTH sides of the steer's seq. */
function turn(): TcChatMessage {
  return {
    message_id: "m-1",
    role: "assistant",
    run_id: "run-1",
    parts: [
      {
        type: "text",
        text: "Reading the production table now.",
        status: { type: "complete" },
        seq: 10,
      },
      {
        type: "text",
        text: "Switched to staging.",
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
    toolName: "read_table",
    title: "Read table",
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

describe("TcChat — the mid-run steer", () => {
  it.each(["studio", "focus"] as const)(
    "draws the interjection in %s mode, with the server's sentence and the user's words",
    (mode) => {
      renderChat(mode, { steerNotes: [NOTE] });

      const row = screen.getByTestId("tc-chat-steer-evt-steer-1");
      expect(
        within(row).getByTestId("tc-chat-steer-evt-steer-1-label").textContent,
      ).toBe("You steered this run.");
      expect(
        within(row).getByTestId("tc-chat-steer-evt-steer-1-text").textContent,
      ).toBe("Use the staging table, not production.");
    },
  );

  it("renders NOTHING when the host wires no steers — the prop is safe to land unmounted", () => {
    renderChat("studio");
    expect(document.querySelector(".tc-steer")).toBeNull();
    // …and the transcript it would have joined is otherwise untouched.
    expect(screen.getByText("Reading the production table now.")).toBeTruthy();
    expect(screen.getByText("Switched to staging.")).toBeTruthy();
  });

  it("lands BETWEEN the prose on either side of it, not at the tail", () => {
    renderChat("studio", { steerNotes: [NOTE] });

    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    expect(list).not.toBeNull();
    const text = (list as Element).textContent ?? "";
    const before = text.indexOf("Reading the production table now.");
    const steer = text.indexOf("Use the staging table");
    const after = text.indexOf("Switched to staging.");
    // The whole reason the interleave is ordered on `sequence_no`: a steer that
    // drains to the bottom of the thread says the user intervened at the end,
    // which is the opposite of what the record shows.
    expect(before).toBeGreaterThanOrEqual(0);
    expect(steer).toBeGreaterThan(before);
    expect(after).toBeGreaterThan(steer);
  });

  it("draws BELOW the card it reacted to when the two share a seq", async () => {
    renderChat("studio", { steerNotes: [NOTE], toolCalls: [toolCall()] });

    // The tool card mounts asynchronously (see TcChat.test.tsx:782). Querying
    // synchronously races it, and a missing card reads as an ordering failure
    // rather than the timing one it is.
    await screen.findByTestId("tc-chat-tool-call-1");

    const tool = screen.getByTestId("tc-chat-tool-call-1");
    const row = screen.getByTestId("tc-chat-steer-evt-steer-1");
    // Document order, not row index — the claim is purely "the note is painted
    // after the card". The user interjected in reaction to something; drawn
    // above it, the transcript would show them reacting to what they had not
    // yet been shown.
    const relation = tool.compareDocumentPosition(row);
    expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("stays an in-thread line — its own peer row, and nothing to press", () => {
    renderChat("studio", { steerNotes: [NOTE], toolCalls: [toolCall()] });
    const row = screen.getByTestId("tc-chat-steer-evt-steer-1");
    expect(row.querySelectorAll("button")).toHaveLength(0);
    // `groupActivityStream` is opt-in (tool + fleet only), so a new stream kind
    // defaults to visible-and-ungrouped rather than swallowed. Asserted as a
    // structural fact — the row is a DIRECT child of the transcript list —
    // because the failure mode is silent: a folded note is simply not there.
    const item = screen.getByTestId("tc-chat-steer-item-evt-steer-1");
    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    expect(item.parentElement).toBe(list);
    expect(item.contains(row)).toBe(true);
  });

  it("survives a transcript with nothing else in it", () => {
    // A run steered before it produced anything visible. Without steers counting
    // toward `nothingToShow` this drew "No messages yet." over a sentence the
    // reader had just typed.
    render(
      <TransportProvider transport={makeTransport()}>
        <TcChat
          conversationId="c"
          mode="studio"
          messages={[]}
          activeRunId="run-1"
          steerNotes={[NOTE]}
        />
      </TransportProvider>,
    );
    expect(screen.queryByTestId("tc-chat-empty")).toBeNull();
    expect(screen.getByTestId("tc-chat-steer-evt-steer-1")).toBeTruthy();
  });

  it("draws one line per steer, in seq order", () => {
    renderChat("studio", {
      steerNotes: [
        { ...NOTE, eventId: "evt-b", seq: 25, text: "And skip the backfill." },
        NOTE,
      ],
    });
    const list = screen.getByTestId("tc-chat-messages").querySelector("ul");
    const text = (list as Element).textContent ?? "";
    expect(text.indexOf("Use the staging table")).toBeLessThan(
      text.indexOf("And skip the backfill."),
    );
  });

  it("tells the user that ⏎ steers, because the send control is a Stop button", () => {
    // The placeholder is the ONLY announcement of the mid-run send path — with a
    // run live the send slot is occupied by Stop, so nothing else on screen says
    // that typing does anything at all.
    renderChat("studio", { steering: true });
    expect(
      screen.getByPlaceholderText("Steer this run — ⏎ to send"),
    ).toBeTruthy();
  });

  it("keeps the ordinary placeholder when no run is live", () => {
    renderChat("studio");
    expect(screen.getByPlaceholderText("Send a message…")).toBeTruthy();
  });
});
