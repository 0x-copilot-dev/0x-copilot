import type { RunId, SectionResult } from "@enterprise-search/api-types";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { RouterProvider } from "../../../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../../refs/registry";
import type { ArtifactRoute, Router } from "../../../routing/router";

import type { HomeRecentRun } from "../_home-stub";
import { RecentRuns } from "./RecentRuns";

afterEach(() => {
  __resetItemRefRegistryForTests();
});

const noopRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

// Fixed "now" so relative-time output is deterministic.
const NOW_MS = Date.parse("2026-05-18T12:00:00Z");

function wrap(node: ReactElement): ReactElement {
  return <RouterProvider router={noopRouter}>{node}</RouterProvider>;
}

function makeRun(overrides: Partial<HomeRecentRun> = {}): HomeRecentRun {
  return {
    run_id: "run_001" as RunId,
    title: "Draft renewal email",
    status: "succeeded",
    started_at: "2026-05-18T11:50:00Z",
    ...overrides,
  };
}

describe("<RecentRuns>", () => {
  it("renders one row per run when status='ok'", () => {
    // Register a resolver so <ItemLink> doesn't reject.
    registerItemRefResolver("run", async () => null);

    const recent: SectionResult<HomeRecentRun[]> = {
      status: "ok",
      data: [
        makeRun({ run_id: "run_001" as RunId, status: "succeeded" }),
        makeRun({
          run_id: "run_002" as RunId,
          title: "Sync calendar",
          status: "running",
        }),
        makeRun({
          run_id: "run_003" as RunId,
          title: "Cancelled task",
          status: "cancelled",
        }),
      ],
    };

    render(wrap(<RecentRuns recent={recent} now={NOW_MS} />));

    const section = screen.getByTestId("home-recent-runs");
    expect(section).toHaveAttribute("data-section-status", "ok");
    expect(screen.getAllByTestId("home-recent-run-row")).toHaveLength(3);

    // Status pill mapping is exhaustive (info / ok / error / muted).
    const pills = screen.getAllByTestId("status-pill");
    expect(pills.map((p) => p.getAttribute("data-status"))).toEqual([
      "ok", // succeeded
      "info", // running
      "muted", // cancelled
    ]);
  });

  it("renders the empty state when status='ok' and data is empty", () => {
    const recent: SectionResult<HomeRecentRun[]> = { status: "ok", data: [] };
    render(wrap(<RecentRuns recent={recent} />));
    const empty = screen.getByTestId("home-recent-runs-empty");
    expect(empty).toHaveAttribute("data-section-status", "ok");
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "No recent runs.",
    );
  });

  it("renders the error branch with the server-supplied message", () => {
    const recent: SectionResult<HomeRecentRun[]> = {
      status: "error",
      error: "upstream timeout",
    };
    render(wrap(<RecentRuns recent={recent} />));
    const err = screen.getByTestId("home-recent-runs-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(err).toHaveAttribute("data-section-status", "error");
    expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
      "upstream timeout",
    );
  });

  it("renders the unavailable branch with a fallback message when error is omitted", () => {
    const recent: SectionResult<HomeRecentRun[]> = { status: "unavailable" };
    render(wrap(<RecentRuns recent={recent} />));
    const u = screen.getByTestId("home-recent-runs-unavailable");
    expect(u).toHaveAttribute("data-section-status", "unavailable");
    expect(screen.getByTestId("empty-state-body")).toBeInTheDocument();
  });

  it("renders an <ItemLink> per row (cross-destination ref discipline)", () => {
    registerItemRefResolver("run", async () => null);
    const recent: SectionResult<HomeRecentRun[]> = {
      status: "ok",
      data: [makeRun()],
    };
    render(wrap(<RecentRuns recent={recent} now={NOW_MS} />));
    // While the resolver promise is in flight we render the skeleton; on
    // resolve-to-null we render the deleted chip. Either marker proves
    // <ItemLink> is in the tree.
    const present =
      screen.queryAllByTestId("item-link-skeleton").length +
      screen.queryAllByTestId("item-link-deleted").length;
    expect(present).toBeGreaterThan(0);
  });
});
