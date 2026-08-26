import { describe, expect, it, vi } from "vitest";
import { EMPTY_CONNECTOR_TRUST } from "../approvals";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";

import type { SubagentEntry } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import type { FleetProjection, SubagentActivityRecord } from "../subagents";
import type { ToolCallEntry } from "./eventProjector";
import type { McpAuthPort } from "../destinations/run/mcpAuthPort";
import { TransportProvider } from "../providers/TransportProvider";
import { SwimlaneScrubProvider } from "./SwimlaneScrubContext";
import {
  TcChat,
  type TcChatApproval,
  type TcChatMessage,
  type TcChatMessagesResponse,
  type TcChatProps,
} from "./TcChat";

// Assistant text now renders through the citation-safe markdown path
// (Streamdown). Streamdown installs an IntersectionObserver for its
// visibility-gated caret animation; jsdom ships none, so a no-op keeps
// assistant markdown renderable under test.
class NoopIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): unknown[] {
    return [];
  }
}
if (typeof globalThis.IntersectionObserver === "undefined") {
  (
    globalThis as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = NoopIntersectionObserver;
}

interface StubRecord {
  readonly calls: TypedRequest[];
}

function makeTransport(resolver: (req: TypedRequest) => Promise<unknown>): {
  transport: Transport;
  record: StubRecord;
} {
  const record: StubRecord = { calls: [] };
  const transport: Transport = {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      record.calls.push(req);
      return resolver(req) as Promise<TRes>;
    },
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => {},
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, record };
}

function withTransport(transport: Transport, children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

const SAMPLE_MESSAGES: ReadonlyArray<TcChatMessage> = [
  {
    message_id: "m1",
    role: "user",
    parts: [{ type: "text", text: "Draft an email to ops" }],
    created_at_ms: 1716000000000,
  },
  {
    message_id: "m2",
    role: "assistant",
    parts: [{ type: "text", text: "Sure — here is a draft." }],
    created_at_ms: 1716000060000,
  },
];

const SAMPLE_RESPONSE: TcChatMessagesResponse = { messages: SAMPLE_MESSAGES };

/**
 * A turn shaped like a real agent loop: the model says something, acts, then
 * says something else. Both text parts belong to ONE assistant turn and carry
 * the `seq` they opened at, which is what lets a card render between them.
 *
 * This is the fixture the interleaving bug could not express: before parts were
 * ordered, `text-before-tools` and `text-after-tools` were the same object with
 * one anchor, so no ordering key could place a card between them.
 */
const INTERLEAVED_TURN: ReadonlyArray<TcChatMessage> = [
  {
    message_id: "u1",
    role: "user",
    parts: [{ type: "text", text: "What is the deploy status?" }],
    created_at_ms: 1716000000000,
  },
  {
    message_id: "a1",
    role: "assistant",
    run_id: "run-1",
    created_at_ms: 1716000010000,
    parts: [
      { type: "text", text: "Let me check.", seq: 2 },
      { type: "text", text: "It shipped at 09:14.", seq: 8 },
    ],
  },
];

// PR-3.8 — fleet fixtures for the inline SubagentFleetCard slot (FR-3.17a).
function subagentEntry(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_a",
    parent_run_id: "run-1",
    subagent_name: "doc_reader",
    status: "running",
    display_title: "Doc reader",
    objective_summary: null,
    started_at: "2026-05-06T10:00:00Z",
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

function fleet(overrides: Partial<FleetProjection> = {}): FleetProjection {
  return {
    fleetId: "fleet-1",
    title: "Parallel research",
    sub: null,
    agentIds: ["doc_reader", "press_scout"],
    taskIds: ["task_a", "task_b"],
    total: 2,
    running: 2,
    done: 0,
    failed: 0,
    elapsed: null,
    finished: false,
    sequenceNo: 4,
    createdAtMs: 1716000030000,
    children: [
      subagentEntry({ task_id: "task_a", display_title: "Doc reader" }),
      subagentEntry({ task_id: "task_b", display_title: "Press scout" }),
    ],
    ...overrides,
  };
}

// A GFM table mid-stream: header + separator + one complete row, plus an
// incomplete trailing row (`| Globex`). The citation-safe streaming markdown
// path must parse the complete rows into a real <table> and hold the partial
// one — never emitting the raw `|pipe|` delimiters as visible text.
const STREAMING_TABLE_MESSAGE: TcChatMessage = {
  message_id: "m-table",
  role: "assistant",
  parts: [
    {
      type: "text",
      text: "| Account | Q4 |\n| --- | --- |\n| Acme | 176 |\n| Globex",
      status: { type: "running" },
    },
  ],
  created_at_ms: 1716000120000,
};

describe("TcChat", () => {
  it("fetches messages from /v1/agent/conversations/{id}/messages on mount", async () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve(SAMPLE_RESPONSE),
    );
    render(
      withTransport(
        transport,
        <TcChat conversationId="conv-1" mode="studio" />,
      ),
    );
    await screen.findByText("Draft an email to ops");
    expect(record.calls).toHaveLength(1);
    expect(record.calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/agent/conversations/conv-1/messages",
    });
  });

  it("refetches when conversationId changes", async () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve(SAMPLE_RESPONSE),
    );
    const { rerender } = render(
      withTransport(
        transport,
        <TcChat conversationId="conv-1" mode="studio" />,
      ),
    );
    await screen.findByText("Draft an email to ops");
    rerender(
      withTransport(
        transport,
        <TcChat conversationId="conv-2" mode="studio" />,
      ),
    );
    await waitFor(() => {
      expect(record.calls.length).toBe(2);
    });
    expect(record.calls[1]?.path).toBe(
      "/v1/agent/conversations/conv-2/messages",
    );
  });

  it("renders studio mode with messages and a composer", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    await screen.findByText("Sure — here is a draft.");
    expect(screen.getByTestId("tc-chat")).toHaveAttribute(
      "data-mode",
      "studio",
    );
    expect(screen.getByTestId("composer")).toBeInTheDocument();
  });

  it("renders an injected host composer via renderComposer instead of the base composer", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const seen: Array<{ disabled: boolean; placeholder: string }> = [];
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          renderComposer={(ctx) => {
            seen.push(ctx);
            return <div data-testid="host-composer">host composer</div>;
          }}
        />,
      ),
    );
    await screen.findByText("Sure — here is a draft.");
    // The host composer wins the slot; the base composer never mounts.
    expect(screen.getByTestId("host-composer")).toBeInTheDocument();
    expect(screen.queryByTestId("composer")).not.toBeInTheDocument();
    // Live cockpit → the seam hands the host a non-disabled, "send" placeholder.
    expect(seen.at(-1)).toEqual({
      disabled: false,
      placeholder: "Send a message…",
    });
  });

  it("passes the ghost disabled state + placeholder to the injected composer when scrubbed", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const seen: Array<{ disabled: boolean; placeholder: string }> = [];
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat
            conversationId="c"
            mode="studio"
            renderComposer={(ctx) => {
              seen.push(ctx);
              return <div data-testid="host-composer" />;
            }}
          />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByTestId("tc-chat-ghost-banner");
    // Off-live → the injected composer is told to disable, with the snap copy.
    expect(seen.at(-1)).toEqual({
      disabled: true,
      placeholder: "Snap to now to send a message",
    });
  });

  it("renders focus mode as the shared transcript + composer, not a stub", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="focus" />),
    );
    expect(screen.getByTestId("tc-chat")).toHaveAttribute("data-mode", "focus");
    // Focus is now a working chat — the same transcript + composer as Studio,
    // not the old Activity/Approvals placeholder.
    expect(screen.getByTestId("tc-chat-messages")).toBeInTheDocument();
    expect(screen.getByTestId("composer")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-chat-focus-tabs")).not.toBeInTheDocument();
  });

  it("centers the conversation and composer on the shared 1088px rail", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="focus" />),
    );

    await screen.findByText("Sure — here is a draft.");
    const messageRail = screen
      .getByTestId("tc-chat-messages")
      .querySelector("ul");
    expect(messageRail).not.toBeNull();
    expect(messageRail).toHaveStyle({
      marginLeft: "auto",
      marginRight: "auto",
      maxWidth: "var(--chat-content-width, 68rem)",
      width: "100%",
    });
    expect(screen.getByTestId("tc-chat-composer-slot")).toHaveStyle({
      marginLeft: "auto",
      marginRight: "auto",
      maxWidth: "var(--chat-content-width, 68rem)",
      width: "100%",
    });
  });

  // Regression: the transcript declared `overflow-y: auto` only, and CSS then
  // computes the untouched axis to `auto` as well — so one long token or a wide
  // tool payload panned the messages sideways under a stationary composer.
  it("scrolls the transcript vertically only, never sideways", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    expect(screen.getByTestId("tc-chat-messages")).toHaveStyle({
      overflowX: "hidden",
      overflowY: "auto",
    });
  });

  it("renders host-provided messages in focus without a fallback fetch", () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve(SAMPLE_RESPONSE),
    );
    const messages: TcChatMessage[] = [
      {
        message_id: "m1",
        role: "user",
        parts: [{ type: "text", text: "steer the run" }],
      },
    ];
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" messages={messages} />,
      ),
    );
    // Host-fed transcript renders directly …
    expect(screen.getByText("steer the run")).toBeInTheDocument();
    // … and the one-time GET fallback never fires.
    expect(record.calls).toHaveLength(0);
  });

  it("renders an error state when the message fetch rejects", async () => {
    const { transport } = makeTransport(() =>
      Promise.reject(new Error("nope")),
    );
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    await waitFor(() => {
      expect(screen.getByTestId("tc-chat-error")).toBeInTheDocument();
    });
  });

  it("renders an empty state when there are zero messages", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [] }),
    );
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    await waitFor(() => {
      expect(screen.getByTestId("tc-chat-empty")).toBeInTheDocument();
    });
  });

  it("shows ghost banner and disables composer when scrubbedTo is a number", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat conversationId="c" mode="studio" />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByTestId("tc-chat-ghost-banner");
    expect(screen.getByTestId("tc-chat")).toHaveAttribute("data-ghost", "true");
    expect(screen.getByTestId("composer-textarea")).toBeDisabled();
  });

  it("hides messages newer than the scrub time in ghost mode", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat conversationId="c" mode="studio" />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByText("Draft an email to ops");
    expect(
      screen.queryByText("Sure — here is a draft."),
    ).not.toBeInTheDocument();
  });

  it('renders all messages when scrubbedTo is "now"', async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: "now" }}>
          <TcChat conversationId="c" mode="studio" />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByText("Sure — here is a draft.");
    expect(
      screen.queryByTestId("tc-chat-ghost-banner"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("tc-chat")).toHaveAttribute(
      "data-ghost",
      "false",
    );
  });

  it("FR-3.19: streams a partial GFM table through the markdown path with a blinking cursor and no raw pipe leak", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [STREAMING_TABLE_MESSAGE] }),
    );
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    // Streamdown parses the completed rows into a real <table> — i.e. the
    // tabular markdown renders via the markdown path, not chat raw text.
    const table = await screen.findByRole("table");
    expect(table).toBeInTheDocument();

    const li = screen.getByTestId("tc-chat-message-m-table");
    // Assistant markdown must NOT fall through the raw PlainText renderer
    // (the only place a literal `|pipe|` could leak at this layer).
    expect(li.querySelector(".aui-plain-text")).toBeNull();
    // No half-parsed table markup surfaces as visible text.
    expect(li.textContent ?? "").not.toContain("|");
    // The incremental blinking cursor is active while the part is running.
    expect(li.querySelector(".assistant-markdown--streaming")).not.toBeNull();
  });

  it("invokes onSend when the composer sends", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const onSend = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" onSend={onSend} />,
      ),
    );
    await screen.findByText("Draft an email to ops");
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "thanks" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("thanks");
  });
});

