/* design-parity · live ACTIVITY destination render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `ActivityDestination` — the exact
 * component both hosts mount for the `activity` slug — to static HTML, wrapped
 * with the REAL design-system styles.css, so the browser extractor reads the
 * shipping computed styles. This is the "live" side of the activity parity diff;
 * the "design" side is the vendored Claude Design ActivitySurface
 * (design-kit/app-v3/index.html?dest=activity&state=default).
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-activity.test.tsx
 * Output: surfaces/activity/live/default.html  (+ copied ds.css)
 *
 * PRD-04 — every row's title is now PLAIN TEXT (`data-testid="activity-row-title"`,
 * inside `<Row>`'s `[data-testid="row-title"]` slot). The old `<ItemLink kind="run">`
 * that resolved a run's title in a `useEffect` (and, under the production
 * registry, rendered the constant "Run") is gone: the row IS the click target.
 * So there is no async resolution to await — a plain client render into jsdom
 * serializes the real post-render DOM directly.
 * ========================================================================= */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  ActivityRunRow,
  ConversationId,
  RunId,
} from "@0x-copilot/api-types";
import {
  ActivityDestination,
  RouterProvider,
  Topbar,
  __resetItemRouteRegistryForTests,
} from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/activity/live/" + p);

// ---------------------------------------------------------------------------
// Fixture — mirrors the design's ACTIVITY fixture 1:1 in shape
// (design-kit/app-v3/copilot-data.jsx:149-164): THREE day groups of 3 / 3 / 2
// runs, same titles, same meta strings, same status mix.
// ---------------------------------------------------------------------------

// Local 12:30 on Thu 16 Jul 2026 — after the latest "Today" run (11:44) so the
// running row's relative time is a small, realistic value.
const NOW = new Date(2026, 6, 16, 12, 30, 0).getTime();

const at = (day: number, hh: number, mm: number): string =>
  new Date(2026, 6, day, hh, mm, 0).toISOString();

const ACTIVITY_ROWS: ReadonlyArray<ActivityRunRow> = [
  // --- Today (Jul 16) ---
  {
    run_id: "run_launch_week_ops" as RunId,
    conversation_id: "conv_launch_week_ops" as ConversationId,
    title: "Launch Week ops",
    status: "running",
    meta: "4 apps · 7 steps · awaiting 1 approval",
    started_at: at(16, 11, 44),
  },
  {
    run_id: "run_treasury_recon" as RunId,
    conversation_id: "conv_treasury_recon" as ConversationId,
    title: "Weekly treasury reconciliation",
    status: "done",
    meta: "Sheets, Safe, Dune · 12 steps · balanced",
    started_at: at(16, 9, 2),
  },
  {
    run_id: "run_investor_update" as RunId,
    conversation_id: "conv_investor_update" as ConversationId,
    title: "Draft investor update",
    status: "done",
    meta: "Docs · 5 steps · saved to Local files",
    started_at: at(16, 8, 15),
  },
  // --- Yesterday (Jul 15) ---
  {
    run_id: "run_rebalance_lp" as RunId,
    conversation_id: "conv_rebalance_lp" as ConversationId,
    title: "Rebalance LP positions",
    status: "paused",
    meta: "paused — needed your approval on a swap",
    started_at: at(15, 18, 30),
  },
  {
    run_id: "run_triage_github" as RunId,
    conversation_id: "conv_triage_github" as ConversationId,
    title: "Triage new GitHub issues",
    status: "done",
    meta: "GitHub · 9 steps · 3 labeled, 1 escalated",
    started_at: at(15, 14, 7),
  },
  {
    run_id: "run_discord_ama" as RunId,
    conversation_id: "conv_discord_ama" as ConversationId,
    title: "Summarize Discord AMA",
    status: "done",
    meta: "Discord · 4 steps · posted recap",
    started_at: at(15, 11, 20),
  },
  // --- Mon, Jul 14 (the design's explicit-date divider) ---
  {
    run_id: "run_vendor_invoices" as RunId,
    conversation_id: "conv_vendor_invoices" as ConversationId,
    title: "Vendor invoice batch",
    status: "stopped",
    meta: "stopped — you rejected 2 of 6 payouts",
    started_at: at(14, 16, 44),
  },
  {
    run_id: "run_competitor_digest" as RunId,
    conversation_id: "conv_competitor_digest" as ConversationId,
    title: "Competitor launch digest",
    status: "done",
    meta: "Web · 6 steps · saved 1 page",
    started_at: at(14, 10, 3),
  },
];

// The 7 titles the OLD code hid behind the constant "Run" (every non-running
// row). PRD-04's regression guard asserts they now render.
const PREVIOUSLY_HIDDEN_TITLES = [
  "Weekly treasury reconciliation",
  "Draft investor update",
  "Rebalance LP positions",
  "Triage new GitHub issues",
  "Summarize Discord AMA",
  "Vendor invoice batch",
  "Competitor launch digest",
] as const;

