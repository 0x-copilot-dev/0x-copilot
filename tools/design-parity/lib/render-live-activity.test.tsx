/* design-parity · live ACTIVITY destination render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `ActivityDestination` — the exact
 * component both hosts mount for the `activity` slug (web:
 * apps/frontend/src/features/activity/ActivityRoute.tsx; desktop:
 * apps/desktop/renderer/destinationBinders.tsx → ActivityBinder) — to static
 * HTML, wrapped with the REAL design-system styles.css, so the browser
 * extractor reads the shipping computed styles. This is the "live" side of the
 * activity parity diff; the "design" side is the vendored Claude Design
 * ActivitySurface (design-kit/app-v3/index.html?dest=activity&state=default).
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-activity.test.tsx
 * Output: surfaces/activity/live/default.html  (+ copied ds.css)
 *
 * WHY A CLIENT RENDER, NOT renderToStaticMarkup
 * ---------------------------------------------
 * Non-running Activity rows render their title through `<ItemLink kind="run">`,
 * which resolves its label in a `useEffect` (refs/ItemLink.tsx:97-121).
 * `renderToStaticMarkup` never runs effects, so every done/paused/stopped row
 * would serialize as the `item-link-skeleton` "loading…" chip — measuring the
 * skeleton instead of the real row title. So we render through
 * @testing-library into jsdom (effects DO run), await the resolved links, and
 * then serialize `outerHTML`. The markup written to disk is therefore the real
 * post-effect DOM of the shipping component, not a re-authored copy.
 *
 * STYLESHEETS
 * -----------
 * The Activity surface ships ZERO CSS-file rules: `.pg` / `.pg-lead` /
 * `.rowlist` / `.act-day` / `.sect-h` are class HOOKS only — every visual
 * property comes from inline `CSSProperties` in ActivityDestination.tsx and its
 * `destinations/_shared/{Row,RowList,PageLead}.tsx` primitives (verified: no
 * .css file under packages/ or apps/ defines any of those selectors). The only
 * stylesheet that matters is the design-system token sheet those inline styles
 * read (`var(--color-*)`, `var(--font-size-*)`, …), so that is the only sheet
 * linked here.
 * ========================================================================= */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { ActivityRunRow, RunId } from "@0x-copilot/api-types";
import {
  ActivityDestination,
  RouterProvider,
  Topbar,
  registerItemRefResolver,
  resolveItemRef,
  __resetItemRefRegistryForTests,
} from "@0x-copilot/chat-surface";

// ---------------------------------------------------------------------------
// ACT-06 probe seam — capture the registry state the PRODUCTION barrel leaves
// behind, at import time, BEFORE any test's beforeEach/afterEach touches it.
// Whatever this resolves to is exactly what both hosts get, because neither
// apps/frontend nor apps/desktop ever calls registerItemRefResolver("run", …).
// ---------------------------------------------------------------------------
const PRODUCTION_RUN_REF = resolveItemRef({
  kind: "run",
  id: "run_treasury_recon",
} as never);

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/activity/live/" + p);

// ---------------------------------------------------------------------------
// Fixture — mirrors the design's ACTIVITY fixture 1:1 in shape
// (design-kit/app-v3/copilot-data.jsx:149-164): THREE day groups of 3 / 3 / 2
// runs, same titles, same meta strings, same status mix
// (running, done, done | paused, done, done | stopped, done).
//
// The design fixture carries wall-clock strings ("11:44"); the live wire type
// carries an ISO `started_at` and the component derives BOTH the day grouping
// and the relative time from it, so the times below are the design's clock
// times rebuilt as local Date instances against a pinned `NOW`.
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
    title: "Launch Week ops",
    status: "running",
    meta: "4 apps · 7 steps · awaiting 1 approval",
    started_at: at(16, 11, 44),
  },
  {
    run_id: "run_treasury_recon" as RunId,
    title: "Weekly treasury reconciliation",
    status: "done",
    meta: "Sheets, Safe, Dune · 12 steps · balanced",
    started_at: at(16, 9, 2),
  },
  {
    run_id: "run_investor_update" as RunId,
    title: "Draft investor update",
    status: "done",
    meta: "Docs · 5 steps · saved to Local files",
    started_at: at(16, 8, 15),
  },
  // --- Yesterday (Jul 15) ---
  {
    run_id: "run_rebalance_lp" as RunId,
    title: "Rebalance LP positions",
    status: "paused",
    meta: "paused — needed your approval on a swap",
    started_at: at(15, 18, 30),
  },
  {
    run_id: "run_triage_github" as RunId,
    title: "Triage new GitHub issues",
    status: "done",
    meta: "GitHub · 9 steps · 3 labeled, 1 escalated",
    started_at: at(15, 14, 7),
  },
  {
    run_id: "run_discord_ama" as RunId,
    title: "Summarize Discord AMA",
    status: "done",
    meta: "Discord · 4 steps · posted recap",
    started_at: at(15, 11, 20),
  },
  // --- Mon, Jul 14 (the design's explicit-date divider) ---
  {
    run_id: "run_vendor_invoices" as RunId,
    title: "Vendor invoice batch",
    status: "stopped",
    meta: "stopped — you rejected 2 of 6 payouts",
    started_at: at(14, 16, 44),
  },
  {
    run_id: "run_competitor_digest" as RunId,
    title: "Competitor launch digest",
    status: "done",
    meta: "Web · 6 steps · saved 1 page",
    started_at: at(14, 10, 3),
  },
];

