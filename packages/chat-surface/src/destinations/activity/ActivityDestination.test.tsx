// ActivityDestination — presentational run-history feed (PR-4.5 · PRD-08).
//
// Covers the PRD §8 unit matrix plus the PRD-08 regression guards: wall-clock
// time (D3), the trailing navigation slot (D4), the quiet day divider (D7), the
// four-state machine with header Retry + folded `unavailable` (D8), the icon
// tile without wrapper spans (D5), and the restored three-sentence lead (D10).
//
// NOTE on computed styles: several source values are design-system CSS-variable
// tokens (e.g. `--font-weight-regular`, `--font-size-mono-10`) that jsdom's
// getComputedStyle does NOT resolve — it returns the literal `var(...)`. So the
// jsdom assertions below pin token APPLICATION (via `.style`) plus the computed
// values jsdom DOES resolve (unset `text-transform`/`letter-spacing`, literal
// padding/width), AND resolve the token chain from the design-system SoT via
// `resolveDesignToken` to pin the numeric the browser computes (400/10px). The
// same numerics are independently confirmed in real Chromium by the
// design-parity harness (`day.head` / `row.live.name`, DoD 20).

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
import { resolveDesignToken } from "../_shared/resolveDesignToken.testutil";
import { statusTone as runStatusTone } from "../../shell/statusTone";

// --- Test seams -----------------------------------------------------------

// Local noon on Jul 18 2026. Rows below are built the same way so their local
// calendar day round-trips regardless of the test runner's timezone.
const NOW = new Date(2026, 6, 18, 12, 0, 0).getTime();

const TODAY_ISO = new Date(2026, 6, 18, 9, 0, 0).toISOString();
const TODAY_LATER_ISO = new Date(2026, 6, 18, 10, 30, 0).toISOString();
const YESTERDAY_ISO = new Date(2026, 6, 17, 15, 0, 0).toISOString();
const OLDER_ISO = new Date(2026, 6, 12, 10, 0, 0).toISOString();

// D7 — explicit dividers now read weekday + month + day, with the year appended
// ONLY when the row is a previous calendar year. Jul 12 2026 is the same year
// as NOW, so no year.
const OLDER_DATE_LABEL = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
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
  it("maps status to tone via the shared SSOT (design semantics; PRD-08 D2)", () => {
    // done → success (jade), stopped → muted (off), needs_input → warning (amber,
    // the design's fourth `paused` slot). `paused` is no longer a member of the
    // taxonomy (D2), so it is not tested here.
    expect(activityStatusTone("running")).toBe("ok");
    expect(activityStatusTone("done")).toBe("ok");
    expect(activityStatusTone("stopped")).toBe("muted");
    expect(activityStatusTone("needs_input")).toBe("warning");
  });

  it("labels come from the shell SSOT and are lowercase (one vocabulary)", () => {
    expect(runStatusTone("needs_input").label).toBe("needs you");
    expect(runStatusTone("running").label).toBe("running");
    expect(runStatusTone("stopped").label).toBe("stopped");
  });
});

