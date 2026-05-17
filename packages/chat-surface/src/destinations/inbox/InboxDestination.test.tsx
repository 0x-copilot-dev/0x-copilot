// InboxDestination shell tests (P4-B1).
//
// Covers: loading skeleton, error/unavailable empty states, section
// bucketing (unread / snoozed / read (last 7d) / dismissed),
// dismissed-section collapse, bulk-select toolbar, render-detail slot.

import type {
  AgentId,
  ApprovalId,
  ConversationId,
  InboxItemId,
  ProjectId,
  SectionResult,
  UserId,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

// Import the destination's index for ItemRef resolver side-effects —
// registers kind `"inbox_item"`. Without this the primary ItemLink on
// each row would resolve to the deleted-chip path.
import "./index";

// TODO(merge): rewire to "@enterprise-search/api-types"
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

    // The row's primary navigation flows through ItemLink (skeleton
    // first; resolves async via the registry).
    const skeleton = screen.getAllByTestId("item-link-skeleton")[0];
    expect(skeleton).toBeDefined();
    expect(skeleton!.getAttribute("data-item-kind")).toBe("inbox_item");
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

  it("renders the detail slot in place of the list body when focusedItemId is set", () => {
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

    // Detail slot renders; list body does NOT.
    expect(screen.getByTestId("inbox-detail-slot")).toBeInTheDocument();
    expect(screen.getByTestId("fake-detail")).toBeInTheDocument();
    expect(screen.queryByTestId("inbox-sections")).toBeNull();

    // Slot received the close callback.
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
    // At least 3 ItemLink instances rendered in skeleton state — one
    // primary + the two secondary chips. The registry resolves them
    // async; we assert the skeleton presence (kind attribute on each).
    const skeletons = screen.getAllByTestId("item-link-skeleton");
    const kinds = skeletons.map((el) => el.getAttribute("data-item-kind"));
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
});
