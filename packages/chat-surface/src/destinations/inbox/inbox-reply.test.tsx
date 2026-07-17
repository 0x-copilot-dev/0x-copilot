import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TransportProvider } from "../../providers/TransportProvider";

import { InboxReply } from "./inbox-reply";

function makeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({ tools: [], candidates: [] } as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
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
}

function wrap(children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={makeTransport()}>
      {children}
    </TransportProvider>
  );
}

describe("<InboxReply>", () => {
  it("seeds the composer placeholder with the sender label", () => {
    render(wrap(<InboxReply senderLabel="Alex" onReply={() => undefined} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta.placeholder).toBe("Reply to Alex…");
  });

  it("emits routedTo=existing-thread when a threadId is supplied", () => {
    const onReply = vi.fn();
    render(
      wrap(
        <InboxReply
          senderLabel="Alex"
          threadId="thread_001"
          onReply={onReply}
        />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "  thanks  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onReply).toHaveBeenCalledTimes(1);
    expect(onReply).toHaveBeenCalledWith({
      text: "thanks",
      routedTo: "existing-thread",
    });
    expect(screen.getByTestId("inbox-reply")).toHaveAttribute(
      "data-routed-to",
      "existing-thread",
    );
  });

  it("emits routedTo=new-thread when no threadId is supplied", () => {
    const onReply = vi.fn();
    render(wrap(<InboxReply senderLabel="Alex" onReply={onReply} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onReply).toHaveBeenCalledWith({
      text: "hello",
      routedTo: "new-thread",
    });
    expect(screen.getByTestId("inbox-reply")).toHaveAttribute(
      "data-routed-to",
      "new-thread",
    );
  });

  it("re-uses the shared chat-surface Composer (single source of truth)", () => {
    render(wrap(<InboxReply senderLabel="Alex" onReply={() => undefined} />));
    // The shared Composer renders the hint row + toolbar — pick markers that
    // would be missing if anybody forked it.
    expect(screen.getByTestId("composer")).toBeTruthy();
    expect(screen.getByTestId("composer-hint")).toBeTruthy();
    expect(screen.getByTestId("composer-send")).toBeTruthy();
  });

  it("disables submission when caller passes `disabled`", () => {
    const onReply = vi.fn();
    render(
      wrap(<InboxReply senderLabel="Alex" onReply={onReply} disabled={true} />),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onReply).not.toHaveBeenCalled();
  });

  it("treats missing `onReply` as disabled (cross-audit §9.3 lock-out)", () => {
    render(wrap(<InboxReply senderLabel="System" />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
  });
});
