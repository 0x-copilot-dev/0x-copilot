import { describe, expect, it, vi } from "vitest";
import { EMPTY_CONNECTOR_TRUST } from "../approvals";
import {
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

  it("interleaves the fleet card into the message stream by timestamp", async () => {
    // Messages sit at t0 = …000000 and t1 = …060000; the fleet dispatched at
    // …030000 must land between them.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          fleets={[fleet({ createdAtMs: 1716000030000 })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-fleet-fleet-1");
    const list = screen.getByTestId("tc-chat-messages");
    const ids = Array.from(list.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(ids).toEqual([
      "tc-chat-message-m1",
      "tc-chat-fleet-fleet-1",
      "tc-chat-message-m2",
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
    createdAtMs: 1716000030000,
    ...overrides,
  };
}

describe("TcChat — inline tool-call card (Workstream D)", () => {
  it("shows fact-bound inline results in Studio but not Focus", async () => {
    // Uses CSV facts, not sources: the inline SOURCES card was removed from the
    // transcript (sources live in the Sources rail now), so the CSV summary is
    // what remains of `InlineToolResultCard` and it still must be Studio-only.
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
    expect(
      await screen.findByTestId("tc-inline-csv-summary-card"),
    ).toBeInTheDocument();

    rerender(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" toolCalls={[csvCall]} />,
      ),
    );
    expect(
      screen.queryByTestId("tc-inline-csv-summary-card"),
    ).not.toBeInTheDocument();
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

  it("interleaves the tool card into the message stream by timestamp", async () => {
    // Messages sit at t0 = …000000 and t1 = …060000; the tool ran at …030000
    // and must land between them.
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          toolCalls={[toolCall({ createdAtMs: 1716000030000 })]}
        />,
      ),
    );
    await screen.findByTestId("tc-chat-tool-call-1");
    const list = screen.getByTestId("tc-chat-messages");
    const ids = Array.from(list.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-testid"),
    );
    expect(ids).toEqual([
      "tc-chat-message-m1",
      "tc-chat-tool-call-1",
      "tc-chat-message-m2",
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

// PR-3.10 (FR-3.22) — in-chat approvals: the 4-zone ApprovalCard (Studio), the
// `.conf-card` confirmation variant (Focus), and the collapsed receipt on
// resolution.
function approval(overrides: Partial<TcChatApproval> = {}): TcChatApproval {
  return {
    approvalId: "appr-1",
    title: "Post to #launch-aurora",
    reason: "Copilot is asking before it writes outside this chat.",
    summary: "Posts the launch note to #launch-aurora",
    approvalKind: "tool_action",
    serverId: null,
    category: { vendor: "SLACK", access: "ACTION" },
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

describe("TcChat approvals (PR-3.10 / FR-3.22)", () => {
  it("renders a pending approval as the 4-zone ApprovalCard in Studio", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="studio" approvals={[approval()]} />,
      ),
    );
    const card = screen.getByTestId("tc-chat-approval-appr-1");
    expect(card).toHaveTextContent("Post to #launch-aurora");
    expect(
      screen.getByTestId("tc-chat-approval-approve-appr-1"),
    ).toHaveTextContent("Approve");
    expect(
      screen.getByTestId("tc-chat-approval-reject-appr-1"),
    ).toHaveTextContent("Reject");
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
    fireEvent.click(screen.getByTestId("tc-chat-approval-approve-appr-1"));
    expect(onApprove).toHaveBeenCalledWith("appr-1");
    fireEvent.click(screen.getByTestId("tc-chat-approval-reject-appr-1"));
    expect(onReject).toHaveBeenCalledWith("appr-1");
  });

  it("keeps a resolved approval in the transcript, never pinned above the composer", async () => {
    // The original rule here was "a resolved approval is history, so drop it" —
    // right while approvals were PINNED (a "✓ Approved" line above the input
    // added nothing and pushed the conversation up), and wrong once they are
    // anchored in the transcript. Inline, dropping it would reflow the thread
    // mid-conversation and erase the record of who decided what, in place.
    //
    // The half that still holds is the half about pinning, so both are asserted.
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
    const receipt = await screen.findByTestId(
      "tc-chat-approval-receipt-appr-1",
    );
    expect(screen.getByTestId("tc-chat-messages").contains(receipt)).toBe(true);
    // Resolved ⇒ no live decision surface, and nothing pinned.
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
    expect(screen.queryByTestId("tc-chat-approvals-waiting")).toBeNull();

    rerender(
      withTransport(
        transport,
        <TcChat
          conversationId="c"
          mode="studio"
          approvals={[approval({ resolved: true, decision: "rejected" })]}
        />,
      ),
    );
    expect(
      await screen.findByTestId("tc-chat-approval-receipt-appr-1"),
    ).toHaveAttribute("data-decision", "rejected");
  });

  it("renders a pending approval as a `.conf-card` in Focus mode", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(
        transport,
        <TcChat conversationId="c" mode="focus" approvals={[approval()]} />,
      ),
    );
    const conf = screen.getByTestId("tc-chat-conf-card-appr-1");
    expect(conf).toHaveClass("conf-card");
    expect(conf).toHaveTextContent("Post to #launch-aurora");
    // The reassurance is ANNOUNCED, not painted. It is boilerplate identical on
    // every card, so it earns no pixels in the strip above the composer — but a
    // screen-reader user meeting their first approval still needs it, so it
    // survives as the card's accessible description. Asserted on the CARD, not
    // the `.conf-card` wrapper: the wrapper would inherit the hidden node's text
    // either way, so it cannot tell announced from displayed.
    expect(
      screen.getByTestId("tc-chat-conf-consent-appr-1"),
    ).toHaveAccessibleDescription(/The agent paused here/);
    // The design reserves "Approve & sign" for actions that actually reach a
    // wallet; it arrives via `presentation.approve_label` on those approvals.
    // A generic approval promises no signature.
    expect(screen.getByTestId("tc-chat-conf-approve-appr-1")).toHaveTextContent(
      "Approve",
    );
    // The Studio ApprovalCard is NOT used in Focus.
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();
  });

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
    expect(screen.queryByTestId("tc-chat-approvals")).toBeNull();
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
    category: { vendor: "Linear", access: "ACTION" },
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
    // …and NOT the Approve/Reject `/decision` card.
    expect(
      screen.queryByTestId("tc-chat-approval-approve-mcp_auth:run_1:linear"),
    ).toBeNull();
    expect(
      screen.queryByTestId("tc-chat-approval-reject-mcp_auth:run_1:linear"),
    ).toBeNull();
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
    // Not the Focus `.conf-card` Approve/Reject variant.
    expect(
      screen.queryByTestId("tc-chat-conf-card-mcp_auth:run_1:linear"),
    ).toBeNull();
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

  it("still routes a normal tool_action approval through Approve/Reject (no regression)", () => {
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
    // A tool_action approval keeps the Approve/Reject `/decision` card…
    fireEvent.click(screen.getByTestId("tc-chat-approval-approve-appr-1"));
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
    // The two strips are gone, in BOTH modes.
    expect(screen.queryByTestId("tc-chat-approvals")).toBeNull();
    expect(screen.queryByTestId("tc-chat-conf-cards")).toBeNull();
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
    expect(screen.getByTestId("tc-chat-approval-approve-appr-1")).toBeVisible();
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
            toolCall({ id: "call-1", createdAtMs: 1716000010000 }),
            toolCall({ id: "call-2", createdAtMs: 1716000020000 }),
            toolCall({ id: "call-3", createdAtMs: 1716000040000 }),
            toolCall({ id: "call-4", createdAtMs: 1716000050000 }),
          ]}
          approvals={[approval({ createdAtMs: 1716000030000 })]}
        />,
      ),
    );
    expect(screen.getAllByTestId("tool-run-group")).toHaveLength(2);
  });
});
