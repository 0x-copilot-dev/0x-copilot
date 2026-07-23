import type { ConversationId, RunId } from "@0x-copilot/api-types";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RouterProvider } from "../providers/RouterProvider";
import {
  __resetItemRouteRegistryForTests,
  registerItemRoute,
} from "../refs/registry";
import type { ArtifactRoute, Router } from "../routing/router";

import { ActivityList, type ActivityRow } from "./ActivityList";

afterEach(() => {
  __resetItemRouteRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

describe("<ActivityList>", () => {
  it("renders one row per item with link + timestamp", async () => {
    registerItemRoute("chat", (id) => ({ kind: "chat", conversationId: id }));
    registerItemRoute("run", (id) => ({ kind: "run", runId: id }));
    const rows: ReadonlyArray<ActivityRow> = [
      {
        key: "1",
        ref: { kind: "chat", id: "conv_001" as ConversationId },
        timestamp: new Date(NOW - 5 * 60_000).toISOString(),
        context: "Acme renewal",
      },
      {
        key: "2",
        ref: { kind: "run", id: "run_001" as RunId },
        timestamp: new Date(NOW - 30 * 60_000).toISOString(),
      },
    ];
    render(
      <RouterProvider router={noopRouter}>
        <ActivityList rows={rows} now={NOW} ariaLabel="Recent activity" />
      </RouterProvider>,
    );
    expect(screen.getByTestId("activity-list")).toHaveAttribute(
      "aria-label",
      "Recent activity",
    );
    expect(screen.getAllByTestId("activity-row")).toHaveLength(2);
    expect(screen.getByTestId("activity-row-context")).toHaveTextContent(
      "Acme renewal",
    );
    const timestamps = screen.getAllByTestId("activity-row-timestamp");
    expect(timestamps).toHaveLength(2);
    // Routes registered → interactive anchors, rendered synchronously.
    expect(screen.getAllByTestId("item-link")).toHaveLength(2);
  });

  it("renders the time element with a dateTime attribute carrying the ISO string", () => {
    const iso = new Date(NOW - 60_000 * 60).toISOString();
    const rows: ReadonlyArray<ActivityRow> = [
      {
        key: "1",
        ref: { kind: "chat", id: "conv_001" as ConversationId },
        timestamp: iso,
      },
    ];
    render(
      <RouterProvider router={noopRouter}>
        <ActivityList rows={rows} now={NOW} />
      </RouterProvider>,
    );
    const time = screen.getByTestId("activity-row-timestamp");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", iso);
  });
});
