import { describe, expect, it, vi } from "vitest";
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

import type { FleetProjection } from "../subagents";
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
    total: 2,
    running: 2,
    done: 0,
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

  it("renders focus mode as Activity / Approvals tabs and hides the composer", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="focus" />),
    );
    expect(screen.getByTestId("tc-chat")).toHaveAttribute("data-mode", "focus");
    expect(screen.getByTestId("tc-chat-focus-tabs")).toBeInTheDocument();
    expect(screen.queryByTestId("composer")).not.toBeInTheDocument();
  });

  it("switches between Activity and Approvals tabs in focus mode", () => {
    const { transport } = makeTransport(() => Promise.resolve(SAMPLE_RESPONSE));
    render(
      withTransport(transport, <TcChat conversationId="c" mode="focus" />),
    );
    const approvals = screen.getByTestId("tc-chat-tab-approvals");
    expect(screen.getByTestId("tc-chat-focus-panel")).toHaveTextContent(
      /recent activity/i,
    );
    fireEvent.click(approvals);
    expect(approvals).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("tc-chat-focus-panel")).toHaveTextContent(
      /approvals/i,
    );
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

// PR-3.10 (FR-3.22) — in-chat approvals: the 4-zone ApprovalCard (Studio), the
// `.conf-card` confirmation variant (Focus), and the collapsed receipt on
// resolution.
function approval(overrides: Partial<TcChatApproval> = {}): TcChatApproval {
  return {
    approvalId: "appr-1",
    title: "Post to #launch-aurora",
    reason: "Copilot is asking before it writes outside this chat.",
    summary: "Posts the launch note to #launch-aurora",
    category: { vendor: "SLACK", access: "ACTION" },
    params: [{ label: "channel", value: "#launch-aurora" }],
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

  it("collapses a resolved approval to a receipt (approved / rejected)", () => {
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
    expect(
      screen.getByTestId("tc-chat-approval-receipt-appr-1"),
    ).toHaveAttribute("data-decision", "approved");
    // No pending card once resolved.
    expect(screen.queryByTestId("tc-chat-approval-appr-1")).toBeNull();

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
      screen.getByTestId("tc-chat-approval-receipt-appr-1"),
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
    expect(conf).toHaveTextContent("The agent paused here");
    expect(screen.getByTestId("tc-chat-conf-approve-appr-1")).toHaveTextContent(
      "Approve & sign",
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