describe("TcChat — inline fleet card (PR-3.8 / FR-3.17a)", () => {
  it("renders the hoisted SubagentFleetCard with a row per child when a fleet is projected", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [] }),
    );
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" fleets={[fleet()]} />,
      ),
    );
    const card = await screen.findByTestId("tc-chat-fleet-fleet-1");
    // The card's dispatch headline is derived from the projected total.
    expect(card).toHaveTextContent("Dispatched 2 subagents in parallel");
    // One FleetSubagentRow per projected child (reused Phase-1D primitive).
    expect(within(card).getByText("Doc reader")).toBeInTheDocument();
    expect(within(card).getByText("Press scout")).toBeInTheDocument();
  });

  it("renders a sensible single card for a fleet-of-one (WS-E)", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [] }),
    );
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          fleets={[
            fleet({
              fleetId: "solo",
              agentIds: ["researcher"],
              total: 1,
              running: 1,
              done: 0,
              children: [
                subagentEntry({
                  task_id: "task_solo",
                  display_title: "Research",
                }),
              ],
            }),
          ]}
        />,
      ),
    );
    const card = await screen.findByTestId("tc-chat-fleet-solo");
    // Singular headline — never the awkward "1 subagents in parallel".
    expect(card).toHaveTextContent("Dispatched a subagent");
    expect(card).not.toHaveTextContent("1 subagents");
    // The lone child renders exactly one row.
    expect(within(card).getByText("Research")).toBeInTheDocument();
  });

  it("marks a terminal fleet with a failed child as error rather than done", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [] }),
    );
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          fleets={[
            fleet({
              running: 0,
              done: 2,
              failed: 1,
              children: [
                subagentEntry({ status: "completed" }),
                subagentEntry({ task_id: "task_b", status: "failed" }),
              ],
            }),
          ]}
        />,
      ),
    );
    const card = await screen.findByTestId("tc-chat-fleet-fleet-1");
    expect(card.querySelector("[data-fleet-id]")).toHaveAttribute(
      "data-status",
      "error",
    );
    expect(card).toHaveTextContent("2/2 done · 1 failed");
  });

  it("shows canonical subagent tool activity when an inline fleet row is expanded", async () => {
    const { transport } = makeTransport(() =>
      Promise.resolve({ messages: [] }),
    );
    const activitiesByTask: ReadonlyMap<
      string,
      readonly SubagentActivityRecord[]
    > = new Map([
      [
        "task_a",
        [
          {
            id: "call-search",
            kind: "tool",
            title: "web_search",
            status: "completed",
            summary: "Found 3 primary sources",
            inputSummary: null,
            result: "Found 3 primary sources",
            isError: false,
          },
        ],
      ],
    ]);
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          fleets={[fleet()]}
          subagentActivitiesByTask={activitiesByTask}
        />,
      ),
    );

    const card = await screen.findByTestId("tc-chat-fleet-fleet-1");
    const row = card.querySelector('[data-task-id="task_a"]');
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    const timeline = screen.getByRole("region", {
      name: "Doc reader activity timeline",
    });
    expect(timeline).toHaveTextContent("Search web");
    expect(timeline).toHaveTextContent("Found 3 primary sources");
    expect(timeline).not.toHaveTextContent("No activity yet.");
  });

  it("renders no fleet card when no fleet is projected (linear run)", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );
    await screen.findByText("Sure — here is a draft.");
    expect(
      screen.queryByTestId("tc-chat-fleet-fleet-1"),
    ).not.toBeInTheDocument();
  });

  it("interleaves the fleet card between the parts of the turn by sequence_no", async () => {
    // The turn is `text → dispatch → text`. The fleet dispatched at seq 4, so it
    // renders BETWEEN the two text parts — not after the whole turn, which is
    // where wall-clock anchoring used to drain it (one bubble carries one
    // timestamp, its first token, so every mid-turn card sorted after it).
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="run-1"
          messages={INTERLEAVED_TURN}
          fleets={[fleet({ sequenceNo: 4, createdAtMs: 1716000030000 })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-fleet-fleet-1");
    const list = screen.getByTestId("tc-chat-messages");
    const ids = Array.from(list.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(ids).toEqual([
      "tc-chat-message-u1",
      "tc-chat-message-a1-part-0",
      "tc-chat-fleet-fleet-1",
      "tc-chat-message-a1-part-1",
    ]);
  });

  it("hides a fleet dispatched after the scrub cut in ghost mode", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat
            conversationId="c"
            mode="studio"
            fleets={[fleet({ createdAtMs: 1716000060000 })]}
          />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByTestId("tc-chat-ghost-banner");
    expect(
      screen.queryByTestId("tc-chat-fleet-fleet-1"),
    ).not.toBeInTheDocument();
  });
});

// Workstream D — inline tool-call card fixtures + interleave.
function toolCall(overrides: Partial<ToolCallEntry> = {}): ToolCallEntry {
  return {
    id: "call-1",
    toolName: "web_search",
    title: "Search the web",
    status: "running",
    sequenceNo: 3,
    // Unlabelled by default, which the transcript reads as "the active run" —
    // byte-identical to the behaviour before cards carried a run at all. Tests
    // that care about anchoring pass `runId` explicitly.
    runId: null,
    createdAtMs: 1716000030000,
    ...overrides,
  };
}

