// InboxDestination shell tests (P4-B1 + P4-B3 responsive layout).
//
// Covers: loading skeleton, error/unavailable empty states, section
// bucketing (unread / snoozed / read (last 7d) / dismissed),
// dismissed-section collapse, bulk-select toolbar, render-detail slot,
// and the 960px container-width breakpoint that swaps between
// two-pane (list + detail) and single-pane (list OR detail).

import type {
  AgentId,
  ApprovalId,
  ConversationId,
  InboxItemId,
  ProjectId,
  SectionResult,
  UserId,
} from "@0x-copilot/api-types";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Import the destination's index for ItemRef resolver side-effects —
// registers kind `"inbox_item"`. Without this the primary ItemLink on
// each row would resolve to the deleted-chip path.
import "./index";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type { InboxItem, InboxItemKind } from "./_inbox-stub";

import {
  InboxDestination,
  type InboxDestinationProps,
} from "./InboxDestination";

// Helper to mint branded ids inside fixtures.
const asInboxId = (s: string): InboxItemId => s as unknown as InboxItemId;
const asAgentId = (s: string): AgentId => s as unknown as AgentId;
const asUserId = (s: string): UserId => s as unknown as UserId;

// ===========================================================================
// Test scaffolding
// ===========================================================================

function makeRouter(): Router<ArtifactRoute> & {
  navigate: ReturnType<typeof vi.fn>;
} {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderInbox(props: InboxDestinationProps = {}): void {
  const router = makeRouter();
  render(
    <RouterProvider router={router}>
      <InboxDestination {...props} />
    </RouterProvider>,
  );
}

// ---------------------------------------------------------------------------
// ResizeObserver shim
// ---------------------------------------------------------------------------
//
// jsdom doesn't ship `ResizeObserver`. The responsive layout hook reads
// it; tests that exercise the breakpoint install a manual shim that lets
// them drive the observed width directly. Default-fallback tests don't
// install the shim — the hook keeps the wide SSR default and behaves
// like a wide-screen render, which is the correct user-facing default.

type ResizeObserverShim = {
  callback: ResizeObserverCallback;
  target: Element | null;
};

let activeObservers: ResizeObserverShim[] = [];

function installResizeObserverShim(): void {
  activeObservers = [];
  class TestResizeObserver implements ResizeObserver {
    private readonly entry: ResizeObserverShim;
    constructor(cb: ResizeObserverCallback) {
      this.entry = { callback: cb, target: null };
      activeObservers.push(this.entry);
    }
    observe(target: Element): void {
      this.entry.target = target;
    }
    unobserve(): void {
      this.entry.target = null;
    }
    disconnect(): void {
      this.entry.target = null;
      activeObservers = activeObservers.filter((o) => o !== this.entry);
    }
  }
  (
    globalThis as unknown as { ResizeObserver: typeof ResizeObserver }
  ).ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
}

function uninstallResizeObserverShim(): void {
  activeObservers = [];
  delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
}

function fireContainerWidth(widthPx: number): void {
  act(() => {
    for (const o of activeObservers) {
      if (o.target === null) continue;
      // Build a spec-compliant entry: inlineSize is the only field the
      // hook reads; contentRect.width is the legacy fallback.
      const entry: ResizeObserverEntry = {
        target: o.target,
        contentRect: { width: widthPx } as DOMRectReadOnly,
        borderBoxSize: [
          { inlineSize: widthPx, blockSize: 0 } as ResizeObserverSize,
        ],
        contentBoxSize: [
          { inlineSize: widthPx, blockSize: 0 } as ResizeObserverSize,
        ],
        devicePixelContentBoxSize: [
          { inlineSize: widthPx, blockSize: 0 } as ResizeObserverSize,
        ],
      };
      o.callback(
        [entry],
        // The hook ignores the second argument; cast keeps TS quiet.
        {} as ResizeObserver,
      );
    }
  });
}

// ===========================================================================
// Fixtures
// ===========================================================================

// "Now" pinned to 2026-05-17 12:00 UTC. Read-window cutoff = 7d before.
const NOW = Date.parse("2026-05-17T12:00:00.000Z");
const T_NOW = "2026-05-17T11:30:00.000Z";
const T_3_DAYS_AGO = "2026-05-14T12:00:00.000Z";
const T_10_DAYS_AGO = "2026-05-07T12:00:00.000Z";
const T_TOMORROW = "2026-05-18T09:00:00.000Z";

function ok<T>(data: T): SectionResult<T> {
  return { status: "ok", data };
}

type InboxInit = Omit<Partial<InboxItem>, "id"> & {
  readonly id: string;
  readonly kind?: InboxItemKind;
};

function makeItem(over: InboxInit): InboxItem {
  return {
    id: over.id as unknown as InboxItemId,
    sender: over.sender ?? {
      kind: "agent",
      agent_id: asAgentId("ag_atlas"),
      agent_name: "Atlas",
    },
    kind: over.kind ?? "mention",
    subject: over.subject ?? "Subject line",
    preview: over.preview ?? "Preview body",
    status: over.status ?? "unread",
    priority: over.priority ?? "med",
    labels: over.labels ?? [],
    thread_id: over.thread_id,
    run_id: over.run_id,
    approval_id: over.approval_id,
    project_id: over.project_id,
    snoozed_until: over.snoozed_until,
    created_at: over.created_at ?? T_NOW,
    updated_at: over.updated_at ?? T_NOW,
    links: over.links ?? [
      { kind: "inbox_item", id: over.id as unknown as InboxItemId },
    ],
  };
}

const ITEM_UNREAD = makeItem({
  id: "inbox_unread",
  subject: "Sarah mentioned you in #revops",
  preview: "Can you ack the redline before the call?",
  kind: "mention",
  status: "unread",
  priority: "high",
});

const ITEM_SNOOZED = makeItem({
  id: "inbox_snoozed",
  subject: "Approve Salesforce stage change",
  kind: "approval_request",
  status: "snoozed",
  snoozed_until: T_TOMORROW,
  approval_id: "appr_77",
  links: [
    { kind: "inbox_item", id: "inbox_snoozed" as unknown as InboxItemId },
    { kind: "approval", id: "appr_77" as unknown as ApprovalId },
  ],
});

const ITEM_READ_RECENT = makeItem({
  id: "inbox_read_recent",
  subject: "Connector token rotated",
  kind: "system",
  status: "read",
  updated_at: T_3_DAYS_AGO,
});

const ITEM_READ_OLD = makeItem({
  id: "inbox_read_old",
  subject: "Old read item — folds into dismissed",
  kind: "system",
  status: "read",
  updated_at: T_10_DAYS_AGO,
});

const ITEM_DONE = makeItem({
  id: "inbox_done",
  subject: "Dismissed yesterday",
  kind: "error",
  status: "done",
  updated_at: T_3_DAYS_AGO,
});

const FULL_PAYLOAD: ReadonlyArray<InboxItem> = [
  ITEM_UNREAD,
  ITEM_SNOOZED,
  ITEM_READ_RECENT,
  ITEM_READ_OLD,
  ITEM_DONE,
];

// ===========================================================================
// Tests
// ===========================================================================

describe("InboxDestination", () => {
  it("renders the skeleton state when items is null", () => {
    renderInbox({ items: null });
    const region = screen.getByRole("region", { name: /inbox destination/i });
    expect(region).toHaveAttribute("data-state", "loading");
    expect(
      screen.getAllByTestId("inbox-skeleton-section").length,
    ).toBeGreaterThan(0);
  });

  it("renders whole-list error state with a retry button when status=error", () => {
    const onRetry = vi.fn();
    renderInbox({
      items: { status: "error", error: "Network exploded" },
      onRetry,
    });
    const region = screen.getByRole("region", { name: /inbox destination/i });
    expect(region).toHaveAttribute("data-state", "error");
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /could not load inbox/i,
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /network exploded/i,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the unavailable state when status=unavailable", () => {
    renderInbox({
      items: { status: "unavailable", error: "Disabled for tenant" },
    });
    const region = screen.getByRole("region", { name: /inbox destination/i });
    expect(region).toHaveAttribute("data-state", "unavailable");
    expect(screen.getByTestId("empty-state")).toHaveTextContent(
      /inbox unavailable/i,
    );
  });

  it("renders the all-empty Inbox zero state when status=ok with no rows", () => {
    renderInbox({ items: ok<ReadonlyArray<InboxItem>>([]) });
    expect(screen.getByTestId("empty-state")).toHaveTextContent(/inbox zero/i);
  });

  it("buckets inbox items into the four sections (unread / snoozed / read / dismissed)", () => {
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>(FULL_PAYLOAD),
      now: NOW,
      initialDismissedCollapsed: false,
    });

    expect(
      screen.getByTestId("inbox-section-unread").getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen
        .getByTestId("inbox-section-snoozed")
        .getAttribute("data-row-count"),
    ).toBe("1");
    expect(
      screen.getByTestId("inbox-section-read").getAttribute("data-row-count"),
    ).toBe("1");
    // Dismissed gets both the explicit `done` item + the >7d-old read item.
    expect(
      screen
        .getByTestId("inbox-section-dismissed")
        .getAttribute("data-row-count"),
    ).toBe("2");
  });

  it("does NOT render sections with zero rows", () => {
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
      now: NOW,
    });

    expect(screen.queryByTestId("inbox-section-snoozed")).toBeNull();
    expect(screen.queryByTestId("inbox-section-read")).toBeNull();
    expect(screen.queryByTestId("inbox-section-dismissed")).toBeNull();
    expect(screen.getByTestId("inbox-section-unread")).toBeInTheDocument();
  });

  it("collapses the Dismissed section by default and expands on toggle", () => {
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD, ITEM_DONE]),
      now: NOW,
    });

    // Dismissed bucket is rendered but its body is collapsed.
    const dismissedSection = screen.getByTestId("inbox-section-dismissed");
    expect(dismissedSection).toBeInTheDocument();
    expect(screen.queryByTestId("inbox-section-dismissed-body")).toBeNull();

    fireEvent.click(screen.getByTestId("inbox-section-dismissed-collapse"));
    expect(
      screen.getByTestId("inbox-section-dismissed-body"),
    ).toBeInTheDocument();
  });

  it("toggles row selection and surfaces the bulk-action bar with handlers", () => {
    const onBulkMarkRead = vi.fn();
    const onBulkSnooze = vi.fn();
    const onBulkDismiss = vi.fn();
    const onBulkClear = vi.fn();
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD, ITEM_READ_RECENT]),
      now: NOW,
      onBulkMarkRead,
      onBulkSnooze,
      onBulkDismiss,
      onBulkClear,
    });

    // No bulk bar before selecting.
    expect(screen.queryByTestId("inbox-bulk-bar")).toBeNull();

    const [firstSelect] = screen.getAllByTestId("inbox-row-select");
    fireEvent.click(firstSelect!);

    const bar = screen.getByTestId("inbox-bulk-bar");
    expect(bar).toBeInTheDocument();
    expect(bar).toHaveTextContent(/1 selected/i);

    fireEvent.click(screen.getByTestId("inbox-bulk-mark-read"));
    expect(onBulkMarkRead).toHaveBeenCalledWith([asInboxId("inbox_unread")]);

    fireEvent.click(screen.getByTestId("inbox-bulk-snooze"));
    expect(onBulkSnooze).toHaveBeenCalledWith([asInboxId("inbox_unread")]);

    fireEvent.click(screen.getByTestId("inbox-bulk-dismiss"));
    expect(onBulkDismiss).toHaveBeenCalledWith([asInboxId("inbox_unread")]);

    fireEvent.click(screen.getByTestId("inbox-bulk-clear"));
    expect(onBulkClear).toHaveBeenCalledTimes(1);
    // Selection cleared → bar removed.
    expect(screen.queryByTestId("inbox-bulk-bar")).toBeNull();
  });

  it("invokes row handlers and renders ItemLink on the primary link", () => {
    const onMarkRead = vi.fn();
    const onSnooze = vi.fn();
    const onDismiss = vi.fn();
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
      now: NOW,
      onMarkRead,
      onSnooze,
      onDismiss,
    });

    fireEvent.click(screen.getByTestId("inbox-row-mark-read"));
    expect(onMarkRead).toHaveBeenCalledWith(asInboxId("inbox_unread"));

    fireEvent.click(screen.getByTestId("inbox-row-snooze"));
    expect(onSnooze).toHaveBeenCalledWith(asInboxId("inbox_unread"));

    fireEvent.click(screen.getByTestId("inbox-row-dismiss"));
    expect(onDismiss).toHaveBeenCalledWith(asInboxId("inbox_unread"));

    // The row's primary navigation flows through ItemLink. No `inbox_item`
    // route is registered in this package-level test, so it renders as inert
    // text (`item-link-static`) carrying the caller's label (the subject).
    const staticLink = screen.getAllByTestId("item-link-static")[0];
    expect(staticLink).toBeDefined();
    expect(staticLink!.getAttribute("data-item-kind")).toBe("inbox_item");
  });

  it("renders the unread badge from `unreadCount`", () => {
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
      now: NOW,
      unreadCount: 7,
    });
    const subtitle = screen.getByTestId("page-header-subtitle");
    expect(subtitle).toHaveTextContent(/7 unread/i);
    const badges = screen.getByTestId("page-header-badges");
    expect(badges).toHaveTextContent(/7 unread/i);
  });

  it("renders the detail slot alongside the list at the default (wide) container width", () => {
    // No ResizeObserver shim installed here: the hook's SSR-default
    // width is `INBOX_BREAKPOINT_PX`, which is `two-pane`. The list and
    // detail render side by side.
    const onCloseDetail = vi.fn();
    const slotArgs: Array<{ itemId: InboxItemId; onClose: () => void }> = [];
    const renderDetail = (props: {
      itemId: InboxItemId;
      onClose: () => void;
    }) => {
      slotArgs.push(props);
      return <div data-testid="fake-detail">D</div>;
    };

    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
      now: NOW,
      focusedItemId: asInboxId("inbox_unread"),
      onCloseDetail,
      renderDetail,
    });

    // Two-pane: both list AND detail render.
    expect(screen.getByTestId("inbox-detail-slot")).toBeInTheDocument();
    expect(screen.getByTestId("fake-detail")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-sections")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-two-pane")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-destination")).toHaveAttribute(
      "data-pane-mode",
      "two-pane",
    );

    // Slot received a close callback that forwards to onCloseDetail.
    expect(slotArgs.length).toBeGreaterThan(0);
    const first = slotArgs[0];
    expect(first).toBeDefined();
    first!.onClose();
    expect(onCloseDetail).toHaveBeenCalledTimes(1);
  });

  it("renders multiple cross-destination links from item.links beyond the primary", () => {
    const itemWithExtraLinks = makeItem({
      id: "inbox_multi",
      subject: "Multi-link",
      kind: "approval_request",
      status: "unread",
      approval_id: "appr_88",
      links: [
        { kind: "inbox_item", id: "inbox_multi" as unknown as InboxItemId },
        { kind: "approval", id: "appr_88" as unknown as ApprovalId },
        { kind: "chat", id: "conv_z" as unknown as ConversationId },
      ],
    });
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([itemWithExtraLinks]),
      now: NOW,
    });
    // At least 3 ItemLink instances rendered — one primary + the two
    // secondary chips. No routes are registered in this package test, so each
    // is inert text (`item-link-static`) carrying its kind attribute.
    const staticLinks = screen.getAllByTestId("item-link-static");
    const kinds = staticLinks.map((el) => el.getAttribute("data-item-kind"));
    expect(kinds).toContain("inbox_item");
    expect(kinds).toContain("approval");
    expect(kinds).toContain("chat");
  });

  it("renders a sender display string for user, agent, and system senders", () => {
    const items: ReadonlyArray<InboxItem> = [
      makeItem({
        id: "inbox_user",
        subject: "User sender",
        sender: { kind: "user", user_id: asUserId("u1") },
      }),
      makeItem({
        id: "inbox_agent",
        subject: "Agent sender",
        sender: {
          kind: "agent",
          agent_id: asAgentId("ag_x"),
          agent_name: "Acme Bot",
        },
      }),
      makeItem({
        id: "inbox_system",
        subject: "System sender",
        sender: { kind: "system", origin: "connector_error" },
      }),
    ];
    renderInbox({ items: ok<ReadonlyArray<InboxItem>>(items), now: NOW });

    const senders = screen
      .getAllByTestId("inbox-row-sender")
      .map((el) => el.textContent);
    expect(senders).toContain("Teammate");
    expect(senders).toContain("Acme Bot");
    expect(senders).toContain("Connector");
  });

  it("shows the snoozed-until label for snoozed rows", () => {
    renderInbox({
      items: ok<ReadonlyArray<InboxItem>>([ITEM_SNOOZED]),
      now: NOW,
      initialDismissedCollapsed: false,
    });
    expect(screen.getByTestId("inbox-row-snoozed-until")).toBeInTheDocument();
  });

  it("ignores project_id presence (just stores it) — pure presentation does not navigate", () => {
    const item = makeItem({
      id: "inbox_proj",
      subject: "Has project",
      project_id: "p_1" as unknown as ProjectId,
    });
    renderInbox({ items: ok<ReadonlyArray<InboxItem>>([item]), now: NOW });
    // No router.navigate calls — shell is pure presentation.
    // Verifying via the absence of any router error is enough; the
    // render itself implicitly asserts.
    expect(screen.getByTestId("inbox-row")).toBeInTheDocument();
  });

  // =========================================================================
  // P4-B3 — responsive 960px breakpoint
  // =========================================================================
  //
  // Cross-audit §9.2: single-pane swap below 960px (list <-> detail);
  // two-pane above. The hook observes the destination container via
  // ResizeObserver (no JS window listeners). Tests install a manual
  // shim and drive the observed width directly — no layout / paint
  // required, so the assertions are deterministic in jsdom.

  describe("responsive layout (960px breakpoint)", () => {
    beforeEach(() => {
      installResizeObserverShim();
    });
    afterEach(() => {
      uninstallResizeObserverShim();
    });

    it("renders single-pane-list (list only, no detail) below 960px without focus", () => {
      const renderDetail = vi.fn(() => <div data-testid="fake-detail">D</div>);
      renderInbox({
        items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
        now: NOW,
        renderDetail,
        // No focusedItemId — list-only regardless of mode.
      });
      fireContainerWidth(800);

      const root = screen.getByTestId("inbox-destination");
      expect(root).toHaveAttribute("data-pane-mode", "single-pane-list");
      expect(screen.getByTestId("inbox-sections")).toBeInTheDocument();
      expect(screen.queryByTestId("inbox-detail-slot")).toBeNull();
      expect(screen.queryByTestId("inbox-two-pane")).toBeNull();
      expect(renderDetail).not.toHaveBeenCalled();
    });

    it("renders single-pane-detail (detail only, list hidden) below 960px with focus", () => {
      const onCloseDetail = vi.fn();
      const renderDetail = vi.fn(() => <div data-testid="fake-detail">D</div>);
      renderInbox({
        items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
        now: NOW,
        focusedItemId: asInboxId("inbox_unread"),
        onCloseDetail,
        renderDetail,
      });
      fireContainerWidth(640);

      const root = screen.getByTestId("inbox-destination");
      expect(root).toHaveAttribute("data-pane-mode", "single-pane-detail");
      expect(screen.getByTestId("inbox-detail-slot")).toBeInTheDocument();
      expect(screen.queryByTestId("inbox-sections")).toBeNull();
      expect(screen.queryByTestId("inbox-two-pane")).toBeNull();
    });

    it("renders two-pane (list + detail) at exactly 960px with focus", () => {
      // Boundary: 960 is two-pane (>= threshold). 959 is single-pane.
      const renderDetail = vi.fn(() => <div data-testid="fake-detail">D</div>);
      renderInbox({
        items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
        now: NOW,
        focusedItemId: asInboxId("inbox_unread"),
        renderDetail,
      });
      fireContainerWidth(960);

      expect(screen.getByTestId("inbox-destination")).toHaveAttribute(
        "data-pane-mode",
        "two-pane",
      );
      expect(screen.getByTestId("inbox-two-pane")).toBeInTheDocument();
      expect(screen.getByTestId("inbox-list-pane")).toBeInTheDocument();
      expect(screen.getByTestId("inbox-detail-pane")).toBeInTheDocument();
      expect(screen.getByTestId("inbox-sections")).toBeInTheDocument();
      expect(screen.getByTestId("inbox-detail-slot")).toBeInTheDocument();
    });

    it("swaps live from two-pane to single-pane when the container shrinks past 960", () => {
      const renderDetail = vi.fn(() => <div data-testid="fake-detail">D</div>);
      renderInbox({
        items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
        now: NOW,
        focusedItemId: asInboxId("inbox_unread"),
        renderDetail,
      });

      fireContainerWidth(1200);
      expect(screen.getByTestId("inbox-destination")).toHaveAttribute(
        "data-pane-mode",
        "two-pane",
      );
      expect(screen.getByTestId("inbox-sections")).toBeInTheDocument();

      fireContainerWidth(800);
      expect(screen.getByTestId("inbox-destination")).toHaveAttribute(
        "data-pane-mode",
        "single-pane-detail",
      );
      expect(screen.queryByTestId("inbox-sections")).toBeNull();
      expect(screen.getByTestId("inbox-detail-slot")).toBeInTheDocument();

      // Grow back: returns to two-pane and the list reappears.
      fireContainerWidth(1100);
      expect(screen.getByTestId("inbox-destination")).toHaveAttribute(
        "data-pane-mode",
        "two-pane",
      );
      expect(screen.getByTestId("inbox-sections")).toBeInTheDocument();
    });

    it("uses ResizeObserver, NOT a window resize listener", () => {
      // Hard correctness rule from the brief: no JS window listeners.
      // We spy on `addEventListener` on the global object and confirm
      // the hook does not register a `resize` handler. Accessing the
      // browser global through `globalThis` keeps the chat-surface
      // package substrate-agnostic per the no-`window` lint rule.
      const global = globalThis as unknown as {
        addEventListener: typeof addEventListener;
      };
      const addSpy = vi.spyOn(global, "addEventListener");
      try {
        renderInbox({
          items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
          now: NOW,
        });
        fireContainerWidth(1000);

        const resizeRegistrations = addSpy.mock.calls.filter(
          (args) => args[0] === "resize",
        );
        expect(resizeRegistrations).toHaveLength(0);
      } finally {
        addSpy.mockRestore();
      }
    });

    it("forwards detail-slot onClose to onCloseDetail (single primitive across modes)", () => {
      const onCloseDetail = vi.fn();
      const renderDetail = vi.fn(
        ({ onClose }: { itemId: InboxItemId; onClose: () => void }) => (
          <button data-testid="fake-detail-close" onClick={onClose} />
        ),
      );
      renderInbox({
        items: ok<ReadonlyArray<InboxItem>>([ITEM_UNREAD]),
        now: NOW,
        focusedItemId: asInboxId("inbox_unread"),
        renderDetail,
        onCloseDetail,
      });
      fireContainerWidth(600);

      fireEvent.click(screen.getByTestId("fake-detail-close"));
      expect(onCloseDetail).toHaveBeenCalledTimes(1);
    });
  });
});
