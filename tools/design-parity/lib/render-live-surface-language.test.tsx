/* design-parity · live SURFACE-LANGUAGE renders (vitest + jsdom)
 * =========================================================================
 * The LIVE side of the `docs/plan/surface-language/` parity check. Renders the
 * REAL `@0x-copilot/surface-renderers` archetypes to static HTML, wrapped with
 * the REAL `design-system/src/styles.css` + `chat-surface`'s
 * `thread-canvas/surface-language.css`, so the browser extractor reads the
 * shipping computed styles for:
 *
 *   PRD-01  board://    lanes, sticky lane header, card chrome, changed card
 *   PRD-02  no spec     the generic degradation target every archetype falls to
 *
 * Five states → five HTML files under `surfaces/surface-language/live/`:
 *
 *   board          BoardRenderer, spec + 7 issues in 4 lanes, nothing changed
 *   board-changed  same, plus the one trusted change signal (a SurfaceDiff
 *                  `changes` entry pointing at `issues.2`) so lane 1 / card 0
 *                  carries the attention mark — the same card the design marks
 *   board-capped   201 cards, so `board-card-cap` states the truncation
 *   no-spec        TableRenderer with NO spec — the common degradation target
 *   no-spec-board  BoardRenderer with NO spec — PRD-01's "still degrades" line
 *
 * WHY THE FIXTURE MIRRORS THE MOCK'S DATA. The design's `BOARD_COLS` (vendored
 * in `surfaces/surface-language/design/surface-specs.jsx`) has four lanes with
 * 2/2/1/2 cards and marks `LW-142` as changed. Reproducing that payload exactly
 * is what makes the two sides comparable element-for-element: same lane count,
 * same card index for the marked card, same copy in every title. A fixture with
 * different shape would turn every row of the report into fixture noise.
 *
 * THE HUE SCOPE IS REAL, NOT HARDCODED. `TcSurfaceMount` sets
 * `data-surface-hue={surfaceHueForUri(uri)}` on the wrapper it renders the
 * adapter inside; this harness calls the SAME function on the SAME URI, so if
 * `board://` ever stops resolving to `plum` the parity render moves with it
 * instead of silently keeping a stale colour.
 *
 * Run:    node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *           lib/render-live-surface-language.test.tsx
 * Or:     node tools/design-parity/lib/run-surface-language-parity.mjs   (does everything)
 * ========================================================================= */
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";

import { surfaceHueForUri } from "@0x-copilot/chat-surface";
import { BoardRenderer, TableRenderer } from "@0x-copilot/surface-renderers";

/** Comfortably over `BoardRenderer`'s `CARD_RENDER_CAP` (200), which the
 *  package barrel does not re-export — so the fixture states a size instead of
 *  importing the constant, and the test asserts the cap line actually rendered.
 *  If the cap ever climbs past this, the assertion fails loudly rather than
 *  silently measuring an un-truncated board. */
const OVER_CAP = 260;

const HERE = (path: string): string =>
  fileURLToPath(new URL(path, import.meta.url));
const REPO = (path: string): string => HERE(`../../../${path}`);
const LIVE = (path: string): string =>
  HERE(`../surfaces/surface-language/live/${path}`);

const BOARD_URI = "board://linear/cycle/14";
/** No scheme resolves for a spec-less generic view, and that is the point:
 *  `surfaceHueForUri` returns `none` → the hollow ring (PRD-02). */
const NO_SPEC_URI = "incident://pagerduty/4127";

// ── fixtures ───────────────────────────────────────────────────────────────

const BOARD_SPEC = {
  spec_version: 1,
  archetype: "board",
  source: { server: "linear", tool: "linear.cycle.read" },
  title_path: "cycle.name",
  subtitle_path: "cycle.team.name",
  items_path: "issues",
  group_by_path: "state",
  columns: [
    { label: "Title", path: "title" },
    { label: "Meta", path: "meta" },
  ],
  // The mock's board card carries "Open cycle in Linear". Declaring it here is
  // what makes the two link treatments comparable at all — the design puts it
  // in the header rail, live puts it in a row under the lanes.
  link: { label: "Open cycle in Linear", url_path: "cycle.url" },
} as const;