describe("TcChat — inline tool-call card (Workstream D)", () => {
  it("shows fact-bound inline results in Focus as well as Studio", async () => {
    // This test used to assert the opposite. Read it as a PIN on the rule —
    // the transcript renders the same content in both modes — not as proof
    // that Focus was empty before: `InlineToolResultCard` renders only a CSV
    // summary, and `ToolCallCard` (header + disclosure body + file diff) was
    // never mode-aware, so the gate only ever hid a CSV card. Focus subtracts
    // the surface COLUMN and the swimlanes, which is what makes it
    // single-column and what "I can't see anything" was mostly describing; it
    // must not also subtract from the transcript.
    //
    // Uses CSV facts, not sources: the inline SOURCES card was removed from the
    // transcript (sources live in the Sources rail now), so the CSV summary is
    // what remains of `InlineToolResultCard`.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const csvCall = toolCall({
      status: "complete",
      args: { path: "/tmp/forecast.csv" },
      result: { rows: 12, columns: 3 },
    });
    const { rerender } = render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" toolCalls={[csvCall]} />,
      ),
    );
    const studio = await screen.findByTestId("tc-inline-csv-summary-card");
    expect(studio).toHaveTextContent("forecast.csv");
    const studioHtml = studio.outerHTML;

    rerender(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" toolCalls={[csvCall]} />,
      ),
    );
    // Byte-identical, on the same principle the approvals card is pinned by:
    // comparing markup is what stops a mode-conditional attribute, class or
    // ordering growing back — not just the ones someone thought to name.
    expect(screen.getByTestId("tc-inline-csv-summary-card").outerHTML).toBe(
      studioHtml,
    );
  });

  it("reaches an edit_file diff inside the tool card in Focus", async () => {
    // The file-diff view (2c4a2461) is the richest thing the transcript can
    // show, and Focus is the mode with no surface column to fall back on — so
    // if it were not reachable here it would not be reachable at all without
    // switching modes. jsdom performs no layout, so being in the DOM proves
    // nothing about a collapsed <details>: the disclosure is actually opened.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="focus"
          toolCalls={[
            toolCall({
              id: "call-edit",
              toolName: "edit_file",
              title: "Edit a file",
              status: "complete",
              args: {
                file_path: "/tmp/report.md",
                old_string: "old line",
                new_string: "new line",
              },
              result: { content: "Updated /tmp/report.md" },
            }),
          ]}
        />,
      ),
    );
    const item = await screen.findByTestId("tc-chat-tool-call-edit");
    const card = item.querySelector("details");
    expect(card).not.toBeNull();
    // Open WITHOUT a click: the diff is the message, so the card seeds itself
    // open. Clicking here would collapse it.
    expect(card!).toHaveAttribute("open");

    const diff = within(card!).getByTestId("tc-tool-edit-diff");
    expect(
      within(diff).getByTestId("tc-tool-edit-diff-path"),
    ).toHaveTextContent("/tmp/report.md");
    expect(
      within(diff).getByTestId("tc-tool-edit-diff-counts"),
    ).toHaveTextContent("+1−1");
    // The specialised view is a READING of the call; the JSON stays the record,
    // demoted behind its own disclosure. Both must survive in Focus.
    expect(within(card!).getByText("raw payload")).toBeInTheDocument();
  });

  it("no longer renders a sources card under a completed web search", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[toolCall({ status: "complete" })]}
          toolCallCitations={[
            {
              citation_id: "source-1",
              freshness_at: null,
              ordinal: 1,
              snippet: null,
              source_connector: "web",
              source_doc_id: "postmortem",
              source_tool_call_id: "call-1",
              source_url: "https://example.com/postmortem",
              title: "Incident postmortem",
            },
          ]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat");
    expect(
      screen.queryByTestId("tc-inline-web-sources-card"),
    ).not.toBeInTheDocument();
  });

  it("renders a running tool card with a spinner in the transcript", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" toolCalls={[toolCall()]} />,
      ),
    );
    const card = await screen.findByTestId("tc-chat-tool-call-1");
    expect(card).toHaveAttribute("data-tool-status", "running");
    expect(within(card).getByText("web_search")).toBeInTheDocument();
    expect(card.querySelector(".tc-tool-card__spinner")).not.toBeNull();
  });

  it("uses the full compact header as the native detail disclosure", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[
            toolCall({
              status: "complete",
              args: { query: "aurora" },
              result: { hits: 2 },
              provenance: { source: "mcp", serverName: "Brave Search" },
              accessMode: "read_act",
              durationMs: 1200,
            }),
          ]}
        />,
      ),
    );
    const item = await screen.findByTestId("tc-chat-tool-call-1");
    expect(item).toHaveAttribute("data-tool-status", "complete");
    const card = item.querySelector("details");
    expect(card).not.toBeNull();
    expect(card!).not.toHaveAttribute("open");
    const header = card!.querySelector("summary");
    expect(header).not.toBeNull();
    expect(within(header!).getByText("Search the web")).toBeInTheDocument();
    expect(within(header!).queryByText("web_search")).not.toBeInTheDocument();
    expect(within(header!).getByText("Done")).toBeInTheDocument();
    expect(within(header!).getByText("MCP · Brave Search")).toBeInTheDocument();
    expect(within(header!).getByText("read + act")).toBeInTheDocument();
    expect(within(header!).getByText("1.2s")).toBeInTheDocument();

    fireEvent.click(header!);
    expect(card!).toHaveAttribute("open");
    expect(within(card!).getByText("tool")).toBeInTheDocument();
    expect(within(card!).getByText("web_search")).toBeInTheDocument();
    expect(within(card!).getByText("args")).toBeInTheDocument();
    expect(within(card!).getByText("result")).toBeInTheDocument();
    expect(within(card!).getByText("source")).toBeInTheDocument();
    expect(within(card!).getAllByText("MCP · Brave Search")).toHaveLength(2);
    expect(screen.getByTestId("tc-chat-tool-call-1-args")).toHaveTextContent(
      '"query": "aurora"',
    );
    expect(screen.getByTestId("tc-chat-tool-call-1-result")).toHaveTextContent(
      '"hits": 2',
    );
  });

  it("bounds selectable payloads and rolls delegated work up to agent anchors", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[
            toolCall({
              args: { note: "x".repeat(700) },
              subagentTaskIds: ["task-research", "task-verify"],
            }),
          ]}
        />,
      ),
    );
    const item = await screen.findByTestId("tc-chat-tool-call-1");
    fireEvent.click(item.querySelector("summary")!);

    const args = screen.getByTestId("tc-chat-tool-call-1-args");
    expect(args).toHaveAttribute("data-truncated", "true");
    expect(args).toHaveAttribute("tabindex", "0");
    expect(args).toHaveAccessibleName(/truncated to 600 characters/i);
    expect(args.textContent?.length).toBeLessThanOrEqual(601);
    expect(screen.getByText("2 delegated tasks")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "task-research" })).toHaveAttribute(
      "href",
      "#subagent-task-task-research",
    );
    expect(screen.getByRole("link", { name: "task-verify" })).toHaveAttribute(
      "data-subagent-task-id",
      "task-verify",
    );
  });

  it("interleaves the tool card between the parts of the turn by sequence_no", async () => {
    // THE REGRESSION TEST for the interleaving bug: text the model emitted
    // BEFORE the tool call must still render, and must render above the card.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="run-1"
          messages={INTERLEAVED_TURN}
          toolCalls={[toolCall({ sequenceNo: 4 })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-tool-call-1");
    const list = screen.getByTestId("tc-chat-messages");
    const ids = Array.from(list.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(ids).toEqual([
      "tc-chat-message-u1",
      "tc-chat-message-a1-part-0",
      "tc-chat-tool-call-1",
      "tc-chat-message-a1-part-1",
    ]);
    // Both halves of the turn survive — the pre-tool sentence used to be
    // overwritten by `final_response` and never reached the DOM at all.
    expect(screen.getByText("Let me check.")).toBeInTheDocument();
    expect(screen.getByText("It shipped at 09:14.")).toBeInTheDocument();
  });

  it("keeps a prior run's turn out of the active run's seq merge", async () => {
    // Every run numbers its events from 0, so a previous turn's seq 8 must NOT
    // compete with this run's seq 4. The old turn keeps document order and
    // stays above the live tail.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const priorTurn: ReadonlyArray<TcChatMessage> = [
      {
        message_id: "old",
        role: "assistant",
        run_id: "run-0",
        parts: [
          { type: "text", text: "Answer from the previous run.", seq: 8 },
        ],
      },
      ...INTERLEAVED_TURN,
    ];
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="run-1"
          messages={priorTurn}
          toolCalls={[toolCall({ sequenceNo: 4 })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-tool-call-1");
    const list = screen.getByTestId("tc-chat-messages");
    const ids = Array.from(list.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(ids).toEqual([
      "tc-chat-message-old",
      "tc-chat-message-u1",
      "tc-chat-message-a1-part-0",
      "tc-chat-tool-call-1",
      "tc-chat-message-a1-part-1",
    ]);
  });

  it("renders the tool card in focus mode too (shared transcript)", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="focus"
          messages={SAMPLE_MESSAGES}
          toolCalls={[toolCall()]}
        />,
      ),
    );
    expect(
      await screen.findByTestId("tc-chat-tool-call-1"),
    ).toBeInTheDocument();
  });

  it("hides a tool call that ran after the scrub cut in ghost mode", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat
            conversationId="c"
            mode="studio"
            toolCalls={[toolCall({ createdAtMs: 1716000060000 })]}
          />
        </SwimlaneScrubProvider>,
      ),
    );
    await screen.findByTestId("tc-chat-ghost-banner");
    expect(screen.queryByTestId("tc-chat-tool-call-1")).not.toBeInTheDocument();
  });
});

// PR-3.10 (FR-3.22) — in-chat approvals. There is now ONE ask card: the same
// compact `tc-write-gate` row for a parked write and for an ordinary
// `tool_action`, identical in Focus and Studio, and nothing at all once settled.
//
// What these tests used to assert is worth recording, because the names lied
// about it for a long time: the Studio arm claimed to be "the 4-zone
// ApprovalCard" and was in fact a `ConsentCard`, and the Focus arm was the SAME
// `ConsentCard` inside a `.conf-card` div that has no CSS rule anywhere in the
// product. The mode split was five deltas — a wrapper testid, an inert class,
// two button testids, and one sentence of visually-hidden copy — over one
// component. It is gone; `renderApprovalItem` takes no `mode`.
//
// The decision controls therefore moved into that one card, but they KEPT
// their approval-scoped names: `tc-chat-approval-approve-<id>` /
// `-reject-<id>` / `-body-approve-<id>`, supplied by `renderAskCard` through
// the card's `approveTestId` / `declineTestId` / `bodyApproveTestId` props.
// (`tc-write-gate-approve` and friends are the STANDALONE defaults, exercised
// by `TcWriteGateRow.test.tsx`; nothing in the mounted app emits them.)
// Queries below still go through `within(card)` — two asks on screen at once is
// a drawn state, and scoping proves the control belongs to the wrapper whose id
// it decides on rather than merely existing somewhere on the page.
function approval(overrides: Partial<TcChatApproval> = {}): TcChatApproval {
  return {
    approvalId: "appr-1",
    title: "Post to #launch-aurora",
    reason: "Copilot is asking before it writes outside this chat.",
    summary: "Posts the launch note to #launch-aurora",
    approvalKind: "tool_action",
    serverId: null,
    // What `buildCategory` emits for a real `read_only: false` payload. It used
    // to say ACTION, which is a word neither the backend nor the design has:
    // `stream_events._approval_category` maps that same boolean to WRITE.
    category: { vendor: "SLACK", access: "WRITE" },
    params: [{ label: "channel", value: "#launch-aurora" }],
    presentation: null,
    connectorTrust: EMPTY_CONNECTOR_TRUST,
    question: null,
    resolved: false,
    decision: null,
    createdAtMs: 1716000090000,
    ...overrides,
  };
}

describe("TcChat — a parked write is one row, not a question", () => {
  // The gate borrows the `ask_a_question` WIRE shape so it can reuse the
  // ApprovalCoordinator resume plumbing. Nothing about that makes it a
  // question, and routed as one it rendered a free-text box for a yes/no about
  // a real side effect — the backend ignores that text entirely and decides on
  // `decision`. The id prefix is what tells them apart.
  const writeGate = (over: Partial<TcChatApproval> = {}) =>
    approval({
      approvalId: "mcp_write:run_abc:call_1",
      approvalKind: "ask_a_question",
      title: "Create an issue in Parth-test",
      question: {
        header: "Approve write",
        question: "Allow Linear to run save_issue?",
        hint: null,
        options: [],
        multiSelect: false,
        allowFreeText: true,
      },
      category: { vendor: "linear", access: "WRITE" },
      ...over,
    });

  it("routes a parked write to the compact row, not the question card", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[writeGate()]} />,
      ),
    );

    expect(screen.getByTestId("tc-write-gate-row")).toBeTruthy();
    expect(
      screen.queryByTestId("tc-chat-question-card-mcp_write:run_abc:call_1"),
    ).toBeNull();
  });

  it("leaves a genuine agent question on the question card", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[writeGate({ approvalId: "appr-q1" })]}
        />,
      ),
    );

    expect(screen.queryByTestId("tc-write-gate-row")).toBeNull();
    expect(screen.getByTestId("tc-chat-question-card-appr-q1")).toBeTruthy();
  });

  it("a resolved write keeps its receipt rather than re-asking", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[writeGate({ resolved: true, decision: "approved" })]}
        />,
      ),
    );

    expect(screen.queryByTestId("tc-write-gate-row")).toBeNull();
  });
});