// ===========================================================================
// 4-state machine (FR-4.2 · PRD-08 D8)
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

  it("error: renders role=alert (no in-body retry) + a header Retry (D8)", async () => {
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
    await userEvent.click(screen.getByTestId("activity-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  // DoD 18 — the deleted `unavailable` branch folds into `error`.
  it("unavailable folds into the error state with a Retry (DoD 18)", () => {
    renderActivity({ items: { status: "unavailable" }, onRetry: vi.fn() });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "error",
    );
    expect(screen.getByTestId("activity-error")).toBeInTheDocument();
    expect(screen.getByTestId("activity-retry")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-unavailable")).toBeNull();
  });

  it("empty: ok with zero rows renders the retuned empty copy (D8)", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "empty",
    );
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    // The old copy asserted "the agent hasn't run anything" — which the client
    // cannot know — and is gone.
    expect(screen.queryByText(/hasn't run anything/i)).toBeNull();
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
    expect(dividers[0]).toHaveClass("act-day");
  });

  // DoD 19 — Retry lives in the page header, so it renders in error, empty AND
  // ready (a stale-but-loaded list still needs a refresh).
  it("the header Retry renders in ready, error and empty (DoD 19)", () => {
    const onRetry = vi.fn();
    const { unmount } = render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={NOW}
          onRetry={onRetry}
          items={{ status: "ok", data: [row()] }}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("activity-destination")).toHaveAttribute(
      "data-state",
      "ready",
    );
    expect(screen.getByTestId("activity-retry")).toBeInTheDocument();
    unmount();

    renderActivity({ items: { status: "error" }, onRetry });
    expect(screen.getByTestId("activity-retry")).toBeInTheDocument();

    screen.getByTestId("activity-retry"); // present in error
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={NOW}
          onRetry={onRetry}
          items={{ status: "ok", data: [] }}
        />
      </RouterProvider>,
    );
    expect(screen.getAllByTestId("activity-retry").length).toBeGreaterThan(0);
  });

  it("no Retry control in the loading state", () => {
    renderActivity({ items: null, onRetry: vi.fn() });
    expect(screen.queryByTestId("activity-retry")).toBeNull();
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
    expect(screen.getAllByTestId("activity-day-rowlist")).toHaveLength(2);
  });
});

// ===========================================================================
// D7 — the quiet day divider (was wearing the section-header's clothes)
// ===========================================================================

describe("<ActivityDestination> — day divider (D7)", () => {
  it("renders an explicit date as 'Mon, Jul 14', class act-day (no sect-h), quiet type (DoD 8)", () => {
    // en-US so the design's US-ordered "Mon, Jul 14" is produced (en-GB yields
    // day-first "Mon 14 Jul"); Jul 14 2025 is a Monday and same-year as `now`,
    // so no year is appended.
    const rowDay = new Date(2025, 6, 14, 10, 0, 0);
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={new Date(2025, 6, 20, 12, 0, 0).getTime()}
          locale="en-US"
          items={{
            status: "ok",
            data: [row({ started_at: rowDay.toISOString() })],
          }}
        />
      </RouterProvider>,
    );
    const divider = screen.getByTestId("activity-day");
    expect(divider.textContent).toBe("Mon, Jul 14");
    // Stopped claiming to be a section header.
    expect(divider.className).toBe("act-day");
    expect(divider.className).not.toContain("sect-h");
    // Quiet: no uppercase, no tracking (the design sets neither).
    expect(getComputedStyle(divider).textTransform).toBe("none");
    expect(getComputedStyle(divider).letterSpacing).toBe("normal");
    // 10px mono + regular weight via the tokens. jsdom's getComputedStyle does
    // not substitute var(), so first assert the tokens are APPLIED…
    expect(divider.style.fontWeight).toBe("var(--font-weight-regular)");
    expect(divider.style.fontSize).toContain("font-size-mono-10");
    // …then resolve those tokens through the same design-system SoT the browser
    // reads (`--font-weight-regular: 400` styles.css:98; `--font-size-mono-10:
    // 0.625rem` == 10px styles.css:88) and pin the COMPUTED numbers the DoD
    // names — 400 / 10px — from `.act-day` (copilot.css:1683-1691). This fails
    // if either token is redefined off-value. Real-Chromium confirmation of the
    // same 400/10px is DoD 20 (`day.head`).
    expect(resolveDesignToken(divider.style.fontWeight)).toBe("400");
    expect(resolveDesignToken(divider.style.fontSize)).toBe("10px");
  });

  it("appends the year for a previous-calendar-year row, and omits it in-year (DoD 9)", () => {
    // Row one calendar year older than `now`.
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={new Date(2026, 6, 20, 12, 0, 0).getTime()}
          locale="en-US"
          items={{
            status: "ok",
            data: [
              row({
                started_at: new Date(2025, 6, 14, 10, 0, 0).toISOString(),
              }),
            ],
          }}
        />
      </RouterProvider>,
    );
    expect(screen.getByTestId("activity-day").textContent).toContain("2025");

    // Same calendar year → no year.
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={new Date(2026, 6, 20, 12, 0, 0).getTime()}
          locale="en-US"
          items={{
            status: "ok",
            data: [
              row({
                started_at: new Date(2026, 6, 13, 10, 0, 0).toISOString(),
              }),
            ],
          }}
        />
      </RouterProvider>,
    );
    const dividers = screen.getAllByTestId("activity-day");
    expect(dividers[dividers.length - 1]!.textContent).not.toContain("2026");
  });
});

