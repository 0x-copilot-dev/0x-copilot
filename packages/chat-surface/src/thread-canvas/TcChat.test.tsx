import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import { SwimlaneScrubProvider } from "./SwimlaneScrubContext";
import {
  TcChat,
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
  it("fetches messages from /v1/conversations/{id}/messages on mount", async () => {
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
      path: "/v1/conversations/conv-1/messages",
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
    expect(record.calls[1]?.path).toBe("/v1/conversations/conv-2/messages");
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