/** The ask card for one approval, scoped by its id-bearing wrapper. */
const askCard = (id: string): HTMLElement =>
  screen.getByTestId(`tc-chat-approval-${id}`);

// The three decision controls, by the APPROVAL-SCOPED names `renderAskCard`
// emits. Written as helpers rather than inline template strings so a rename has
// exactly one edit site here, and so a query for a control can never silently
// address a different card's button than the wrapper it was read through.
//
// `bodyApproveTid` is deliberately `…-body-approve-<id>` and not
// `…-approve-body-<id>`: the five journeys that press Approve select by the
// `tc-chat-approval-approve-` PREFIX, and the body approve is the one control
// an irreversible write withholds until its payload has rendered.
const approveTid = (id: string): string => `tc-chat-approval-approve-${id}`;
const rejectTid = (id: string): string => `tc-chat-approval-reject-${id}`;
const bodyApproveTid = (id: string): string =>
  `tc-chat-approval-body-approve-${id}`;

describe("TcChat approvals (PR-3.10 / FR-3.22)", () => {
  it.each(["studio", "focus"] as const)(
    "renders a pending tool_action as the compact ask card in %s mode",
    (mode) => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      render(
        withTransport(
          transport,
          <TcChat conversationId="c" mode={mode} approvals={[approval()]} />,
        ),
      );
      const card = askCard("appr-1");
      expect(card).toHaveTextContent("Post to #launch-aurora");
      // The ask is a CARD, in both modes — the one thing that may never
      // degrade into a line of text is the surface that takes the decision.
      expect(within(card).getByTestId("tc-write-gate")).toBeTruthy();
      expect(within(card).getByTestId(approveTid("appr-1"))).toHaveTextContent(
        "Approve",
      );
      // "Decline", not "Reject": the refusal is a decision the run continues
      // past, and it now sits FIRST so the safe option is the first Tab stop.
      expect(within(card).getByTestId(rejectTid("appr-1"))).toHaveTextContent(
        "Decline",
      );
      // `SLACK · WRITE` arrives as the projection's enum and is lower-cased on
      // the way out rather than reached via `text-transform`, so what a screen
      // reader hears is what is on screen. "write", not "action": the axis is
      // the backend's own word for `read_only: false`.
      expect(
        within(card).getByTestId("tc-write-gate-connector").textContent,
      ).toBe("SLACK · write");
      // The retired surfaces are GONE, not merely unasserted — checked in both
      // modes, because the `.conf-card` wrapper and the `tc-chat-conf-*`
      // controls only ever rendered in Focus.
      expect(screen.queryByTestId("tc-chat-conf-card-appr-1")).toBeNull();
      expect(screen.queryByTestId("tc-chat-conf-approve-appr-1")).toBeNull();
      expect(screen.queryByTestId("tc-chat-conf-consent-appr-1")).toBeNull();
      expect(document.querySelector(".conf-card")).toBeNull();
    },
  );

  it("renders the SAME card in Focus as in Studio, to the byte", () => {
    // The mode split was never a different card — it was one `ConsentCard`
    // under two wrapper testids, an inert `.conf-card` class, and one sentence
    // of visually-hidden copy. Comparing rendered markup is what stops it
    // growing back by accident: any mode-conditional attribute, class, label or
    // ordering difference inside the ask fails here, not just the ones someone
    // thought to name.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[approval()]} />,
      ),
    );
    // React's `useId` for the card's accessible description is unique per
    // MOUNT, and the two arms below are two mounts by construction. Normalising
    // it keeps the claim intact — "Focus and Studio render the same card" — by
    // excluding the one value React guarantees will differ between any two
    // renders, which no assertion about sameness could ever have included.
    const normalise = (html: string): string =>
      html.replace(/_r_[0-9a-z]+_/g, "_rID_");
    const studioHtml = normalise(
      screen.getByTestId("tc-chat-approval-appr-1").outerHTML.trim(),
    );
    // An equality over two empty wrappers would pass while proving nothing, so
    // the captured side is checked to be the real card first.
    expect(studioHtml).toContain(approveTid("appr-1"));
    expect(studioHtml).toContain("Post to #launch-aurora");
    // Torn down between the two, deliberately: RTL binds `screen` to
    // `document.body`, so leaving the first mount up would make the second
    // query ambiguous and the comparison meaningless.
    cleanup();

    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" approvals={[approval()]} />,
      ),
    );
    expect(
      normalise(screen.getByTestId("tc-chat-approval-appr-1").outerHTML.trim()),
    ).toBe(studioHtml);
  });

  it("fires onApprove / onReject with the approval id", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[approval()]}
          onApprove={onApprove}
          onReject={onReject}
        />,
      ),
    );
    // The control's testid names an approval and the handler is called with an
    // approval id. This pins that they are the SAME id — a scoped name is only
    // worth having if the card underneath it decides on the approval it names.
    const card = askCard("appr-1");
    fireEvent.click(within(card).getByTestId(approveTid("appr-1")));
    expect(onApprove).toHaveBeenCalledWith("appr-1");
    fireEvent.click(within(card).getByTestId(rejectTid("appr-1")));
    expect(onReject).toHaveBeenCalledWith("appr-1");
  });

  it("leaves nothing behind once settled, either way it went", async () => {
    // The receipt was one line of BARE TEXT in a transcript made of cards, which
    // is what made it read as debris. Approved, it restated the tool card right
    // below it; denied, it was the only non-card row in the thread. The decision
    // survives on the event stream, which the Approvals tab projects from.
    //
    // Nothing may be pinned above the composer either; that half of the older
    // rule still holds and is asserted alongside.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { rerender } = render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[approval({ resolved: true, decision: "approved" })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-messages");

    for (const decision of ["approved", "rejected"] as const) {
      rerender(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[approval({ resolved: true, decision })]}
          />,
        ),
      );
      // The receipt is asserted on the CLASS `ApprovalReceipt` really paints.
      // `tc-chat-approval-receipt-<id>` is a testid no product code emits, so
      // querying it was null whatever the transcript did.
      expect(document.querySelector(".atlas-approval-receipt")).toBeNull();
      // The wrapper goes with it — an empty row would leave the receipt's gap.
      expect(screen.queryByTestId("tc-chat-approval-item-appr-1")).toBeNull();
      // Settled ⇒ no live decision surface, and nothing pinned.
      expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
      expect(screen.queryByTestId("tc-chat-approvals-waiting")).toBeNull();
    }
  });

  it.each(["studio", "focus"] as const)(
    "announces itself as an approval, by name, in %s mode",
    (mode) => {
      // The announcement, in both modes, and it took two passes to get whole.
      //
      // `ConsentCard` carried a visually-hidden reassurance wired through
      // `aria-describedby` — the ONLY home for either of its two strings, never
      // painted — and the consolidation dropped it, leaving an approval to
      // announce as its name plus three buttons. It is back, but NOT as the old
      // copy: those were standing claims about the product ("You're always
      // asked before Copilot acts outside this chat"), reassuring and silent
      // about the decision actually in front of you. The card now derives its
      // description from the ask, which also makes irreversibility audible —
      // `data-risk` is not ARIA and the dot is `aria-hidden`, so that fact used
      // to reach a screen reader through the chip alone.
      //
      // Asserted in BOTH modes on purpose: one card, one announcement. A
      // mode-varying description would rebuild the split this change removed.
      // The wording itself is pinned in `TcWriteGateRow.test.tsx`.
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      render(
        withTransport(
          transport,
          <TcChat conversationId="c" mode={mode} approvals={[approval()]} />,
        ),
      );
      const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
      expect(card).toHaveAccessibleName("Approval: Post to #launch-aurora");
      expect(card).toHaveAccessibleDescription(/paused on this decision/i);
      // The design reserves "Approve & sign" for actions that actually reach a
      // wallet; it arrived via `presentation.approve_label`, which the unified
      // card does not read. A generic approval promises no signature either way.
      expect(within(card).getByTestId(approveTid("appr-1"))).toHaveTextContent(
        "Approve",
      );
    },
  );

  it("hides approvals while scrubbed off-now", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat conversationId="c" mode="studio" approvals={[approval()]} />
        </SwimlaneScrubProvider>,
      ),
    );
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
    // Nowhere else either. Asserting the absence of the old strip's testid
    // proved nothing once that strip was deleted — no product code emits the
    // id, so the assertion passed over any markup at all, including a strip
    // that came back under a different name. The invariant is structural, so
    // it is checked structurally.
    expect(
      document.querySelectorAll("[data-testid^=tc-chat-approval]"),
    ).toHaveLength(0);
  });
});

