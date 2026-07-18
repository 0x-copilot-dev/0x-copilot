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
