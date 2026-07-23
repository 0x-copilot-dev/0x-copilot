// ActivityDestination — presentational run-history feed (PR-4.5).
//
// Covers the PRD §8 unit matrix: day grouping via injected `now` (FR-4.14),
// status→tone map incl. running/stopped/needs_input (FR-4.15), every row →
// onOpenRun with { conversationId, runId } (FR-4.16, PRD-04 Seam C), retention
// link (FR-4.17), the exact lead copy (FR-4.17), and the 4-state machine
// (FR-4.2).

import type {
  ActivityRunRow,
  ConversationId,
  RunId,
} from "@0x-copilot/api-types";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  ActivityDestination,
  activityStatusTone,
  groupActivityByDay,
  ACTIVITY_LEAD_COPY,
  ACTIVITY_RETENTION_LINK_COPY,
} from "./ActivityDestination";
import { statusTone as runStatusTone } from "../../shell/statusTone";

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
    conversation_id: "conv_default" as ConversationId,
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
  it("maps status to tone via the shared SSOT (PRD-B design semantics)", () => {
    // done → success (jade), stopped → muted (off) — the design's schema, which
    // corrects the earlier done→grey / stopped→red inversion.
    expect(activityStatusTone("running")).toBe("ok");
    expect(activityStatusTone("done")).toBe("ok");
    expect(activityStatusTone("paused")).toBe("warning");
    expect(activityStatusTone("stopped")).toBe("muted");
    expect(activityStatusTone("needs_input")).toBe("info");
  });

  it("labels come from the shell SSOT and are lowercase (PRD-02 — one vocabulary)", () => {
    // The former per-destination label switch is gone; Activity reads
    // `runStatusTone(status).label`. needs_input resolves to the SSOT's
    // "needs you" (not the old divergent "Needs input").
    expect(runStatusTone("needs_input").label).toBe("needs you");
    expect(runStatusTone("running").label).toBe("running");
    expect(runStatusTone("stopped").label).toBe("stopped");
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

  it("renders ONE rowlist card per day (not per-row chips) — FR-G.2", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({ run_id: "r_today_a" as RunId, started_at: TODAY_ISO }),
          row({ run_id: "r_today_b" as RunId, started_at: TODAY_LATER_ISO }),
          row({ run_id: "r_yday" as RunId, started_at: YESTERDAY_ISO }),
        ],
      },
    });
    // Two days → exactly two rowlist cards.
    expect(screen.getAllByTestId("activity-day-rowlist")).toHaveLength(2);
  });

  it("drops the 22px PageHeader title — the lead opens the surface (FR-G.2)", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    expect(screen.queryByTestId("page-header")).toBeNull();
    expect(screen.queryByTestId("page-header-title")).toBeNull();
    expect(screen.getByTestId("activity-lead")).toBeInTheDocument();
  });
});

// ===========================================================================
// Rows — navigation + content (FR-4.15 / FR-4.16)
// ===========================================================================

describe("<ActivityDestination> — rows", () => {
  it("a running row is a button that fires onOpenRun with { conversationId, runId } (FR-4.16, Seam C)", async () => {
    const onOpenRun = vi.fn();
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({
            run_id: "run_live" as RunId,
            conversation_id: "conv_live" as ConversationId,
            status: "running",
            title: "Live sync",
          }),
        ],
      },
      onOpenRun,
    });
    const rowEl = screen.getByTestId("activity-row");
    // The shared Row renders a `role="button"` control (rich content, keyboard).
    expect(rowEl).toHaveAttribute("role", "button");
    expect(rowEl).toHaveAttribute("data-open", "run");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_live",
      runId: "run_live",
    });
  });

  // DoD 5 — a finished (status="done") row also activates, opening the Run
  // cockpit bound to its CONVERSATION (the row is the click target, PRD-04).
  it("activating a done row fires onOpenRun once with { conversationId, runId } (DoD 5)", async () => {
    const onOpenRun = vi.fn();
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({
            run_id: "run_done" as RunId,
            conversation_id: "conv_done" as ConversationId,
            status: "done",
            title: "Weekly treasury reconciliation",
          }),
        ],
      },
      onOpenRun,
    });
    const rowEl = screen.getByTestId("activity-row");
    expect(rowEl).toHaveAttribute("role", "button");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_done",
      runId: "run_done",
    });
  });

  it("a live row shows the brand mark (success); others show a clock (FR-G.2)", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({ run_id: "r_live" as RunId, status: "running", title: "Live" }),
          row({ run_id: "r_done" as RunId, status: "done", title: "Done" }),
        ],
      },
      onOpenRun: vi.fn(),
    });
    const rows = screen.getAllByTestId("activity-row");
    const liveRow = rows.find(
      (r) => r.getAttribute("data-status") === "running",
    )!;
    const doneRow = rows.find((r) => r.getAttribute("data-status") === "done")!;

    const liveIcon = within(liveRow).getByTestId("activity-row-icon");
    expect(liveIcon).toHaveAttribute("data-live", "true");
    // BrandMark renders a 400×400 turbine <svg>.
    expect(liveIcon.querySelector('svg[viewBox="0 0 400 400"]')).not.toBeNull();

    const doneIcon = within(doneRow).getByTestId("activity-row-icon");
    expect(doneIcon).toHaveAttribute("data-live", "false");
    // Icon glyphs are authored on a 24×24 viewBox.
    expect(doneIcon.querySelector('svg[viewBox="0 0 24 24"]')).not.toBeNull();
  });

  it("shows the dot on a LIVE chip only (FR-G.2)", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [
          row({ run_id: "r_live" as RunId, status: "running" }),
          row({ run_id: "r_done" as RunId, status: "done" }),
        ],
      },
      onOpenRun: vi.fn(),
    });
    const rows = screen.getAllByTestId("activity-row");
    const dotIn = (status: string): boolean => {
      const rowEl = rows.find((r) => r.getAttribute("data-status") === status)!;
      const pill = within(rowEl).getByTestId("status-pill");
      // The dot is the pill's only aria-hidden child span.
      return pill.querySelector('span[aria-hidden="true"]') !== null;
    };
    expect(dotIn("running")).toBe(true);
    expect(dotIn("done")).toBe(false);
  });

  // DoD 4 — every row renders its real title as plain text (no cross-destination
  // link); the titles appear in fixture order and NO `item-link` anchor renders.
  it("renders every row's real title as plain text, in order, with zero item-links (DoD 4)", () => {
    const titles = [
      "Weekly treasury reconciliation",
      "Draft investor update",
      "Rebalance LP positions",
      "Triage new GitHub issues",
      "Summarize Discord AMA",
      "Vendor invoice batch",
      "Competitor launch digest",
      "Launch Week ops",
    ];
    // One day bucket, most-recent first — build started_at descending so the
    // rendered order matches the fixture array order above.
    const base = new Date(2026, 6, 18, 11, 0, 0).getTime();
    renderActivity({
      items: {
        status: "ok",
        data: titles.map((title, i) =>
          row({
            run_id: `run_${i}` as RunId,
            conversation_id: `conv_${i}` as ConversationId,
            status: i === 7 ? "running" : "done",
            title,
            started_at: new Date(base - i * 60_000).toISOString(),
          }),
        ),
      },
      onOpenRun: vi.fn(),
    });
    expect(
      screen.getAllByTestId("activity-row-title").map((e) => e.textContent),
    ).toEqual(titles);
    expect(screen.queryAllByTestId("item-link")).toHaveLength(0);
    expect(screen.queryAllByTestId("item-link-static")).toHaveLength(0);
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