// THE SAFETY PROPERTY, PINNED AT THE WIRING BOUNDARY.
//
// `TcWriteGateRow.test.tsx` proves the rule against a literal `irreversible`
// prop, which proves the COMPONENT and nothing about whether anything ever sets
// it. The predicate in between is `isIrreversible`, so a projection that stops
// setting the flag silently returns false for every approval while every
// component test stays green and the destructive lane never renders. That is
// the "a fix can land on a dead branch" shape, and these are the tests that
// would see it.
//
// This block used to drive `category.access: "DESTRUCTIVE"` — a label neither
// producer of that field could emit, so it pinned the wire over a value only a
// fixture could supply. The flag is now set by `buildIrreversible` from
// `op_class` / `risk_level`, and `approvalProjection.test.ts` pins THOSE against
// real payload shapes. Between the two files the chain is covered end to end:
// wire → projection → predicate → card.
describe("TcChat — a destructive ask reaches the card's destructive lane", () => {
  const destructive = (over: Partial<TcChatApproval> = {}) =>
    approval({
      title: "Delete the launch channel",
      category: { vendor: "SLACK", access: "WRITE" },
      irreversible: true,
      ...over,
    });

  it.each(["studio", "focus"] as const)(
    "exposes no one-click approve in %s mode",
    (mode) => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      render(
        withTransport(
          transport,
          <TcChat conversationId="c" mode={mode} approvals={[destructive()]} />,
        ),
      );
      const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
      expect(card.getAttribute("data-risk")).toBe("high");
      // Not "there is no approve": no approve reachable in ONE CLICK from the
      // collapsed card. Both names are checked, because the rule is expressed
      // by the SPLIT between them.
      expect(within(card).queryByTestId(approveTid("appr-1"))).toBeNull();
      expect(within(card).queryByTestId(bodyApproveTid("appr-1"))).toBeNull();
      // Declining stays one click, in every state. Making someone expand to say
      // no is what leaves a write parked forever.
      expect(within(card).getByTestId(rejectTid("appr-1"))).toBeTruthy();
      // …and the chip is the only non-visual signal that this is destructive:
      // the dot is aria-hidden and `data-risk` is not an ARIA attribute.
      expect(within(card).getByTestId("tc-write-gate-chip").textContent).toBe(
        "can't be undone",
      );
    },
  );

  it("opens approval only after the payload it is made on is on screen", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const onApprove = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[destructive()]}
          onApprove={onApprove}
        />,
      ),
    );
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    // Still no one-click approve — the rule is ORDER, not location.
    expect(within(card).queryByTestId(approveTid("appr-1"))).toBeNull();
    fireEvent.click(within(card).getByTestId(bodyApproveTid("appr-1")));
    expect(onApprove).toHaveBeenCalledWith("appr-1");
  });

  it("withholds approval entirely when the ask carries no payload", () => {
    // A gate can open before its approval projection lands, so an empty params
    // frame is a REAL state — and approving over it is exactly the blind
    // approval the whole lane exists to prevent.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[destructive({ params: [] })]}
        />,
      ),
    );
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    expect(within(card).getByTestId("tc-write-gate-body")).toBeTruthy();
    expect(within(card).queryByTestId(approveTid("appr-1"))).toBeNull();
    expect(within(card).queryByTestId(bodyApproveTid("appr-1"))).toBeNull();
  });
});

// The generalisation, at the wiring boundary: every gate-shaped prop is
// optional, so an ordinary tool approval — no connector, no ledger row, no
// arguments, no reason — must render through the same card WITHOUT a hole in
// it. Each of these is a real production state, not a degraded one: `category`
// is null for every non-MCP ask, `ledgerId` is undefined for every approval
// with no `gate.opened` row (i.e. all of them except a parked write), and
// `params` is empty whenever the tool's arguments are non-primitive.
describe("TcChat — a bare approval renders no empty frames", () => {
  const bare = (over: Partial<TcChatApproval> = {}) =>
    approval({
      title: "Send the weekly digest",
      category: null,
      params: [],
      reason: "",
      summary: null,
      ...over,
    });

  it("omits the meta, the params frame, the audit anchor and the reason", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[bare()]} />,
      ),
    );
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    expect(within(card).getByTestId("tc-write-gate-title").textContent).toBe(
      "Send the weekly digest",
    );
    // No connector ⇒ no meta span, rather than a dangling separator.
    expect(within(card).queryByTestId("tc-write-gate-connector")).toBeNull();
    // Not destructive by default: an unlabelled ask is an ordinary one, never a
    // severity nobody asserted.
    expect(within(card).queryByTestId("tc-write-gate-chip")).toBeNull();
    expect(within(card).getByTestId(approveTid("appr-1"))).toBeTruthy();

    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    expect(within(card).getByTestId("tc-write-gate-body")).toBeTruthy();
    // Nothing framed: an empty params table reads as "it will send nothing",
    // which is a different claim from "we do not have the arguments".
    expect(within(card).queryByTestId("tc-write-gate-body-params")).toBeNull();
    // And the audit anchor is OMITTED, never guessed. It anchors on
    // `gate.opened` — a different event from the `approval_requested` this card
    // was projected from — so deriving one locally would point at the wrong
    // ledger row. No host join ⇒ no line.
    expect(
      within(card).queryByTestId("tc-write-gate-body-ledger-id"),
    ).toBeNull();
    expect(within(card).queryByTestId("tc-write-gate-body-reason")).toBeNull();
  });

  it("still says whether it can be undone, which is the one thing it always knows", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[bare()]} />,
      ),
    );
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    expect(
      within(card).getByTestId("tc-write-gate-body-reversibility").textContent,
    ).toBe("You can undo this from the connector if it's wrong.");
  });
});

// THE WIRE, end to end. The projection has carried `presentation` all along;
// `renderAskCard` simply stopped reading it, so a shape the backend spent a
// whole projector deriving reached the client and rendered nowhere. Two lanes
// reach this card and only one of them ever HAS a presentation: the write gate
// rides `ask_a_question`, whose allow-list carries no presentation and no
// arguments, so it must be unaffected. These pin both halves.
describe("TcChat — the projected shape reaches the card", () => {
  const draft =
    "Launch Week is here. Over the next 7 days we're shipping one thing a day.";

  const shaped = (over: Partial<TcChatApproval> = {}) =>
    approval({
      approvalKind: "mcp_tool",
      presentation: {
        layout: "preview",
        approveLabel: "Approve & send",
        rejectLabel: null,
        provenance: null,
        rows: [],
        preview: { text: draft, meta: "14 words · 72 characters" },
      },
      ...over,
    });

  function mount(approvals: readonly TcChatApproval[]): void {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={approvals} />,
      ),
    );
  }

  it("shows the draft the connector will receive, and the verb for sending it", () => {
    mount([shaped()]);
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    // The verb is on the collapsed card — it is what the button promises, so
    // it cannot be behind the disclosure.
    expect(within(card).getByTestId(approveTid("appr-1")).textContent).toBe(
      "Approve & send",
    );
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    expect(
      within(card).getByTestId("tc-write-gate-body-preview").textContent,
    ).toContain(draft);
    expect(
      within(card).getByTestId("tc-write-gate-body-preview-meta").textContent,
    ).toBe("14 words · 72 characters");
  });

  it("draws a batch as its line items", () => {
    mount([
      shaped({
        title: "Sign the payout batch",
        presentation: {
          layout: "rows",
          approveLabel: "Approve & sign",
          rejectLabel: null,
          provenance: null,
          preview: null,
          rows: [
            {
              label: "Mira Patel",
              value: "2,400 USDC",
              note: "design",
              initials: "MP",
              rowId: "p1",
              status: "pending",
              decidable: true,
            },
          ],
        },
      }),
    ]);
    const card = within(askCard("appr-1")).getByTestId("tc-write-gate");
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    const rows = within(card).getByTestId("tc-write-gate-body-rows");
    expect(rows.textContent).toContain("Mira Patel");
    expect(rows.textContent).toContain("2,400 USDC");
  });

  it("leaves a parked write — which never carries a shape — untouched", () => {
    mount([
      approval({
        approvalId: "mcp_write:run_abc:call_1",
        approvalKind: "ask_a_question",
        title: "Create an issue in Parth-test",
        presentation: null,
      }),
    ]);
    const card = within(askCard("mcp_write:run_abc:call_1")).getByTestId(
      "tc-write-gate",
    );
    expect(
      within(card).getByTestId(approveTid("mcp_write:run_abc:call_1"))
        .textContent,
    ).toBe("Approve");
    fireEvent.click(within(card).getByTestId("tc-write-gate-review"));
    expect(within(card).queryByTestId("tc-write-gate-body-preview")).toBeNull();
    expect(within(card).queryByTestId("tc-write-gate-body-rows")).toBeNull();
  });
});

// WC-P5a (AD-6/AD-7) — the mid-run MCP-OAuth Connect card. An `mcp_auth` gate (or
// a `mcp_discovery:` catalog suggestion) renders Connect / Skip wired to the host
// `McpAuthPort`, NOT the Approve/Reject → `/decision` path (which resolves only
// `mcp_tool`/`tool_action`/`ask_a_question`). TcChat never POSTs — resolution is
// the injected port; a normal approval is unaffected (regression guard).
function mcpAuthApproval(
  overrides: Partial<TcChatApproval> = {},
): TcChatApproval {
  return approval({
    approvalId: "mcp_auth:run_1:linear",
    title: "Connect Linear",
    reason: "Sign in to Linear so Copilot can read your issues.",
    summary: "MCP authentication required",
    approvalKind: "mcp_auth",
    serverId: "linear",
    // `access: null` is what an `mcp_auth_required` payload really projects to:
    // it names a `server_id` and carries no `read_only`, so there is no axis to
    // state. It used to read ACTION here, labelling a connector the run merely
    // wants to sign into as one taking an action.
    category: { vendor: "Linear", access: null },
    params: [],
    ...overrides,
  });
}

function makePort(): {
  port: McpAuthPort;
  beginAuth: ReturnType<typeof vi.fn>;
  skipAuth: ReturnType<typeof vi.fn>;
  installFromCatalog: ReturnType<typeof vi.fn>;
} {
  const beginAuth = vi.fn();
  const skipAuth = vi.fn();
  const installFromCatalog = vi.fn();
  return {
    port: { beginAuth, skipAuth, installFromCatalog },
    beginAuth,
    skipAuth,
    installFromCatalog,
  };
}

