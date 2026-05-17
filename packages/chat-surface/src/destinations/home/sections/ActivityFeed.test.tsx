import type {
  ConversationId,
  RunId,
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
import type { HomeActivityRow } from "../_home-stub";

import { ActivityFeed } from "./ActivityFeed";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function makeRow(
  overrides: Partial<HomeActivityRow> & Pick<HomeActivityRow, "id" | "target">,
): HomeActivityRow {
  return {
    kind: "drafted_artifact",
    agent_id: "agt_001",
    agent_name: "Atlas",
    summary: "Atlas drafted a 4-page brief.",
    created_at: new Date(NOW - 10 * 60_000).toISOString(),
    tone: "neutral",
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

function registerRunResolver(): void {
  registerItemRefResolver("run", async (id) => ({
    label: `Run ${id}`,
    icon: null,
    route: { kind: "run", runId: id },
  }));
}

describe("<ActivityFeed>", () => {
  describe("status === 'ok'", () => {
    it("renders one ActivityList row per entry, using <ItemLink> for the ref", async () => {
      registerChatResolver();
      registerRunResolver();
      const rows: ReadonlyArray<HomeActivityRow> = [
        makeRow({
          id: "ev_1",
          target: { kind: "chat", id: "conv_001" as ConversationId },
        }),
        makeRow({
          id: "ev_2",
          target: { kind: "run", id: "run_001" as RunId },
        }),
      ];
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "ok",
        data: rows,
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} nowMs={NOW} />
        </RouterProvider>,
      );
      const section = screen.getByTestId("home-activity-feed");
      expect(section).toHaveAttribute("data-status", "ok");
      expect(screen.getByTestId("home-activity-count")).toHaveTextContent("2");
      expect(screen.getAllByTestId("activity-row")).toHaveLength(2);
      // Cross-refs go through the <ItemLink> registry — no
      // direct router.navigate from this section.
      await waitFor(() =>
        expect(screen.getAllByTestId("item-link").length).toBeGreaterThan(0),
      );
    });

    it("includes the agent name and summary in each row's context line", async () => {
      registerChatResolver();
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "ok",
        data: [
          makeRow({
            id: "ev_a",
            target: { kind: "chat", id: "conv_a" as ConversationId },
            agent_name: "Atlas",
            summary: "Drafted a brief.",
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} nowMs={NOW} />
        </RouterProvider>,
      );
      await waitFor(() =>
        expect(screen.getByTestId("activity-row-context")).toHaveTextContent(
          "Atlas — Drafted a brief.",
        ),
      );
    });

    it("uses each row's ISO timestamp as the <time dateTime>", async () => {
      registerChatResolver();
      const iso = new Date(NOW - 5 * 60_000).toISOString();
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "ok",
        data: [
          makeRow({
            id: "ev_t",
            target: { kind: "chat", id: "conv_t" as ConversationId },
            created_at: iso,
          }),
        ],
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} nowMs={NOW} />
        </RouterProvider>,
      );
      const time = screen.getByTestId("activity-row-timestamp");
      expect(time).toHaveAttribute("datetime", iso);
    });

    it("renders the §12.3 empty-state copy when data is an empty array", () => {
      // §12.3 home-prd: "Nothing's happened yet today. Atlas activity will
      // appear here." — distinct from the error state.
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "ok",
        data: [],
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} nowMs={NOW} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("home-activity-feed")).toHaveAttribute(
        "data-status",
        "empty",
      );
      expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
        "Nothing's happened yet today.",
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "Atlas activity will appear here.",
      );
    });

    it("treats a missing `data` field as zero rows (defensive)", () => {
      // The wire shape allows omitting `data` on non-ok branches; on
      // 'ok' it should be present, but we defend against partial-fixture
      // payloads so the empty fallback always fires.
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "ok",
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} nowMs={NOW} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
        "Nothing's happened yet today.",
      );
    });
  });

  describe("status === 'error' (§12.6 partial failure)", () => {
    it("renders an EmptyState with the backend error message + Retry CTA", () => {
      const onRetry = vi.fn();
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "error",
        error: "Upstream timeout — try again.",
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} onRetry={onRetry} />
        </RouterProvider>,
      );
      const section = screen.getByTestId("home-activity-feed");
      expect(section).toHaveAttribute("data-status", "error");
      expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
        "Couldn't load activity",
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "Upstream timeout — try again.",
      );
      fireEvent.click(screen.getByTestId("empty-state-action"));
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it("uses a generic body when the wire carries no error string", () => {
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "error",
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} onRetry={() => undefined} />
        </RouterProvider>,
      );
      expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
        "Other sections are unaffected. Try again in a moment.",
      );
    });

    it("omits the Retry CTA when no onRetry handler is provided", () => {
      // Section components are pure presentation — they don't invent
      // retry behavior. If the host doesn't pass a handler, the CTA is
      // absent (rather than rendering a button that does nothing).
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "error",
        error: "Boom.",
      };
      render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} />
        </RouterProvider>,
      );
      expect(screen.queryByTestId("empty-state-action")).toBeNull();
    });
  });

  describe("status === 'unavailable'", () => {
    it("renders nothing — the destination skips the section entirely", () => {
      // Cross-audit §1.1: 'unavailable' is the not-yet-shipped signal.
      // Home should render as though the section didn't exist (vs error
      // which shows a retry).
      const activity: SectionResult<ReadonlyArray<HomeActivityRow>> = {
        status: "unavailable",
      };
      const { container } = render(
        <RouterProvider router={noopRouter}>
          <ActivityFeed activity={activity} />
        </RouterProvider>,
      );
      expect(container.firstChild).toBeNull();
    });
  });
});
