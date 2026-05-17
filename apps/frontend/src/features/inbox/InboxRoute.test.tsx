import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type { InboxItemId } from "@enterprise-search/api-types";

import type {
  InboxBodyRef,
  InboxItem,
  InboxStreamEnvelope,
  InboxUnreadCount,
  ListInboxResponse,
} from "../../api/_inbox-stub";

// Mock chat-surface BEFORE InboxRoute pulls it in — <InboxDestination />
// would otherwise need full Transport / Router provider scaffolding to
// mount; its own rendering is exercised in the chat-surface package
// test suite.
vi.mock("@enterprise-search/chat-surface", () => ({
  InboxDestination: () => <div data-testid="inbox-destination-stub">stub</div>,
}));

// Mock the host-side ports module so `usePort("badge"|"notification")`
// returns stubs we can assert against.
const badgeSetBadge = vi.fn();
const notify = vi.fn();
const isAvailable = vi.fn(() => false);
vi.mock("../../ports", () => ({
  usePort: (name: string) => {
    if (name === "badge") return { setBadge: badgeSetBadge };
    if (name === "notification")
      return { notify, isAvailable, requestPermission: vi.fn() };
    return {};
  },
}));

// Mock the inboxApi module so the tests don't have to drive the real
// fetch / SSE plumbing — that surface is covered in inboxApi.test.ts.
const inboxApiMocks = vi.hoisted(() => ({
  fetchInbox: vi.fn(),
  fetchUnreadCount: vi.fn(),
  streamInboxEvents: vi.fn(),
}));
vi.mock("../../api/inboxApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/inboxApi")>(
      "../../api/inboxApi",
    );
  return {
    ...actual,
    fetchInbox: inboxApiMocks.fetchInbox,
    fetchUnreadCount: inboxApiMocks.fetchUnreadCount,
    streamInboxEvents: inboxApiMocks.streamInboxEvents,
  };
});

// Imports below this line resolve through the mocks above.
import {
  InboxRoute,
  applyInboxEnvelope,
  computeUnreadCount,
} from "./InboxRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function item(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    id: "inbox_1" as InboxItemId,
    tenant_id: "tenant_1",
    recipient_user_id: "user_test",
    sender: { kind: "agent", agent_id: "agent_1", agent_name: "Atlas" },
    kind: "mention",
    subject: "Doc draft ready",
    preview: "Atlas drafted the Q3 brief and tagged you.",
    body_ref: "body_1" as InboxBodyRef,
    status: "unread",
    priority: "med",
    labels: [],
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function listResponse(items: ReadonlyArray<InboxItem>): ListInboxResponse {
  return { items, next_cursor: null };
}

function unreadResponse(unread = 0, high = 0): InboxUnreadCount {
  return {
    unread,
    high_priority_unread: high,
    as_of: "2026-05-18T09:00:00Z",
  };
}

function envelope(
  type: "item_created" | "item_updated" | "item_deleted",
  it: InboxItem,
  sequenceNo = 1,
): InboxStreamEnvelope {
  return {
    sequence_no: sequenceNo,
    event_type: type,
    item: it,
    emitted_at: "2026-05-18T09:00:00Z",
  };
}

// Captures the latest streamInboxEvents callback bundle so tests can
// synchronously deliver SSE events / errors without depending on the
// real Transport.
function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: InboxStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: InboxStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  inboxApiMocks.streamInboxEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: InboxStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