describe("TcChat MCP-OAuth Connect card (WC-P5a / AD-7)", () => {
  it("renders a Connect / Skip card for an `mcp_auth` gate, not Approve/Reject", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
        />,
      ),
    );
    // The Connect card renders (with Connect + Skip)…
    expect(
      screen.getByTestId("tc-chat-mcp-auth-mcp_auth:run_1:linear"),
    ).toHaveTextContent("Connect Linear");
    expect(
      screen.getByTestId("tc-chat-mcp-connect-mcp_auth:run_1:linear"),
    ).toHaveTextContent("Connect");
    // "Deny" not "Skip": the design's fourth connector state offers to reverse
    // this ("Reconsider"), which only reads as sensible if it was a decision.
    expect(
      screen.getByTestId("tc-chat-mcp-skip-mcp_auth:run_1:linear"),
    ).toHaveTextContent("Deny");
    // …and NOT the Approve/Decline `/decision` ask card. Asserted on THREE
    // names that all exist in product code — the card root plus the two
    // approval-scoped decision controls this very id would carry if the ask
    // card had rendered — so none of these negatives can pass vacuously.
    expect(screen.queryByTestId("tc-write-gate")).toBeNull();
    expect(
      screen.queryByTestId(approveTid("mcp_auth:run_1:linear")),
    ).toBeNull();
    expect(screen.queryByTestId(rejectTid("mcp_auth:run_1:linear"))).toBeNull();
  });

  it("Connect calls beginAuth(serverId), Skip calls skipAuth(serverId); onApprove/onReject never fire", () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve(SAMPLE_RESPONSE),
    );
    const { port, beginAuth, skipAuth } = makePort();
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          onApprove={onApprove}
          onReject={onReject}
          mcpAuthPort={port}
        />,
      ),
    );
    fireEvent.click(
      screen.getByTestId("tc-chat-mcp-connect-mcp_auth:run_1:linear"),
    );
    expect(beginAuth).toHaveBeenCalledWith("linear", {
      connectorSlug: null,
    });
    fireEvent.click(
      screen.getByTestId("tc-chat-mcp-skip-mcp_auth:run_1:linear"),
    );
    expect(skipAuth).toHaveBeenCalledWith("linear");
    // The connector-auth gate NEVER resolves via the `/decision` handlers.
    expect(onApprove).not.toHaveBeenCalled();
    expect(onReject).not.toHaveBeenCalled();
    // TcChat itself opened no transport request (host owns the redirect).
    expect(record.calls.some((c) => c.path.includes("/decision"))).toBe(false);
  });

  it("recognises a `mcp_discovery:` suggestion as a Connect card too", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port, beginAuth } = makePort();
    const onApprove = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[
            // A suggestion carries the `mcp_discovery:` id prefix; even if its
            // kind were stripped, the prefix routes it to the Connect card.
            mcpAuthApproval({
              approvalId: "mcp_discovery:run_1:seed:linear",
              approvalKind: "unknown",
              serverId: "linear",
            }),
          ]}
          onApprove={onApprove}
          mcpAuthPort={port}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-mcp-auth-mcp_discovery:run_1:seed:linear"),
    ).not.toBeNull();
    fireEvent.click(
      screen.getByTestId("tc-chat-mcp-connect-mcp_discovery:run_1:seed:linear"),
    );
    expect(beginAuth).toHaveBeenCalledWith("linear", {
      connectorSlug: null,
    });
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("renders the Connect card in Focus mode too", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="focus"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-mcp-connect-mcp_auth:run_1:linear"),
    ).not.toBeNull();
    // Not the ask card either — and in Focus specifically, which is where the
    // retired `.conf-card` variant used to claim this. There is no Focus
    // variant now, so the check that means something is the CARD's absence.
    expect(screen.queryByTestId("tc-write-gate")).toBeNull();
  });

  it("degrades gracefully with no port wired (buttons render but are inert)", () => {
    const { transport, record } = makeTransport(() =>
      Promise.resolve(SAMPLE_RESPONSE),
    );
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
        />,
      ),
    );
    const connect = screen.getByTestId(
      "tc-chat-mcp-connect-mcp_auth:run_1:linear",
    );
    expect(connect).toBeDisabled();
    // Clicking an inert Connect never throws and never POSTs anything.
    fireEvent.click(connect);
    expect(record.calls.some((c) => c.path.includes("/decision"))).toBe(false);
  });

  it("still routes a normal tool_action approval through Approve/Decline (no regression)", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    const onApprove = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[approval()]}
          onApprove={onApprove}
          mcpAuthPort={port}
        />,
      ),
    );
    // A tool_action approval keeps the Approve/Decline `/decision` card…
    fireEvent.click(
      within(askCard("appr-1")).getByTestId(approveTid("appr-1")),
    );
    expect(onApprove).toHaveBeenCalledWith("appr-1");
    // …and never the Connect card.
    expect(screen.queryByTestId("tc-chat-mcp-auth-appr-1")).toBeNull();
  });

  // The card has always DRAWN four states; until now it was always handed
  // `pending`, because nothing in the run stream can see a consent popup. The
  // owner is `useConnectorConsentStates` in the Run cockpit, and these tests pin
  // the seam it drives — that `connectorConsentStates` reaches the card, keyed
  // by `server_id`.
  it("renders the state the host reports for this server, not a hardcoded pending", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
          connectorConsentStates={{ linear: "connecting" }}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-connector-mcp_auth:run_1:linear"),
    ).toHaveAttribute("data-state", "connecting");
    // Connecting swaps the two actions for a single Cancel.
    expect(screen.getByTestId("cc-cancel")).toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-chat-mcp-connect-mcp_auth:run_1:linear"),
    ).toBeNull();
  });

  it("reaches connected — the state only the host's OAuth return can report", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
          connectorConsentStates={{ linear: "connected" }}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-connector-mcp_auth:run_1:linear"),
    ).toHaveAttribute("data-state", "connected");
  });

  it("offers Reconsider once denied", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port, beginAuth } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
          connectorConsentStates={{ linear: "denied" }}
        />,
      ),
    );
    // Reversible by design — a denial is a decision, not a dead end.
    fireEvent.click(screen.getByTestId("cc-reconsider"));
    expect(beginAuth).toHaveBeenCalledWith("linear", {
      connectorSlug: null,
    });
  });

  it("Cancel while connecting returns the card to pending", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    const onCancel = vi.fn();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
          connectorConsentStates={{ linear: "connecting" }}
          onConnectorConsentCancel={onCancel}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("cc-cancel"));
    expect(onCancel).toHaveBeenCalledWith("linear");
  });

  it("keys state by server_id, so one connector's state never bleeds into another's", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[
            mcpAuthApproval(),
            mcpAuthApproval({
              approvalId: "mcp_auth:run_1:notion",
              title: "Connect Notion",
              serverId: "notion",
            }),
          ]}
          mcpAuthPort={port}
          connectorConsentStates={{ linear: "denied" }}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-connector-mcp_auth:run_1:linear"),
    ).toHaveAttribute("data-state", "denied");
    expect(
      screen.getByTestId("tc-chat-connector-mcp_auth:run_1:notion"),
    ).toHaveAttribute("data-state", "pending");
  });

  // A slug-keyed host (desktop) cannot start a connect from a `server_id`: its
  // whole path is keyed on the catalog slug, because the backend reconstructs
  // the loopback redirect from a validated port rather than accepting one from
  // the client. So the card names the connector both ways and lets each host
  // use the key its own flow is built on.
  describe("naming the connector for a slug-keyed host", () => {
    function connectWith(overrides: Partial<TcChatApproval>) {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      const { port, beginAuth } = makePort();
      render(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[mcpAuthApproval(overrides)]}
            mcpAuthPort={port}
          />,
        ),
      );
      fireEvent.click(
        screen.getByTestId(
          `tc-chat-mcp-connect-${mcpAuthApproval(overrides).approvalId}`,
        ),
      );
      return beginAuth;
    }

    it("passes an installed server's connector slug", () => {
      const beginAuth = connectWith({ connectorSlug: "linear" });
      expect(beginAuth).toHaveBeenCalledWith("linear", {
        connectorSlug: "linear",
      });
    });

    it("falls back to a suggestion's catalog slug", () => {
      // Different fields, same answer to "which connector". A slug-keyed
      // connect is install-then-auth and idempotent, so it does not care which
      // of the two named it.
      const beginAuth = connectWith({
        approvalId: "mcp_discovery:run_1:seed:notion",
        serverId: "seed:notion",
        catalogSlug: "notion",
      });
      expect(beginAuth).toHaveBeenCalledWith("seed:notion", {
        connectorSlug: "notion",
      });
    });

    it("prefers the installed identity when a card somehow carries both", () => {
      const beginAuth = connectWith({
        connectorSlug: "linear",
        catalogSlug: "linear-catalog",
      });
      expect(beginAuth).toHaveBeenCalledWith("linear", {
        connectorSlug: "linear",
      });
    });

    it("reports null for a custom server rather than guessing a slug", () => {
      // A pasted-URL MCP server has no catalog identity. The honest answer lets
      // the host say "not available here" instead of failing mid-flow.
      const beginAuth = connectWith({});
      expect(beginAuth).toHaveBeenCalledWith("linear", { connectorSlug: null });
    });
  });

  // Mute lands on the card because that is where the intent forms. The
  // distinction that matters: a GATE is a connector the user installed and the
  // run is blocked on — denying it is a decision about this run. A slugged
  // SUGGESTION is a connector they never asked for, and denying that is a
  // decision about the connector.
  describe("mute-on-deny", () => {
    it("mutes when denying an uninstalled catalog suggestion", () => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      const { port, skipAuth } = makePort();
      const onMute = vi.fn();
      render(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[
              mcpAuthApproval({
                approvalId: "mcp_discovery:run_1:seed:linear",
                serverId: "seed:linear",
                catalogSlug: "linear",
              }),
            ]}
            mcpAuthPort={port}
            onConnectorMute={onMute}
          />,
        ),
      );
      fireEvent.click(
        screen.getByTestId("tc-chat-mcp-skip-mcp_discovery:run_1:seed:linear"),
      );
      // Both: the run stops asking AND the suggestion never returns.
      expect(skipAuth).toHaveBeenCalledWith("seed:linear");
      // Keyed by SLUG, not `server_id` — no server row exists for a connector
      // the user has not installed.
      expect(onMute).toHaveBeenCalledWith("linear");
    });

    it("does NOT mute when denying a gate on an installed connector", () => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      const { port, skipAuth } = makePort();
      const onMute = vi.fn();
      render(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[mcpAuthApproval()]}
            mcpAuthPort={port}
            onConnectorMute={onMute}
          />,
        ),
      );
      fireEvent.click(
        screen.getByTestId("tc-chat-mcp-skip-mcp_auth:run_1:linear"),
      );
      expect(skipAuth).toHaveBeenCalledWith("linear");
      // "Never suggest this again" is meaningless for something already
      // installed, and muting it here would quietly hide it from future runs.
      expect(onMute).not.toHaveBeenCalled();
    });
  });

  describe("connected receipt", () => {
    it("collapses to the action-free connected card", () => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      const { port } = makePort();
      render(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[mcpAuthApproval({ title: "Linear" })]}
            mcpAuthPort={port}
            connectorConsentStates={{ linear: "connected" }}
          />,
        ),
      );
      const card = screen.getByTestId(
        "tc-chat-connector-mcp_auth:run_1:linear",
      );
      expect(card).toHaveAttribute("data-state", "connected");
      expect(card).toHaveTextContent("Linear connected");
      expect(card.querySelector("button")).toBeNull();
    });

    it("retains the same connected card after the next run clears the approval projection", () => {
      const { transport } = makeTransport(() =>
        Promise.resolve(SAMPLE_RESPONSE),
      );
      render(
        withTransport(
          transport,
          <TcChat
            conversationId="c"
            mode="studio"
            approvals={[]}
            connectedConnectorReceipt={{
              approvalId: "mcp_auth:run_1:linear",
              serverId: "linear",
              displayName: "Linear",
            }}
          />,
        ),
      );

      const card = screen.getByTestId(
        "tc-chat-connector-mcp_auth:run_1:linear",
      );
      expect(card).toHaveAttribute("data-state", "connected");
      expect(card).toHaveTextContent("Linear connected");
      expect(card.querySelector("button")).toBeNull();
    });
  });

  it("falls back to pending when the host reports nothing (hook-less host)", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { port } = makePort();
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[mcpAuthApproval()]}
          mcpAuthPort={port}
        />,
      ),
    );
    expect(
      screen.getByTestId("tc-chat-connector-mcp_auth:run_1:linear"),
    ).toHaveAttribute("data-state", "pending");
  });
});

