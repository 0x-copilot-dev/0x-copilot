import type { ConversationId, InboxItemId, RunId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../refs/registry";
import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { InboxDetail, type InboxDetailItem } from "./InboxDetail";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

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

function makeRouter(): {
  router: Router<ArtifactRoute>;
  navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn();
  const router: Router<ArtifactRoute> = {
    current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
    navigate,
    subscribe: () => () => undefined,
  };
  return { router, navigate };
}

function harness(ui: ReactElement): ReactElement {
  return (
    <TransportProvider transport={makeTransport()}>
      <RouterProvider router={makeRouter().router}>{ui}</RouterProvider>
    </TransportProvider>
  );
}

function makeItem(overrides: Partial<InboxDetailItem> = {}): InboxDetailItem {
  return {
    id: "inbox_001" as InboxItemId,
    kind: "mention",
    subject: "Acme renewal needs a decision",
    sender: { kind: "user", label: "Alex" },
    recipientLabel: "You",
    receivedAt: "2026-05-17T14:00:00Z",
    status: "unread",
    priority: "high",
    labels: ["renewal"],
    threadId: "thread_001",
    ...overrides,
  };
}

describe("<InboxDetail>", () => {
  it("renders the subject, sender, recipient, and status chips", () => {
    render(
      harness(<InboxDetail item={makeItem()} bodyState={{ kind: "idle" }} />),
    );
    expect(screen.getByTestId("inbox-detail-subject")).toHaveTextContent(
      "Acme renewal needs a decision",
    );
    expect(screen.getByTestId("inbox-detail-sender")).toHaveTextContent("Alex");
    expect(screen.getByTestId("inbox-detail-recipient")).toHaveTextContent(
      "You",
    );
    // chip row carries kind/priority/status + each label
    const chips = screen.getAllByTestId("status-pill");
    expect(chips.length).toBeGreaterThanOrEqual(3);
  });

  it("renders cross-destination links through <ItemLink> (cross-audit §3.3)", async () => {
    registerItemRefResolver("run", async (id) => ({
      label: `Run ${id}`,
      icon: null,
      route: { kind: "run", runId: id } as ArtifactRoute,
      breadcrumb: "Runs",
    }));
    render(
      harness(
        <InboxDetail
          item={makeItem({
            links: [{ kind: "run", id: "run_xyz" as RunId }],
          })}
          bodyState={{ kind: "idle" }}
        />,
      ),
    );
    await waitFor(() => {
      expect(screen.getByTestId("item-link")).toBeInTheDocument();
    });
    expect(screen.getByTestId("inbox-detail-links")).toBeInTheDocument();
  });

  it("shows action buttons only when their callbacks are supplied", () => {
    const onMarkRead = vi.fn();
    render(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "idle" }}
          onMarkRead={onMarkRead}
        />,
      ),
    );
    expect(screen.getByTestId("inbox-detail-mark-read")).toBeInTheDocument();
    expect(screen.queryByTestId("inbox-detail-snooze")).toBeNull();
    expect(screen.queryByTestId("inbox-detail-dismiss")).toBeNull();
    fireEvent.click(screen.getByTestId("inbox-detail-mark-read"));
    expect(onMarkRead).toHaveBeenCalledWith("inbox_001");
  });

  it("toggles the snooze picker and pipes its ISO datetime to onSnooze", () => {
    const onSnooze = vi.fn();
    render(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "idle" }}
          onSnooze={onSnooze}
        />,
      ),
    );
    const snoozeButton = screen.getByTestId("inbox-detail-snooze");
    expect(screen.queryByTestId("inbox-snooze-picker")).toBeNull();
    fireEvent.click(snoozeButton);
    expect(screen.getByTestId("inbox-snooze-picker")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("inbox-snooze-preset-one_hour"));
    expect(onSnooze).toHaveBeenCalledTimes(1);
    expect(onSnooze.mock.calls[0][0]).toBe("inbox_001");
    const iso = onSnooze.mock.calls[0][1] as string;
    expect(Number.isNaN(new Date(iso).getTime())).toBe(false);
    // picker closes after a preset is chosen
    expect(screen.queryByTestId("inbox-snooze-picker")).toBeNull();
  });

  it("forwards reply submissions with the inbox id and routing", () => {
    const onReply = vi.fn();
    render(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "idle" }}
          onReply={onReply}
        />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "looks good" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onReply).toHaveBeenCalledWith("inbox_001", {
      text: "looks good",
      routedTo: "existing-thread",
    });
  });

  it("locks reply for connector-error items (cross-audit §9.3)", () => {
    const onReply = vi.fn();
    render(
      harness(
        <InboxDetail
          item={makeItem({ kind: "error", status: "unread" })}
          bodyState={{ kind: "idle" }}
          onReply={onReply}
        />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
    fireEvent.change(ta, { target: { value: "still typing" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onReply).not.toHaveBeenCalled();
    expect(screen.getByTestId("inbox-detail-reply-hint")).toBeInTheDocument();
  });

  it("surfaces body loading / error / ready states (lazy fetch via body_ref)", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      harness(
        <InboxDetail item={makeItem()} bodyState={{ kind: "loading" }} />,
      ),
    );
    expect(screen.getByTestId("inbox-detail-body")).toHaveAttribute(
      "data-body-state",
      "loading",
    );
    rerender(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "error", message: "timeout" }}
          onRetryBody={onRetry}
        />,
      ),
    );
    expect(screen.getByText(/timeout/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("inbox-detail-body-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "ready", body: "Hello, world." }}
        />,
      ),
    );
    expect(screen.getByTestId("inbox-detail-body")).toHaveTextContent(
      "Hello, world.",
    );
  });

  it("hides the back button until onBack is wired", () => {
    const { rerender } = render(
      harness(<InboxDetail item={makeItem()} bodyState={{ kind: "idle" }} />),
    );
    expect(screen.queryByTestId("inbox-detail-back")).toBeNull();
    const onBack = vi.fn();
    rerender(
      harness(
        <InboxDetail
          item={makeItem()}
          bodyState={{ kind: "idle" }}
          onBack={onBack}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("inbox-detail-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  // Type-only assertion — keep the ConversationId import live so the
  // narrowed ItemRef branch above stays self-checking.
  it("typechecks the conversation-ref branch", () => {
    const _ref: { kind: "chat"; id: ConversationId } = {
      kind: "chat",
      id: "conv_001" as ConversationId,
    };
    expect(_ref.kind).toBe("chat");
  });
});
