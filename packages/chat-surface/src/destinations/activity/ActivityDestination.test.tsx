// ActivityDestination — presentational run-history feed (PR-4.5).
//
// Covers the PRD §8 unit matrix: day grouping via injected `now` (FR-4.14),
// status→tone map incl. running/stopped/needs_input (FR-4.15), running-row
// → onOpenRun + non-running-row → ItemLink (FR-4.16), retention link
// (FR-4.17), the exact lead copy (FR-4.17), and the 4-state machine (FR-4.2).

import type { ActivityRunRow, RunId } from "@0x-copilot/api-types";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import {
  __resetItemRefRegistryForTests,
  registerItemRefResolver,
} from "../../refs/registry";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  ActivityDestination,
  activityStatusLabel,
  activityStatusTone,
  groupActivityByDay,
  ACTIVITY_LEAD_COPY,
  ACTIVITY_RETENTION_LINK_COPY,
} from "./ActivityDestination";

// --- Test seams -----------------------------------------------------------

// Local noon on Jul 18 2026. Built with the local Date constructor so the
// Today/Yesterday derivation is TZ-robust: rows below are constructed the
// same way, so their local calendar day round-trips regardless of the test
// runner's timezone.
const NOW = new Date(2026, 6, 18, 12, 0, 0).getTime();

const TODAY_ISO = new Date(2026, 6, 18, 9, 0, 0).toISOString();
const TODAY_LATER_ISO = new Date(2026, 6, 18, 10, 30, 0).toISOString();
const YESTERDAY_ISO = new Date(2026, 6, 17, 15, 0, 0).toISOString();
const OLDER_ISO = new Date(2026, 6, 12, 10, 0, 0).toISOString();

const OLDER_DATE_LABEL = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
}).format(new Date(2026, 6, 12, 10, 0, 0));

function row(over: Partial<ActivityRunRow> = {}): ActivityRunRow {
  return {
    run_id: "run_default" as RunId,
    title: "Untitled run",
    status: "done",
    meta: "gmail · calendar",
    started_at: TODAY_ISO,
    ...over,
  };
}

const navigate = vi.fn();
const testRouter: Router<ArtifactRoute> = {
  current: () => ({ kind: "chat", conversationId: "x" }) as ArtifactRoute,
  navigate,
  subscribe: () => () => undefined,
};

function renderActivity(
  props: Parameters<typeof ActivityDestination>[0] = {},
): void {
  render(
    <RouterProvider router={testRouter}>
      <ActivityDestination now={NOW} {...props} />
    </RouterProvider>,
  );
}

beforeEach(() => {
  navigate.mockClear();
  // Non-running rows navigate through the "run" ItemLink resolver; the host
  // owns it (Run destination), so tests register a stand-in.
  registerItemRefResolver("run", async (id) => ({
    label: `Run ${id}`,
    icon: null,
    route: { kind: "run", runId: id },
  }));
});

afterEach(() => {
  __resetItemRefRegistryForTests();
});

// ===========================================================================
// Pure helpers
// ===========================================================================

describe("groupActivityByDay", () => {
  it("groups rows into most-recent-day-first buckets with Today/Yesterday/date labels", () => {
    const groups = groupActivityByDay(
      [
        row({ run_id: "r_old" as RunId, started_at: OLDER_ISO }),
        row({ run_id: "r_today" as RunId, started_at: TODAY_ISO }),
        row({ run_id: "r_yday" as RunId, started_at: YESTERDAY_ISO }),
      ],
      NOW,
    );

    expect(groups.map((g) => g.label)).toEqual([
      "Today",
      "Yesterday",
      OLDER_DATE_LABEL,
    ]);
  });

  it("sorts rows within a day most-recent first", () => {
    const groups = groupActivityByDay(
      [
        row({ run_id: "r_early" as RunId, started_at: TODAY_ISO }),
        row({ run_id: "r_late" as RunId, started_at: TODAY_LATER_ISO }),
      ],
      NOW,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]!.rows.map((r) => r.run_id)).toEqual(["r_late", "r_early"]);
  });

  it("buckets unparseable timestamps under an 'Earlier' group sorted last", () => {
    const groups = groupActivityByDay(
      [
        row({ run_id: "r_bad" as RunId, started_at: "not-a-date" }),
        row({ run_id: "r_today" as RunId, started_at: TODAY_ISO }),
      ],
      NOW,
    );

    expect(groups[0]!.label).toBe("Today");
    expect(groups[groups.length - 1]!.label).toBe("Earlier");
  });
});

describe("activity status → tone / label", () => {
  it("maps every status to a distinct tone (FR-4.15)", () => {
    expect(activityStatusTone("running")).toBe("ok");
    expect(activityStatusTone("done")).toBe("muted");
    expect(activityStatusTone("paused")).toBe("warning");
    expect(activityStatusTone("stopped")).toBe("error");
    expect(activityStatusTone("needs_input")).toBe("info");
  });

  it("labels needs_input with a space", () => {
    expect(activityStatusLabel("needs_input")).toBe("Needs input");
    expect(activityStatusLabel("running")).toBe("Running");
    expect(activityStatusLabel("stopped")).toBe("Stopped");
  });
});