// ===========================================================================
// Rows — navigation + content (FR-4.15 / FR-4.16 · PRD-08 D3/D4/D5)
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
    expect(rowEl).toHaveAttribute("role", "button");
    expect(rowEl).toHaveAttribute("data-open", "run");
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_live",
      runId: "run_live",
    });
  });

  it("activating a done row fires onOpenRun once with { conversationId, runId }", async () => {
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
    await userEvent.click(rowEl);
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_done",
      runId: "run_done",
    });
  });

  // DoD 24 — the icon glyph is a DIRECT child of `[data-testid="row-icon"]`, no
  // intervening `[data-testid="activity-row-icon"]` wrapper (D5 deleted it).
  it("a live row's brand mark, and a done row's clock, are direct children of the row-icon tile (DoD 24)", () => {
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

    // The old wrapper testid is gone everywhere.
    expect(screen.queryAllByTestId("activity-row-icon")).toHaveLength(0);

    const liveTile = within(liveRow).getByTestId("row-icon");
    const liveSvg = liveTile.querySelector("svg")!;
    expect(liveSvg.getAttribute("viewBox")).toBe("0 0 400 400"); // BrandMark
    expect(liveSvg.parentElement).toBe(liveTile); // direct child, no wrapper

    const doneTile = within(doneRow).getByTestId("row-icon");
    const doneSvg = doneTile.querySelector("svg")!;
    expect(doneSvg.getAttribute("viewBox")).toBe("0 0 24 24"); // clock glyph
    expect(doneSvg.parentElement).toBe(doneTile);
  });

  it("iconTone tints the live tile jade (success) and leaves others muted", () => {
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
    const tile = (status: string): HTMLElement =>
      within(
        rows.find((r) => r.getAttribute("data-status") === status)!,
      ).getByTestId("row-icon");
    expect(tile("running").style.color).toBe("var(--color-success)");
    expect(tile("done").style.color).toBe("var(--color-text-muted)");
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
      return pill.querySelector('span[aria-hidden="true"]') !== null;
    };
    expect(dotIn("running")).toBe(true);
    expect(dotIn("done")).toBe(false);
  });

  // DoD 7 — navigable rows get a trailing chevron; non-navigable rows an empty
  // but always-16px spacer, so the time column never rags.
  it("marks navigable rows with a trailing chevron and reserves a 16px spacer otherwise (DoD 7)", () => {
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
    const liveRow = rows.find(
      (r) => r.getAttribute("data-status") === "running",
    )!;
    const doneRow = rows.find((r) => r.getAttribute("data-status") === "done")!;

    const liveTrailing = within(liveRow).getByTestId("row-trailing");
    expect(liveTrailing.querySelector("svg")).not.toBeNull(); // chevron
    const doneTrailing = within(doneRow).getByTestId("row-trailing");
    expect(doneTrailing).toBeEmptyDOMElement(); // reserved spacer, no glyph

    expect(getComputedStyle(liveTrailing).width).toBe("16px");
    expect(getComputedStyle(doneTrailing).width).toBe("16px");
  });

  // DoD 6 — row time is a wall clock, NOT relative, inside the day-grouped feed.
  it("renders the row time as a wall clock (11:44), never 'ago' (DoD 6)", () => {
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={Date.parse("2026-07-22T12:00:00Z")}
          locale="en-GB"
          timeZone="UTC"
          items={{
            status: "ok",
            data: [row({ started_at: "2026-07-22T11:44:00Z" })],
          }}
        />
      </RouterProvider>,
    );
    const time = screen.getByTestId("activity-row-time");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-07-22T11:44:00Z");
    expect(time.textContent).toBe("11:44");
    expect(time.textContent).not.toMatch(/ago|just now/);
  });

  // DoD 4 — real titles as plain text, in order, zero item-links.
  it("renders every row's real title as plain text, in order, with zero item-links (DoD 4)", () => {
    const titles = [
      "Weekly treasury reconciliation",
      "Draft investor update",
      "Rebalance LP positions",
      "Launch Week ops",
    ];
    const base = new Date(2026, 6, 18, 11, 0, 0).getTime();
    renderActivity({
      items: {
        status: "ok",
        data: titles.map((title, i) =>
          row({
            run_id: `run_${i}` as RunId,
            conversation_id: `conv_${i}` as ConversationId,
            status: i === 3 ? "running" : "done",
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
  });

  // DoD 4 — a row with an EMPTY meta renders no sub-line (never "0 apps").
  it("renders NO row-sub node for a row whose meta is empty (DoD 4)", () => {
    renderActivity({
      items: {
        status: "ok",
        data: [row({ run_id: "r_nometa" as RunId, meta: "" })],
      },
      onOpenRun: vi.fn(),
    });
    expect(screen.queryByTestId("row-sub")).toBeNull();
    expect(screen.queryByTestId("activity-row-meta")).toBeNull();
  });

  it("each row shows title, meta, wall-clock time, and a status chip (FR-4.15)", () => {
    render(
      <RouterProvider router={testRouter}>
        <ActivityDestination
          now={Date.parse("2026-07-22T12:00:00Z")}
          locale="en-GB"
          timeZone="UTC"
          items={{
            status: "ok",
            data: [
              row({
                run_id: "run_r" as RunId,
                status: "running",
                title: "Draft reply",
                meta: "gmail · notion",
                started_at: "2026-07-22T09:02:00Z",
              }),
            ],
          }}
          onOpenRun={vi.fn()}
        />
      </RouterProvider>,
    );
    const rowEl = screen.getByTestId("activity-row");
    const scoped = within(rowEl);
    expect(scoped.getByTestId("activity-row-title")).toHaveTextContent(
      "Draft reply",
    );
    expect(scoped.getByTestId("activity-row-meta")).toHaveTextContent(
      "gmail · notion",
    );
    expect(scoped.getByTestId("activity-row-time").textContent).toBe("09:02");
    expect(scoped.getByTestId("status-pill")).toHaveAttribute(
      "data-status",
      "ok",
    );
  });
});

// ===========================================================================
// Header lead + retention link (FR-4.17 · PRD-08 D10)
// ===========================================================================

describe("<ActivityDestination> — lead + retention link", () => {
  it("restores the three-sentence lead incl. 'most recent first' (DoD 25)", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    const lead = screen.getByTestId("activity-lead");
    expect(lead).toHaveTextContent(ACTIVITY_LEAD_COPY);
    expect(lead).toHaveTextContent("most recent first");
  });

  it("links ONLY the phrase 'Settings → Privacy', not the whole sentence (DoD 25)", async () => {
    const onOpenRetentionSettings = vi.fn();
    renderActivity({
      items: { status: "ok", data: [] },
      onOpenRetentionSettings,
    });
    const link = screen.getByTestId("activity-retention-link");
    // The link text is EXACTLY the phrase (not "Retention, export, and delete…").
    expect(link.textContent).toBe(ACTIVITY_RETENTION_LINK_COPY);
    expect(ACTIVITY_RETENTION_LINK_COPY).toBe("Settings → Privacy");
    expect(link.textContent).not.toMatch(/Retention/);
    await userEvent.click(link);
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

// ===========================================================================
// Page shell padding (D9)
// ===========================================================================

describe("<ActivityDestination> — page shell (D9)", () => {
  it("the page container uses the design's .pg padding 20px 24px 40px (DoD 26)", () => {
    renderActivity({ items: { status: "ok", data: [] } });
    const pg = screen
      .getByTestId("activity-destination")
      .querySelector(".pg") as HTMLElement;
    expect(pg).not.toBeNull();
    expect(getComputedStyle(pg).padding).toBe("20px 24px 40px");
  });
});