// ---------------------------------------------------------------------------
// Ports / context the surface needs
// ---------------------------------------------------------------------------

// ItemLink (non-running row titles) navigates through the Router port. The
// parity render never clicks, so navigate is inert.
const fakeRouter = {
  current: () => ({ kind: "run", runId: "run_launch_week_ops" }) as never,
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

/** Wrap the captured markup with the REAL design-system token sheet and a
 *  fixed dark frame approximating the destination viewport the shell gives
 *  Activity. Typography/color/border/padding are frame-independent; width and
 *  height are comparator noise. */
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
    // ds.css @font-face's the vendored JetBrains Mono at a path relative to
    // itself — copy the woff2s alongside so the mono day-dividers / times
    // measure with the REAL face instead of a fallback metric.
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

  beforeEach(() => {
    // The "run" resolver is HOST-owned (the Run destination registers it); the
    // parity harness stands in for the host with the same contract the real
    // resolver honours: label = the run's display title.
    registerItemRefResolver(
      "run",
      async (id) => ({
        label: ACTIVITY_ROWS.find((r) => r.run_id === id)?.title ?? `Run ${id}`,
        icon: null,
        route: { kind: "run", runId: id },
      }),
      { replace: true },
    );
  });

  afterEach(() => {
    __resetItemRefRegistryForTests();
    cleanup();
  });

  it("default — the day-grouped run feed", async () => {
    render(
      h(
        RouterProvider,
        { router: fakeRouter },
        h(
          "div",
          { style: { display: "flex", flexDirection: "column", flex: 1 } },
          // Shell chrome the design's activity page also shows (topbar title +
          // subtitle). Rendered exactly as ChatShell mounts it for this slug:
          // NEITHER host passes `topbarLeaf`, so `leaf` is null here on purpose.
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

    // Wait for every ItemLink to resolve — otherwise the done/paused/stopped
    // rows serialize as the "loading…" skeleton and the diff measures nothing.
    await waitFor(() => {
      expect(screen.queryAllByTestId("item-link-skeleton")).toHaveLength(0);
      expect(screen.getAllByTestId("item-link")).toHaveLength(7);
    });

    // Sanity: the real surface actually rendered its rows + groups.
    expect(screen.getAllByTestId("activity-day-group")).toHaveLength(3);
    expect(screen.getAllByTestId("activity-row")).toHaveLength(8);

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
// ACT-06 — per-run title, rendered with the PRODUCTION ItemRef registry
// ===========================================================================
//
// The parity render above stands in for the host with a resolver that returns
// the run's real title. This block asks the opposite question: what does the
// shipping app render, given the registry as the barrel actually leaves it?
//
// Grep-established facts this block turns into an executable assertion:
//   * the ONLY non-test registerItemRefResolver("run", …) in the repo is the
//     placeholder at packages/chat-surface/src/destinations/home/index.ts:54,
//     which returns the constant label "Run";
//   * apps/frontend/src/app/App.tsx registers todo / inbox_item / project /
//     library_* / agent (:229…:303) — never "run";
//   * apps/desktop contains no registerItemRefResolver call at all;
//   * packages/chat-surface/src/destinations/run/index.ts (the destination the
//     in-repo test comments credit with owning it) exports no resolver.
describe("ACT-06 · per-run title under the production registry", () => {
  afterEach(() => {
    cleanup();
  });

  it('the barrel\'s own "run" resolver returns the constant label "Run"', async () => {
    const resolved = await PRODUCTION_RUN_REF;
    expect(resolved).not.toBeNull();
    expect(resolved!.label).toBe("Run");
  });

  it('every non-running row therefore renders "Run" instead of its title', async () => {
    // Re-seed the registry with the EXACT placeholder body from
    // destinations/home/index.ts:54-61 (the earlier describe's afterEach
    // cleared the module-singleton map).
    registerItemRefResolver(
      "run",
      async (id) => ({
        label: "Run",
        icon: null,
        route: { kind: "run", runId: id as unknown as string },
        breadcrumb: "Runs",
      }),
      { replace: true },
    );

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

    await waitFor(() => {
      expect(screen.queryAllByTestId("item-link-skeleton")).toHaveLength(0);
      expect(screen.getAllByTestId("item-link")).toHaveLength(7);
    });

    // The 7 non-running rows all read "Run"; their projected titles are gone.
    const linkLabels = screen
      .getAllByTestId("item-link")
      .map((el) => el.textContent);
    expect(linkLabels).toEqual(Array.from({ length: 7 }, () => "Run"));

    for (const title of [
      "Weekly treasury reconciliation",
      "Draft investor update",
      "Rebalance LP positions",
      "Triage new GitHub issues",
      "Summarize Discord AMA",
      "Vendor invoice batch",
      "Competitor launch digest",
    ]) {
      expect(screen.queryByText(title)).toBeNull();
    }

    // Only the single running row keeps its title (rendered directly, not
    // through ItemLink — ActivityDestination.tsx:511-515).
    expect(screen.getByTestId("activity-row-title").textContent).toBe(
      "Launch Week ops",
    );
  });
});
