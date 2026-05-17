import type {
  ConversationId,
  SectionResult,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../../refs/registry";
import type { ArtifactRoute, Router } from "../../../routing/router";
import type { HomePinnedChat } from "../_home-stub";

import { PinnedChats } from "./PinnedChats";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function makePinned(
  overrides: Partial<HomePinnedChat> & Pick<HomePinnedChat, "conversation_id">,
): HomePinnedChat {
  return {
    title: "Renewal — Acme",
    last_message_at: new Date(NOW - 20 * 60_000).toISOString(),
    unread_message_count: 0,
    ...overrides,
  };
}

function registerChatResolver(): void {
  registerItemRefResolver("chat", async (id) => ({
    label: `Chat ${id}`,
    icon: null,
    route: { kind: "chat", conversationId: id },
  }));
}

describe("<PinnedChats>", () => {
  describe("status === 'ok'", () => {
    it("renders one row per pinned chat via <DocList> + <ItemLink>", async () => {
      registerChatResolver();
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "ok",
        data: [
          makePinned({ conversation_id: "conv_001" as ConversationId }),
          makePinned({
            conversation_id: "conv_002" as ConversationId,
            subtitle: "Aurora launch",
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} nowMs={NOW} />
        </RouterProvider>,
      );
      const section = screen.getByTestId("home-pinned-chats");
      expect(section).toHaveAttribute("data-status", "ok");
      // DocList primitive owns the <ul> chrome.
      expect(screen.getByTestId("doc-list")).toHaveAttribute(
        "data-mode",
        "slot",
      );
      expect(screen.getAllByTestId("home-pinned-row")).toHaveLength(2);
      // All cross-refs flow through <ItemLink>.
      await waitFor(() =>
        expect(screen.getAllByTestId("item-link").length).toBeGreaterThan(0),
      );
    });

    it("renders the optional subtitle when present and omits it when absent", async () => {
      registerChatResolver();
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "ok",
        data: [
          makePinned({
            conversation_id: "conv_with_subtitle" as ConversationId,
            subtitle: "Q1 launch",
          }),
          makePinned({
            conversation_id: "conv_without_subtitle" as ConversationId,
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} nowMs={NOW} />
        </RouterProvider>,
      );
      const subtitles = await screen.findAllByTestId("home-pinned-subtitle");
      expect(subtitles).toHaveLength(1);
      expect(subtitles[0]).toHaveTextContent("Q1 launch");
    });

    it("renders an unread badge only when unread_message_count > 0", async () => {
      registerChatResolver();
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "ok",
        data: [
          makePinned({
            conversation_id: "conv_unread" as ConversationId,
            unread_message_count: 5,
          }),
          makePinned({
            conversation_id: "conv_read" as ConversationId,
            unread_message_count: 0,
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} nowMs={NOW} />
        </RouterProvider>,
      );
      const badges = await screen.findAllByTestId("home-pinned-unread");
      expect(badges).toHaveLength(1);
      expect(badges[0]).toHaveTextContent("5");
      expect(badges[0]).toHaveAttribute("aria-label", "5 unread");
    });

    it("renders a <time dateTime> with the last_message_at ISO", async () => {
      registerChatResolver();
      const iso = new Date(NOW - 90 * 60_000).toISOString();
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "ok",
        data: [
          makePinned({
            conversation_id: "conv_t" as ConversationId,
            last_message_at: iso,
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} nowMs={NOW} />
        </RouterProvider>,
      );
      const time = await screen.findByTestId("home-pinned-timestamp");
      expect(time.tagName).toBe("TIME");
      expect(time).toHaveAttribute("datetime", iso);
    });

    it("renders the per-section empty-state copy when data is an empty array", () => {
      // Task acceptance copy: 'No pinned chats yet — pin a conversation
      // to see it here.' (Title + body split so the EmptyState primitive
      // can render the dashed-border panel.)
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "ok",
        data: [],
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} nowMs={NOW} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("home-pinned-chats")).toHaveAttribute(
        "data-status",
        "empty",
      );
      expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
        "No pinned chats yet",
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "Pin a conversation to see it here.",
      );
    });
  });

  describe("status === 'error' (§12.6 partial failure)", () => {
    it("renders an EmptyState with the wire error + Retry CTA", () => {
      const onRetry = vi.fn();
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "error",
        error: "DB connection lost.",
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} onRetry={onRetry} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("home-pinned-chats")).toHaveAttribute(
        "data-status",
        "error",
      );
      expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
        "Couldn't load pinned chats",
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "DB connection lost.",
      );
      fireEvent.click(screen.getByTestId("empty-state-action"));
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it("uses a generic body when the wire omits the error string", () => {
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "error",
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} onRetry={() => undefined} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "Other sections are unaffected. Try again in a moment.",
      );
    });

    it("omits the Retry CTA when no onRetry handler is provided", () => {
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "error",
        error: "Boom.",
      };
      render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} />
        </RouterProvider>,
      );
      expect(screen.queryByTestId("empty-state-action")).toBeNull();
    });
  });

  describe("status === 'unavailable'", () => {
    it("renders nothing — the destination skips the section entirely", () => {
      const pinned: SectionResult<ReadonlyArray<HomePinnedChat>> = {
        status: "unavailable",
      };
      const { container } = render(
        <RouterProvider router={noopRouter}>
          <PinnedChats pinned={pinned} />
        </RouterProvider>,
      );
      expect(container.firstChild).toBeNull();
    });
  });
});