// ---------------------------------------------------------------------------
// Ports / context the surface needs. The parity render never activates a row.
// ---------------------------------------------------------------------------
const fakeRouter = {
  current: () => ({ kind: "run", runId: "run_launch_week_ops" }) as never,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · activity · LIVE</title>
    <link rel="stylesheet" href="./ds.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1180px; height: 820px; display: flex; flex-direction: column;
        background: var(--color-bg); color: var(--color-text);
        font-family: var(--font-sans); overflow: hidden;
      }
      #frame > [data-component="activity-destination"] { flex: 1; min-height: 0; }
    </style>
  </head>
  <body>
    <div id="frame">${inner}</div>
  </body>
</html>`;
}

describe("live activity — ActivityDestination → static HTML", () => {
  beforeAll(() => {
    mkdirSync(LIVE(""), { recursive: true });
    copyFileSync(REPO("packages/design-system/src/styles.css"), LIVE("ds.css"));
    mkdirSync(LIVE("fonts"), { recursive: true });
    for (const f of [
      "jetbrains-mono-latin.woff2",
      "jetbrains-mono-latin-ext.woff2",
    ]) {
      copyFileSync(
        REPO(`packages/design-system/src/fonts/${f}`),
        LIVE(`fonts/${f}`),
      );
    }
  });

  afterEach(() => {
    __resetItemRouteRegistryForTests();
    cleanup();
  });

  it("default — the day-grouped run feed (titles are plain text)", () => {
    render(
      h(
        RouterProvider,
        { router: fakeRouter },
        h(
          "div",
          { style: { display: "flex", flexDirection: "column", flex: 1 } },
          h(Topbar, { activeDestination: "activity", leaf: null }),
          h(ActivityDestination, {
            items: { status: "ok", data: ACTIVITY_ROWS },
            now: NOW,
            onOpenRun: () => undefined,
            onOpenRetentionSettings: () => undefined,
          }),
        ),
      ),
    );

    // PRD-04 — no ItemLink anywhere; every row's title is plain text.
    expect(screen.queryAllByTestId("item-link")).toHaveLength(0);
    expect(screen.getAllByTestId("activity-day-group")).toHaveLength(3);
    expect(screen.getAllByTestId("activity-row")).toHaveLength(8);
    expect(screen.getAllByTestId("activity-row-title")).toHaveLength(8);

    const topbar = document.querySelector('[data-component="topbar"]');
    const surface = document.querySelector(
      '[data-component="activity-destination"]',
    );
    expect(surface).not.toBeNull();

    writeFileSync(
      LIVE("default.html"),
      shell(`${topbar?.outerHTML ?? ""}${surface!.outerHTML}`),
    );
  });
});

// ===========================================================================
// ACT-06 — per-run title (INVERTED, PRD-04 DoD 8)
// ===========================================================================
//
// The old ACT-06 pinned the BUG: it re-seeded the placeholder "run" resolver
// (destinations/home/index.ts:54, `label: "Run"`) and asserted all 7
// non-running rows read "Run" while each real title `queryByText` → null. PRD-04
// deletes that resolver AND the ItemLink call site, so this block now asserts
// the opposite: with NO "run" route registered (the barrel registers none),
// the surface renders ZERO `item-link`s and EACH previously-hidden title is
// present.
describe("ACT-06 · per-run title renders (no ItemLink, no constant label)", () => {
  afterEach(() => {
    __resetItemRouteRegistryForTests();
    cleanup();
  });

  it("renders every real title as plain text and NO item-link, with no run route registered", () => {
    render(
      h(
        RouterProvider,
        { router: fakeRouter },
        h(ActivityDestination, {
          items: { status: "ok", data: ACTIVITY_ROWS },
          now: NOW,
          onOpenRun: () => undefined,
          onOpenRetentionSettings: () => undefined,
        }),
      ),
    );

    // No cross-destination link is rendered — the title is plain row text.
    const linkLabels = screen
      .queryAllByTestId("item-link")
      .map((el) => el.textContent);
    expect(linkLabels).toEqual([]);

    // Each of the 7 titles the old constant-"Run" resolver hid is now present.
    // (`getByText` throws if absent, so a truthy result IS the presence proof;
    // the design-parity harness loads no jest-dom `toBeInTheDocument` matcher.)
    for (const title of PREVIOUSLY_HIDDEN_TITLES) {
      expect(screen.getByText(title)).toBeTruthy();
    }

    // The running row keeps its title too (all 8 render as plain text now).
    expect(
      screen.getAllByTestId("activity-row-title").map((e) => e.textContent),
    ).toContain("Launch Week ops");
  });
});