// ===========================================================================
// 4-state machine (FR-4.2)
// ===========================================================================

describe("<ActivityDestination> — 4 states", () => {
  it("loading: null items render a skeleton with role=status (not a bare spinner)", () => {
    renderActivity({ items: null });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "loading",
    );
    const loading = screen.getByTestId("activity-loading");
    expect(loading).toHaveAttribute("role", "status");
    expect(
      screen.getAllByTestId("activity-skeleton-row").length,
    ).toBeGreaterThan(0);
  });

  it("error: renders role=alert + a working Retry action", async () => {
    const onRetry = vi.fn();
    renderActivity({
      items: { status: "error", error: "boom" },
      onRetry,
    });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByTestId("activity-error")).toHaveAttribute(
      "role",
      "alert",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("unavailable: renders the distinct 'not enabled' empty-state", () => {
    renderActivity({ items: { status: "unavailable" } });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    expect(screen.getByTestId("activity-unavailable")).toBeInTheDocument();
    expect(screen.getByText("Activity unavailable")).toBeInTheDocument();
  });

  it("empty: ok with zero rows renders 'No activity yet'", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "empty",
    );
    expect(screen.getByText("No activity yet")).toBeInTheDocument();
  });

  it("ready: ok with rows renders day-grouped dividers (FR-4.14)", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({ run_id: "r_today" as RunId, started_at: TODAY_ISO }),
          row({ run_id: "r_yday" as RunId, started_at: YESTERDAY_ISO }),
        ],
      },
    });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "ready",
    );
    const dividers = screen.getAllByTestId("activity-day");
    expect(dividers.map((d) => d.textContent)).toEqual(["Today", "Yesterday"]);
    // `.act-day` class carried for design-spec fidelity.
    expect(dividers[0]).toHaveClass("act-day");
  });
});

// ===========================================================================
// Rows — navigation + content (FR-4.15 / FR-4.16)
// ===========================================================================

describe("<ActivityDestination> — rows", () => {
  it("a running row is a button that fires onOpenRun with the run id (FR-4.16)", async () => {
    const onOpenRun = vi.fn();
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({
            run_id: "run_live" as RunId,
            status: "running",
            title: "Live sync",
          }),
        ],
      },
      onOpenRun,
    });
    const rowEl = screen.getByTestId("activity-row");
    expect(rowEl.tagName).toBe("BUTTON");
    expect(rowEl).toHaveAttribute("data-open", "run");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledWith("run_live");
  });

  it("a non-running row navigates through the run ItemLink resolver (FR-4.16)", async () => {
    renderActivity({
      items: {
        status: "ok",
        data: [row({ run_id: "run_done" as RunId, status: "done" })],
      },
    });
    const link = await screen.findByTestId("item-link");
    expect(link).toHaveAttribute("data-item-kind", "run");
    expect(link).toHaveAttribute("data-item-id", "run_done");
    await userEvent.click(link);
    expect(navigate).toHaveBeenCalledWith({ kind: "run", runId: "run_done" });
  });

  it("each row shows title, meta, mono time, and a status chip (FR-4.15)", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({
            run_id: "run_r" as RunId,
            status: "running",
            title: "Draft reply",
            meta: "gmail · notion",
          }),
        ],
      },
      onOpenRun: vi.fn(),
    });
    const rowEl = screen.getByTestId("activity-row");
    const scoped = within(rowEl);
    expect(scoped.getByTestId("activity-row-title")).toHaveTextContent(
      "Draft reply",
    );
    expect(scoped.getByTestId("activity-row-meta")).toHaveTextContent(
      "gmail · notion",
    );
    const time = scoped.getByTestId("activity-row-time");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", TODAY_ISO);
    // running → jade/success tone.
    expect(scoped.getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "ok",
    );
  });
});

// ===========================================================================
// Header lead + retention link (FR-4.17)
// ===========================================================================

describe("<ActivityDestination> — lead + retention link", () => {
  it("renders the exact lead copy", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    const lead = screen.getByTestId("activity-lead");
    expect(lead).toHaveTextContent(ACTIVITY_LEAD_COPY);
    expect(lead).toHaveTextContent(ACTIVITY_RETENTION_LINK_COPY);
  });

  it("the retention link invokes onOpenRetentionSettings (FR-4.17)", async () => {
    const onOpenRetentionSettings = vi.fn();
    renderActivity({
      items: { status: "ok", data: [] },
      onOpenRetentionSettings,
    });
    await userEvent.click(screen.getByTestId("activity-retention-link"));
    expect(onOpenRetentionSettings).toHaveBeenCalledTimes(1);
  });

  it("without a handler, the retention pointer renders as plain (non-interactive) copy", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    expect(screen.queryByTestId("activity-retention-link")).toBeNull();
    expect(screen.getByTestId("activity-retention-copy")).toHaveTextContent(
      ACTIVITY_RETENTION_LINK_COPY,
    );
  });
});