/** The mock's four lanes, in the mock's order, with the mock's copy. Lane order
 *  is first-appearance order in `items_path`, so the array order IS the lane
 *  order: Triage · In progress · In review · Done. */
const ISSUES = [
  {
    title: "Payout CSV drops the memo column",
    meta: "LW-208 · nova.eth",
    state: "Triage",
  },
  {
    title: "Safe nonce mismatch on retry",
    meta: "LW-211 · unassigned",
    state: "Triage",
  },
  {
    title: "Stage transfers from the contributor sheet",
    meta: "LW-142 · dev.tomo",
    state: "In progress",
  },
  {
    title: "Recap thread draft",
    meta: "LW-190 · 0xlune",
    state: "In progress",
  },
  {
    title: "Approval gate copy pass",
    meta: "LW-177 · rin.eth",
    state: "In review",
  },
  { title: "Event log export", meta: "LW-160 · kira.eth", state: "Done" },
  { title: "Cycle 14 retro notes", meta: "LW-151 · juno.eth", state: "Done" },
];

const BOARD_DATA = {
  cycle: {
    name: "Cycle 14 — Launch Week",
    team: { name: "Platform" },
    url: "https://linear.app/0xcopilot/cycle/14",
  },
  issues: ISSUES,
};

/** The one trusted change signal (BoardRenderer only marks cards named by a
 *  `SurfaceFieldChange` whose `field` indexes `items_path`). `issues.2` is
 *  LW-142 — lane index 1, card index 0 — which is exactly the card the design
 *  marks with `data-chg`. */
const BOARD_CHANGES = [
  { field: "issues.2.state", old: "In progress", new: "In review" },
];

/** One card over the cap, so `board-card-cap` renders and states the truncation
 *  (PRD-01 requirement 6). Lane 0 is the mock's first lane either way. */
const CAPPED_DATA = {
  cycle: {
    name: "Cycle 14 — Launch Week",
    team: { name: "Platform" },
    url: "https://linear.app/0xcopilot/cycle/14",
  },
  issues: Array.from({ length: OVER_CAP }, (_, index) => ({
    title: ISSUES[index % ISSUES.length].title,
    meta: `LW-${300 + index} · nova.eth`,
    state: ISSUES[index % ISSUES.length].state,
  })),
};

/** The mock's `GENERIC_PAYLOAD`, as a RAW tool payload rather than as display
 *  rows: the live generic list titleizes the keys and summarises non-primitives
 *  itself, so `service` must be a real 6-key object and `assignments` a real
 *  2-element array for the rendered labels/values to line up with the mock's
 *  "{ 6 fields }" / "2 items". */
const NO_SPEC_PAYLOAD = {
  incident_number: "4127",
  title: "Elevated 5xx on payouts-api",
  status: "acknowledged",
  urgency: "high",
  created_at: "2026-07-28T09:12:04Z",
  service: {
    id: "PSVC42",
    name: "payouts-api",
    summary: "Payouts API",
    type: "service_reference",
    self: "https://api.pagerduty.com/services/PSVC42",
    html_url: "https://example.pagerduty.com/services/PSVC42",
  },
  assignments: [
    { at: "2026-07-28T09:12:41Z", assignee: "nova.eth" },
    { at: "2026-07-28T09:31:02Z", assignee: "dev.tomo" },
  ],
  html_url: "https://example.pagerduty.com/incidents/4127",
};

// ── shell ──────────────────────────────────────────────────────────────────

/**
 * Wrap a renderer's static markup the way the app does.
 *
 * `data-surface-hue` is the ONE thing the wrapper must carry: it is the scope
 * `--surface-src` resolves in, and `TcSurfaceMount` is the app's only setter of
 * it. Without it the kicker dot renders on the fallback neutral and the whole
 * identity register measures as absent.
 *
 * The frame is pinned to 1040px — the same content width the design harness
 * pins — because the extraction context reports `innerWidth: 0` and every
 * percentage-derived width would otherwise collapse (SKILL.md, "Pin the layout
 * on BOTH sides").
 */