describe("TcChat — agent todos", () => {
  const TODOS = {
    listId: "run-1:todos:1",
    generation: 1,
    todos: [
      { content: "Pull the Q3 export", status: "completed" as const },
      { content: "Reconcile ids", status: "in_progress" as const },
    ],
    completedCount: 1,
    isComplete: false,
    sequenceNo: 4,
  };

  it("pins the checklist directly above the composer in Studio", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" todos={TODOS} />,
      ),
    );

    const panel = await screen.findByTestId("tc-todo-list");
    expect(panel.nextElementSibling).toBe(
      screen.getByTestId("tc-chat-composer-slot"),
    );
  });

  it("pins the same checklist above the composer in Focus", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" todos={TODOS} />,
      ),
    );

    const panel = await screen.findByTestId("tc-todo-list");
    expect(panel.nextElementSibling).toBe(
      screen.getByTestId("tc-chat-composer-slot"),
    );
  });

  it("renders no panel for a run that never opened a checklist", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="studio" />),
    );

    await screen.findByTestId("tc-chat");
    expect(screen.queryByTestId("tc-todo-list")).toBeNull();
  });

  it("hides the checklist while the transcript is scrubbed", async () => {
    // The snapshot carries no per-row timestamps, so there is nothing to rewind
    // it to. Showing today's list beside a time-travelled transcript would
    // assert a state that did not hold at the cut.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <SwimlaneScrubProvider value={{ scrubbedTo: 1716000030000 }}>
          <TcChat conversationId="c" mode="studio" todos={TODOS} />
        </SwimlaneScrubProvider>,
      ),
    );

    await screen.findByTestId("tc-chat-ghost-banner");
    expect(screen.queryByTestId("tc-todo-list")).toBeNull();
  });
});

describe("TcChat — inline approvals", () => {
  it("anchors the card in the transcript, not in a pinned strip", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[approval()]} />,
      ),
    );

    const card = await screen.findByTestId("tc-chat-approval-item-appr-1");
    expect(screen.getByTestId("tc-chat-messages").contains(card)).toBe(true);
    // The two strips are gone — but NOT asserted by their old testids, which no
    // product code emits any more: `queryByTestId("tc-chat-approvals")` is null
    // against any markup whatsoever, so it would keep passing if a pinned strip
    // returned under a new name, which is precisely the regression it was meant
    // to guard. What the strips actually violated was structural — an approval
    // node living outside the transcript — so that is what is checked.
    const transcript = screen.getByTestId("tc-chat-messages");
    // ONE sanctioned exception, named rather than pattern-excluded: the
    // reachability line above the composer. It exists BECAUSE the card can now
    // scroll away, which is the thing the pinned strip used to prevent, and it
    // is a line of chrome rather than a decision surface — you cannot approve
    // from it. Naming it is the point: anything else that appears outside the
    // transcript fails here, including a strip returning under a new testid.
    const WAITING_LINE = "tc-chat-approvals-waiting";
    const approvalNodes = [
      ...document.querySelectorAll("[data-testid^=tc-chat-approval]"),
    ].filter((node) => node.getAttribute("data-testid") !== WAITING_LINE);
    expect(approvalNodes.length).toBeGreaterThan(0);
    for (const node of approvalNodes) {
      expect({
        testId: node.getAttribute("data-testid"),
        inTranscript: transcript.contains(node),
      }).toEqual({
        testId: node.getAttribute("data-testid"),
        inTranscript: true,
      });
    }
  });

  it("renders a pending approval even while the messages are still loading", async () => {
    // The regression the strip was structurally immune to: approvals used to
    // live outside the load state. Inline, an early return on `loading` hid a
    // parked run's only way out.
    let release: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    const { transport } = makeTransport(() => pending as Promise<unknown>);
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[approval()]} />,
      ),
    );

    expect(screen.getByTestId("tc-chat-loading")).toBeInTheDocument();
    expect(
      await screen.findByTestId("tc-chat-approval-item-appr-1"),
    ).toBeInTheDocument();
    release(SAMPLE_RESPONSE);
  });

  it("offers one waiting affordance per pending approval count, and none when resolved", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { rerender } = render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[
            approval(),
            approval({ approvalId: "appr-2", title: "Second ask" }),
          ]}
        />,
      ),
    );

    const waiting = await screen.findByTestId("tc-chat-approvals-waiting");
    expect(waiting).toHaveAttribute("data-pending-count", "2");
    expect(waiting).toHaveTextContent("2 approvals waiting");

    rerender(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[approval({ resolved: true, decision: "approved" })]}
        />,
      ),
    );
    expect(screen.queryByTestId("tc-chat-approvals-waiting")).toBeNull();
  });

  it("marks the in-progress todo as waiting while an approval is pending", async () => {
    // The spinner asserts motion; a parked run has none. `SUBAGENT_PAUSED`
    // exists because paused-ness must never be inferred from the ABSENCE of a
    // completion — a pending approval is a positive fact, so this is sound.
    const todos = {
      listId: "run-1:todos:1",
      generation: 1,
      todos: [
        { content: "Read the folder", status: "in_progress" as const },
        { content: "Summarise it", status: "pending" as const },
      ],
      completedCount: 0,
      isComplete: false,
      sequenceNo: 3,
    };
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    const { rerender } = render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          todos={todos}
          approvals={[approval()]}
        />,
      ),
    );

    await screen.findByTestId("tc-todo-list");
    expect(screen.getByTestId("tc-todo-list")).toHaveAttribute(
      "data-blocked",
      "true",
    );
    const row = screen.getAllByTestId("tc-todo-row")[0];
    expect(row).toHaveAttribute("data-waiting", "true");
    expect(within(row).getByTestId("tc-todo-waiting")).toBeInTheDocument();
    expect(within(row).queryByTestId("tc-todo-spinner")).toBeNull();

    // Resolve it and the row goes back to spinning: work has resumed.
    rerender(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          todos={todos}
          approvals={[approval({ resolved: true, decision: "approved" })]}
        />,
      ),
    );
    const resumed = screen.getAllByTestId("tc-todo-row")[0];
    expect(resumed).toHaveAttribute("data-waiting", "false");
    expect(within(resumed).getByTestId("tc-todo-spinner")).toBeInTheDocument();
  });

  it("leaves the checklist unblocked when nothing is pending", async () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          todos={{
            listId: "run-1:todos:1",
            generation: 1,
            todos: [{ content: "Working", status: "in_progress" as const }],
            completedCount: 0,
            isComplete: false,
            sequenceNo: 1,
          }}
        />,
      ),
    );

    await screen.findByTestId("tc-todo-list");
    expect(screen.getByTestId("tc-todo-list")).toHaveAttribute(
      "data-blocked",
      "false",
    );
    expect(screen.getByTestId("tc-todo-spinner")).toBeInTheDocument();
  });
});