describe("applyInboxEnvelope", () => {
  it("prepends item_created", () => {
    const a = item({ id: "a" as InboxItemId });
    const b = item({ id: "b" as InboxItemId });
    const next = applyInboxEnvelope([a], envelope("item_created", b));
    expect(next.map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("treats item_created as update when id already exists (idempotency)", () => {
    const a = item({ id: "a" as InboxItemId, status: "unread" });
    const aRead = item({ id: "a" as InboxItemId, status: "read" });
    const next = applyInboxEnvelope([a], envelope("item_created", aRead));
    expect(next).toHaveLength(1);
    expect(next[0].status).toBe("read");
  });

  it("replaces an existing item on item_updated", () => {
    const a = item({ id: "a" as InboxItemId, status: "unread" });
    const aDone = item({ id: "a" as InboxItemId, status: "done" });
    const next = applyInboxEnvelope([a], envelope("item_updated", aDone));
    expect(next[0].status).toBe("done");
  });

  it("ignores item_updated for an unknown id", () => {
    const a = item({ id: "a" as InboxItemId });
    const b = item({ id: "b" as InboxItemId });
    const next = applyInboxEnvelope([a], envelope("item_updated", b));
    expect(next).toBe(/* same ref */ next);
    expect(next.map((i) => i.id)).toEqual(["a"]);
  });

  it("drops an item on item_deleted", () => {
    const a = item({ id: "a" as InboxItemId });
    const b = item({ id: "b" as InboxItemId });
    const next = applyInboxEnvelope([a, b], envelope("item_deleted", b));
    expect(next.map((i) => i.id)).toEqual(["a"]);
  });
});

describe("computeUnreadCount", () => {
  it("counts only `unread` status rows", () => {
    expect(
      computeUnreadCount([
        item({ id: "a" as InboxItemId, status: "unread" }),
        item({ id: "b" as InboxItemId, status: "read" }),
        item({ id: "c" as InboxItemId, status: "done" }),
        item({ id: "d" as InboxItemId, status: "unread" }),
      ]),
    ).toBe(2);
  });
});

describe("InboxRoute", () => {
  beforeEach(() => {
    inboxApiMocks.fetchInbox.mockReset();
    inboxApiMocks.fetchUnreadCount.mockReset();
    inboxApiMocks.streamInboxEvents.mockReset();
    badgeSetBadge.mockReset();
    notify.mockReset();
    isAvailable.mockReset();
    isAvailable.mockReturnValue(false);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the destination after the inbox payload loads", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([item()]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(1));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    expect(screen.getByTestId("inbox-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(screen.getByTestId("inbox-destination-stub")).toBeInTheDocument();
  });

  it("pushes the unread count to BadgePort on every list refresh", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([item()]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(3, 1));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    expect(badgeSetBadge).toHaveBeenCalledWith("inbox", 0); // initial loading push
    expect(badgeSetBadge).toHaveBeenLastCalledWith("inbox", 3);
    expect(screen.getByTestId("inbox-route")).toHaveAttribute(
      "data-unread-count",
      "3",
    );
  });

  it("opens the SSE stream only after the initial fetch resolves", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(inboxApiMocks.streamInboxEvents).toHaveBeenCalledTimes(1);
    const sseArgs = inboxApiMocks.streamInboxEvents.mock.calls[0][0];
    expect(sseArgs.identity).toEqual(IDENTITY);
  });

  it("does NOT open the SSE stream while in the error state", async () => {
    inboxApiMocks.fetchInbox.mockRejectedValueOnce(new Error("boom"));
    inboxApiMocks.fetchUnreadCount.mockRejectedValueOnce(new Error("boom"));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route-error")).toBeInTheDocument(),
    );
    expect(inboxApiMocks.streamInboxEvents).not.toHaveBeenCalled();
  });

  it("merges an SSE item_created envelope into the list + recomputes the badge", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    const capture = captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    act(() => {
      capture.lastCall().onOpen?.();
      capture
        .lastCall()
        .onEvent(envelope("item_created", item({ status: "unread" }), 1));
    });

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-unread-count",
        "1",
      ),
    );
    expect(badgeSetBadge).toHaveBeenLastCalledWith("inbox", 1);
  });

  it("fires NotificationPort.notify ONLY when isAvailable() AND priority=high on item_created", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    const capture = captureStreamCallbacks();
    isAvailable.mockReturnValue(true);

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    // Low priority — no notify.
    act(() => {
      capture
        .lastCall()
        .onEvent(envelope("item_created", item({ priority: "low" }), 1));
    });
    expect(notify).not.toHaveBeenCalled();

    // High priority — notify fires with sender name + subject (no body bytes).
    act(() => {
      capture.lastCall().onEvent(
        envelope(
          "item_created",
          item({
            id: "x" as InboxItemId,
            priority: "high",
            subject: "Approve the brief",
            sender: { kind: "agent", agent_id: "ag1", agent_name: "Atlas" },
          }),
          2,
        ),
      );
    });
    expect(notify).toHaveBeenCalledTimes(1);
    const call = notify.mock.calls[0][0];
    expect(call.title).toBe("Atlas");
    expect(call.body).toBe("Approve the brief");
    expect(call.destination).toBe("inbox");
    expect(call.priority).toBe("high");
    expect(call.ref).toEqual({ kind: "inbox_item", id: "x" });
  });

  it("does NOT fire NotificationPort.notify when isAvailable() returns false", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    const capture = captureStreamCallbacks();
    isAvailable.mockReturnValue(false);

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    act(() => {
      capture
        .lastCall()
        .onEvent(envelope("item_created", item({ priority: "high" }), 1));
    });
    expect(notify).not.toHaveBeenCalled();
  });

  it("does NOT fire NotificationPort.notify on item_updated / item_deleted", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([item()]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(1));
    const capture = captureStreamCallbacks();
    isAvailable.mockReturnValue(true);

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );

    act(() => {
      capture
        .lastCall()
        .onEvent(envelope("item_updated", item({ priority: "high" }), 1));
      capture
        .lastCall()
        .onEvent(envelope("item_deleted", item({ priority: "high" }), 2));
    });
    expect(notify).not.toHaveBeenCalled();
  });

  it("falls back to polling /v1/inbox/unread_count every 60s when SSE not yet open", async () => {
    vi.useFakeTimers();
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // Initial unread_count fetch already happened (1 call) — polling
    // adds another call every 60s while SSE has not opened.
    const beforeTickCount = inboxApiMocks.fetchUnreadCount.mock.calls.length;
    expect(beforeTickCount).toBeGreaterThanOrEqual(1);

    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(inboxApiMocks.fetchUnreadCount.mock.calls.length).toBeGreaterThan(
      beforeTickCount,
    );
  });

  it("cancels the polling fallback once SSE opens", async () => {
    vi.useFakeTimers();
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    const capture = captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    act(() => {
      capture.lastCall().onOpen?.();
    });
    const stableCount = inboxApiMocks.fetchUnreadCount.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(120_000);
      await Promise.resolve();
    });
    // No new polls after SSE opened.
    expect(inboxApiMocks.fetchUnreadCount.mock.calls.length).toBe(stableCount);
  });

  it("reconnects with exponential backoff after an SSE error", async () => {
    vi.useFakeTimers();
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(inboxApiMocks.streamInboxEvents).toHaveBeenCalledTimes(1);
    const firstCallbacks = inboxApiMocks.streamInboxEvents.mock.calls[0][0] as {
      onError: (e: Event) => void;
    };
    act(() => firstCallbacks.onError(new Event("error")));
    expect(inboxApiMocks.streamInboxEvents).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(inboxApiMocks.streamInboxEvents).toHaveBeenCalledTimes(2);
  });

  it("closes the SSE handle on unmount", async () => {
    inboxApiMocks.fetchInbox.mockResolvedValue(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValue(unreadResponse(0));
    const close = vi.fn();
    captureStreamCallbacks(close);

    const view = render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(inboxApiMocks.streamInboxEvents).toHaveBeenCalledTimes(1),
    );

    view.unmount();
    expect(close).toHaveBeenCalled();
  });

  it("renders an error state with a working retry when the fetch fails", async () => {
    inboxApiMocks.fetchInbox.mockRejectedValueOnce(
      new Error("tenant lookup failed"),
    );
    inboxApiMocks.fetchUnreadCount.mockRejectedValueOnce(
      new Error("tenant lookup failed"),
    );
    inboxApiMocks.fetchInbox.mockResolvedValueOnce(listResponse([]));
    inboxApiMocks.fetchUnreadCount.mockResolvedValueOnce(unreadResponse(0));
    captureStreamCallbacks();

    render(<InboxRoute identity={IDENTITY} />);

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("inbox-route-error-message")).toHaveTextContent(
      /tenant lookup failed/,
    );

    fireEvent.click(screen.getByTestId("inbox-route-retry"));

    await waitFor(() =>
      expect(screen.getByTestId("inbox-route")).toHaveAttribute(
        "data-state",
        "ready",
      ),
    );
    expect(inboxApiMocks.fetchInbox).toHaveBeenCalledTimes(2);
  });
});