function shell(state: string, hue: string, inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark" data-density="comfortable">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · surface language · ${state} · LIVE</title>
    <link rel="icon" href="data:," />
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; min-height: 100%; background: var(--color-bg); }
      *, *::before, *::after { animation: none !important; transition: none !important; }
      #parity-frame { width: 1040px; height: 760px; display: flex; overflow: hidden; }
      #parity-frame > [data-surface-hue] { flex: 1; min-width: 0; }
    </style>
  </head>
  <body>
    <div id="parity-frame" data-state="${state}">
      <div data-testid="tc-surface-mount" data-surface-hue="${hue}">${inner}</div>
    </div>
  </body>
</html>
`;
}

function persist(state: string, uri: string, markup: string): void {
  writeFileSync(
    LIVE(`${state}.html`),
    shell(state, surfaceHueForUri(uri), markup),
  );
}

describe("live surface-language renders", () => {
  beforeAll(() => {
    mkdirSync(LIVE("fonts"), { recursive: true });
    // One sheet, in cascade order: tokens/base first, then the surface-language
    // chrome that reads them. BOTH are required — `surface-language.css` owns
    // `.sf-kicker__dot`, and a harness that loaded only `styles.css` would
    // measure a dot that does not exist and call it parity.
    writeFileSync(
      LIVE("styles.css"),
      [
        readFileSync(REPO("packages/design-system/src/styles.css"), "utf8"),
        readFileSync(
          REPO("packages/chat-surface/src/thread-canvas/surface-language.css"),
          "utf8",
        ),
      ].join("\n"),
    );
    for (const font of [
      "instrument-sans-latin-ext-italic.woff2",
      "instrument-sans-latin-ext.woff2",
      "instrument-sans-latin-italic.woff2",
      "instrument-sans-latin.woff2",
      "jetbrains-mono-latin-ext.woff2",
      "jetbrains-mono-latin.woff2",
      "space-grotesk-latin-ext.woff2",
      "space-grotesk-latin.woff2",
    ]) {
      copyFileSync(
        REPO(`packages/design-system/src/fonts/${font}`),
        LIVE(`fonts/${font}`),
      );
    }
  });

  it("renders board (lanes at rest)", () => {
    const markup = renderToStaticMarkup(
      BoardRenderer({ spec: BOARD_SPEC, data: BOARD_DATA }),
    );
    expect(markup).toContain('data-testid="board-lanes"');
    expect(markup).toContain('data-testid="board-lane-3-header"');
    expect(markup).not.toContain('data-changed="true"');
    persist("board", BOARD_URI, markup);
  });

  it("renders board-changed (the attention mark on LW-142)", () => {
    const markup = renderToStaticMarkup(
      BoardRenderer({
        spec: BOARD_SPEC,
        data: BOARD_DATA,
        changes: BOARD_CHANGES,
      }),
    );
    // Lane 1 / card 0 and nothing else — the mark must not spread.
    expect(markup).toContain('data-testid="board-lane-1-card-0-changed"');
    expect(markup.match(/data-changed="true"/g)).toHaveLength(1);
    persist("board-changed", BOARD_URI, markup);
  });

  it("renders board-capped (truncation is stated)", () => {
    const markup = renderToStaticMarkup(
      BoardRenderer({ spec: BOARD_SPEC, data: CAPPED_DATA }),
    );
    expect(markup).toContain('data-testid="board-card-cap"');
    persist("board-capped", BOARD_URI, markup);
  });

  it("renders no-spec (TableRenderer's degradation target)", () => {
    const markup = renderToStaticMarkup(TableRenderer(NO_SPEC_PAYLOAD));
    expect(markup).toContain('data-testid="surface-generic-fields"');
    expect(markup).toContain('data-spec="absent"');
    persist("no-spec", NO_SPEC_URI, markup);
  });

  it("renders no-spec-board (BoardRenderer still degrades)", () => {
    const markup = renderToStaticMarkup(BoardRenderer(NO_SPEC_PAYLOAD));
    expect(markup).toContain('data-testid="surface-generic-fields"');
    expect(markup).toContain('data-spec="absent"');
    persist("no-spec-board", NO_SPEC_URI, markup);
  });
});
