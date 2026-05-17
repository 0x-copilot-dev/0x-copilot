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
} from "@enterprise-search/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import { Composer } from "./Composer";

function makeTransport(
  resolver: (req: TypedRequest) => Promise<unknown> = () =>
    Promise.resolve({ tools: [], candidates: [] }),
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> =>
      resolver(req) as Promise<TRes>,
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
}

function withTransport(transport: Transport, children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

describe("Composer", () => {
  it("renders an empty textarea and a disabled Send button by default", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta.value).toBe("");
    expect(screen.getByTestId("composer-send")).toBeDisabled();
  });

  it("enables Send once non-whitespace text is entered", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    expect(screen.getByTestId("composer-send")).not.toBeDisabled();
  });

  it("calls onSend with trimmed text and clears the field on Enter", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "  hello world  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello world");
    expect(ta.value).toBe("");
  });

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "line1" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send when text is only whitespace", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "   \n  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does nothing when disabled", () => {
    const onSend = vi.fn();
    render(
      withTransport(
        makeTransport(),
        <Composer onSend={onSend} disabled={true} />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
    expect(screen.getByTestId("composer-send")).toBeDisabled();
  });

  it("toggles the ToolPicker open and closed", async () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("composer-tools-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("tool-picker")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("composer-tools-toggle"));
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
  });

  it("toggles the ModelPicker open and closed", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.getByTestId("model-picker")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.queryByTestId("model-picker")).not.toBeInTheDocument();
  });

  it("updates the model toggle label when a model is selected", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    fireEvent.click(screen.getByTestId("model-picker-row-claude-haiku-4-5"));
    expect(screen.getByTestId("composer-model-toggle")).toHaveTextContent(
      "Haiku 4.5",
    );
  });

  it("opens the MentionPopover when '@' is typed at a word boundary", async () => {
    const transport = makeTransport(() =>
      Promise.resolve({
        candidates: [{ slug: "tim", label: "Tim", kind: "skill" }],
      }),
    );
    render(withTransport(transport, <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello @t" } });
    await waitFor(() => {
      expect(screen.getByTestId("mention-popover")).toBeInTheDocument();
    });
  });

  it("does not open the MentionPopover for '@' inside a word (e.g. email)", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "name@example" } });
    expect(screen.queryByTestId("mention-popover")).not.toBeInTheDocument();
  });

  it("inserts the selected mention as @{slug} and closes the popover", async () => {
    const transport = makeTransport(() =>
      Promise.resolve({
        candidates: [{ slug: "tim", label: "Tim", kind: "skill" }],
      }),
    );
    render(withTransport(transport, <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello @t" } });
    const row = await screen.findByTestId("mention-row-tim");
    fireEvent.click(row);
    expect(ta.value).toBe("hello @tim ");
    expect(screen.queryByTestId("mention-popover")).not.toBeInTheDocument();
  });

  it("closes pickers on Escape", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.getByTestId("model-picker")).toBeInTheDocument();
    const ta = screen.getByTestId("composer-textarea");
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(screen.queryByTestId("model-picker")).not.toBeInTheDocument();
  });
});