// PRD-03 (D-3.1) — the fold is unit-tested in `groupActivity.test.ts`, but that
// proves only the pure function. These assert the WIRING: that `TcChat` passes
// an `isGroupable` which leaves an approval outside the collapsed group.
//
// The hazard is specific and this file already documents its cousin: an
// approval buried in a collapsed group is a parked run with no visible way out.
describe("TcChat — activity grouping keeps approvals reachable (PRD-03)", () => {
  it("renders a pending approval OUTSIDE the tool-run group", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[
            toolCall({ id: "call-1", createdAtMs: 1716000010000 }),
            toolCall({ id: "call-2", createdAtMs: 1716000020000 }),
          ]}
          approvals={[approval({ createdAtMs: 1716000030000 })]}
        />,
      ),
    );
    const card = screen.getByTestId("tc-chat-approval-appr-1");
    // Assert the group EXISTS first — otherwise a fold that grouped nothing at
    // all would satisfy the containment check vacuously.
    const groups = screen.getAllByTestId("tool-run-group");
    expect(groups).toHaveLength(1);
    for (const group of groups) {
      expect(group.contains(card)).toBe(false);
    }
    // And the approve control is actually clickable, not just present.
    expect(within(card).getByTestId(approveTid("appr-1"))).toBeVisible();
  });

  it("splits a run of tool calls in two when an approval lands between them", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[
            toolCall({ id: "call-1", sequenceNo: 1 }),
            toolCall({ id: "call-2", sequenceNo: 2 }),
            toolCall({ id: "call-3", sequenceNo: 4 }),
            toolCall({ id: "call-4", sequenceNo: 5 }),
          ]}
          approvals={[approval({ sequenceNo: 3 })]}
        />,
      ),
    );
    expect(screen.getAllByTestId("tool-run-group")).toHaveLength(2);
  });
});

// THE WORK THE MODEL DID WHILE THINKING BELONGS TO THE THOUGHT.
//
// A turn is `reasoning → tools → text`. Rendered as three peers that was a
// "Thought for 6s" row, then a "Worked for 140ms · 2 steps" row, then the
// answer — two collapsed disclosures in two visual languages, describing one
// stretch of work between them. The tool calls are not the thought's sibling;
// they are what it DID.
describe("TcChat — tool calls folded into the thought", () => {
  const RUN = "run-1";

  /** `reasoning(1) → …cards… → text(9)`, the shape the fold is about. */
  function thinkingTurn(reasoning = "weighing it up"): TcChatMessage[] {
    return [
      {
        message_id: "u1",
        role: "user",
        parts: [{ type: "text", text: "any csv here?" }],
        run_id: RUN,
      },
      {
        message_id: "a1",
        role: "assistant",
        run_id: RUN,
        parts: [
          { type: "reasoning", text: reasoning, seq: 1 },
          { type: "text", text: "Yes — two of them.", seq: 9 },
        ],
      },
    ];
  }

  function renderTurn(props: Partial<TcChatProps> = {}) {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    return render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId={RUN}
          messages={thinkingTurn()}
          {...props}
        />,
      ),
    );
  }

  it("puts a card that ran during the thought inside it", () => {
    renderTurn({
      toolCalls: [
        toolCall({ id: "call-1", runId: RUN, sequenceNo: 2 }),
        toolCall({ id: "call-2", runId: RUN, sequenceNo: 3 }),
      ],
    });
    const block = screen.getByTestId("cs-thinking-block");
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-1"))).toBe(
      true,
    );
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-2"))).toBe(
      true,
    );
    // And there is no longer a SECOND disclosure saying the same thing.
    expect(screen.queryByTestId("tool-run-group")).not.toBeInTheDocument();
  });

  it("states how many steps it folded away", () => {
    // The count is the honesty guarantee: the row is collapsed by default, so
    // without it, folding the cards in would simply be hiding them.
    renderTurn({
      toolCalls: [
        toolCall({ id: "call-1", runId: RUN, sequenceNo: 2 }),
        toolCall({ id: "call-2", runId: RUN, sequenceNo: 3 }),
      ],
    });
    expect(screen.getByTestId("cs-thinking-block-steps")).toHaveTextContent(
      "· 2 steps",
    );
  });

  it("leaves a card that ran AFTER the thought outside it", () => {
    // `reasoning → text → tool` — thought, spoke, then acted. That tool is not
    // part of the thought, and a fold that swallowed it would be describing
    // the transcript's shape rather than the model's behaviour.
    renderTurn({
      toolCalls: [toolCall({ id: "call-1", runId: RUN, sequenceNo: 10 })],
    });
    const block = screen.getByTestId("cs-thinking-block");
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-1"))).toBe(
      false,
    );
    expect(screen.queryByTestId("cs-thinking-block-steps")).toBeNull();
  });

  it("never folds an approval away, even mid-thought", () => {
    // The rule `groupActivityStream` already documents: an approval buried in
    // a collapsed row hides a parked run's only way out. The fold stops at it,
    // so the card after it stays outside too.
    renderTurn({
      toolCalls: [
        toolCall({ id: "call-1", runId: RUN, sequenceNo: 2 }),
        toolCall({ id: "call-2", runId: RUN, sequenceNo: 4 }),
      ],
      approvals: [approval({ sequenceNo: 3 })],
    });
    const block = screen.getByTestId("cs-thinking-block");
    const card = screen.getByTestId("tc-chat-approval-appr-1");
    expect(block.contains(card)).toBe(false);
    expect(within(card).getByTestId(approveTid("appr-1"))).toBeVisible();
    // seq 2 folded in; seq 4 is past the approval, so it stays a peer.
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-1"))).toBe(
      true,
    );
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-2"))).toBe(
      false,
    );
  });

  it("keeps a card from a settled run out of the live turn's thought", () => {
    // A prior run's seq numbers index a different event space, so "ran during
    // this thought" is not a statement about them.
    renderTurn({
      toolCalls: [toolCall({ id: "call-1", runId: "run-0", sequenceNo: 2 })],
    });
    const block = screen.getByTestId("cs-thinking-block");
    expect(block.contains(screen.getByTestId("tc-chat-tool-call-1"))).toBe(
      false,
    );
  });
});

// CARDS STAY WITH THEIR TURN.
//
// Reported from the live app: ask a question, get an answer with a tool card;
// ask a second question, and the FIRST turn's card has moved down under the
// second one. By the third turn every card seen so far was piled on the newest
// message and every earlier turn was bare.
//
// The cause was that `sequenceNo` is an offset, not an address — each run
// numbers from 0, so run A's seq 3 and run B's seq 3 sorted as one moment. The
// transcript merged every card into a single seq order on that basis. Messages
// were always guarded (`message.run_id === activeRunId`); cards had no run
// identity to be guarded by.
describe("TcChat — a card belongs to the turn that produced it", () => {
  const twoTurns: readonly TcChatMessage[] = [
    {
      message_id: "u1",
      role: "user",
      parts: [{ type: "text", text: "are there any csv in the folder" }],
      run_id: "runA",
    },
    {
      message_id: "a1",
      role: "assistant",
      run_id: "runA",
      parts: [{ type: "text", text: "Yes, there are two CSV files", seq: 9 }],
    },
    {
      message_id: "u2",
      role: "user",
      parts: [{ type: "text", text: "what's in the csvs" }],
      run_id: "runB",
    },
    {
      message_id: "a2",
      role: "assistant",
      run_id: "runB",
      parts: [{ type: "text", text: "They contain benchmark rows", seq: 9 }],
    },
  ];

  // Colliding seqs ON PURPOSE: this is the real shape, and the bug is invisible
  // unless the two runs number their cards the same way — which they always do.
  const glob = toolCall({
    id: "glob",
    title: "Calling glob",
    status: "complete",
    sequenceNo: 3,
    runId: "runA",
  });
  const readFile = toolCall({
    id: "read_file",
    title: "Calling read_file",
    status: "complete",
    sequenceNo: 3,
    runId: "runB",
  });

  function order(): string[] {
    return [...screen.getByTestId("tc-chat-messages").querySelectorAll("li")]
      .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())
      .filter((text) => text.length > 0);
  }
  const indexOf = (needle: string): number =>
    order().findIndex((text) => text.includes(needle));

  it("keeps the settled turn's card above the next question", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="runB"
          messages={twoTurns}
          toolCalls={[glob, readFile]}
        />,
      ),
    );

    const answer1 = indexOf("Yes, there are two CSV files");
    const globCard = indexOf("Calling glob");
    const question2 = indexOf("what's in the csvs");
    const readCard = indexOf("Calling read_file");

    for (const [label, at] of [
      ["answer 1", answer1],
      ["glob card", globCard],
      ["question 2", question2],
      ["read_file card", readCard],
    ] as const) {
      expect({ label, rendered: at >= 0 }).toEqual({ label, rendered: true });
    }
    // Turn 1's card sits with turn 1 — ABOVE the second question, which is the
    // whole claim. Before the fix it rendered below it, next to turn 2's card.
    expect(globCard).toBeGreaterThan(answer1);
    expect(globCard).toBeLessThan(question2);
    // …and turn 2's card stays with turn 2.
    expect(readCard).toBeGreaterThan(question2);
  });

  it("does not pile a third turn's cards onto the newest message", () => {
    // The accumulation half of the report: by turn 3 every card seen so far was
    // under the last message. Each card must sit after its own question.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="runC"
          messages={[
            ...twoTurns,
            {
              message_id: "u3",
              role: "user",
              parts: [{ type: "text", text: "write a random csv" }],
              run_id: "runC",
            },
            {
              message_id: "a3",
              role: "assistant",
              run_id: "runC",
              parts: [{ type: "text", text: "Written", seq: 9 }],
            },
          ]}
          toolCalls={[
            glob,
            readFile,
            toolCall({
              id: "write_file",
              title: "Calling write_file",
              status: "complete",
              sequenceNo: 3,
              runId: "runC",
            }),
          ]}
        />,
      ),
    );

    expect(indexOf("Calling glob")).toBeLessThan(indexOf("what's in the csvs"));
    expect(indexOf("Calling read_file")).toBeLessThan(
      indexOf("write a random csv"),
    );
    expect(indexOf("Calling write_file")).toBeGreaterThan(
      indexOf("write a random csv"),
    );
  });

  it("treats a card with no run as the active run's, as before", () => {
    // Back-compat: everything upstream of this change emitted cards with no run
    // identity at all, and the transcript assumed a single run. An unlabelled
    // card must keep interleaving rather than vanish.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          activeRunId="runB"
          messages={twoTurns}
          toolCalls={[
            toolCall({
              id: "legacy",
              title: "Calling legacy",
              status: "complete",
              sequenceNo: 3,
              runId: null,
            }),
          ]}
        />,
      ),
    );
    expect(indexOf("Calling legacy")).toBeGreaterThan(
      indexOf("what's in the csvs"),
    );
  });
});
