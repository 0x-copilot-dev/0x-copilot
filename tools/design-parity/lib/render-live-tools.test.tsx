/* design-parity · live TOOLS (connectors / MCP) render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL shipping components of the Tools destination — rail slug
 * `connectors`, relabelled "Tools" (packages/chat-surface/src/shell/
 * destinations.ts) — to static HTML, wrapped with the REAL design-system
 * styles.css, so the browser extractor reads the shipping computed styles.
 *
 * States (keys match the design harness surfaces/tools/anchors.json):
 *
 *   default → what the WEB host mounts for the Tools destination:
 *             apps/frontend/src/features/connectors/ConnectorsRoute.tsx:531-601
 *             = <ConnectorsPanel> in a 240px <aside> + <ConnectorsDestination>.
 *             (The desktop host mounts ConnectorsDestination ALONE — no aside,
 *             no ConnectModal — apps/desktop/renderer/destinationBinders.tsx:
 *             479-492. The web composition is rendered here because it is the
 *             superset; the desktop delta is reported, not faked.)
 *
 *   connect → the same page with <ConnectModal open> on its first phase
 *             ("catalog"), the live analog of the design's ConnectModal phase
 *             "pick" (design-kit/app-v3/copilot-flows.jsx:186-268). Props
 *             mirror the web binder exactly, including `onAddCustomServer`
 *             (ConnectorsRoute.tsx:611-613) — which is what makes the live
 *             "Add a custom server" row (ConnectModal.tsx:347-370) the analog
 *             of the design's `.mrow--dash.mrow--pin` Custom-MCP-server row.
 *
 * Fixtures mirror the design fixtures 1:1 in shape: 6 connected rows with the
 * design's names/subs and the same permission mix (5 × read_act, 1 × read —
 * copilot-data.jsx:131-138), and a 6-entry catalog (copilot-data.jsx:139-146).
 *
 * NOTE — every component in this subtree styles with INLINE React styles; there
 * are no surface class names to hook (grep: no .css file under
 * packages/chat-surface/src/destinations/connectors/). Live selectors must be
 * `[data-testid=…]` / role-based. styles.css is still required: every value is
 * a `var(--color-*)` / `var(--font-size-*)` token that only resolves under it.
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *        lib/render-live-tools.test.tsx
 * Output: surfaces/tools/live/{default,connect}.html (+ styles.css + fonts/)
 * ========================================================================= */
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

import type {
  Connector,
  ConnectorCatalogEntry,
  ConnectorId,
  ConnectorSlug,
  SectionResult,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";
import {
  ConnectModal,
  ConnectorsDestination,
  ConnectorsPanel,
  type ConnectorsFilterCounts,
} from "@0x-copilot/chat-surface";

const HERE = (p: string): string => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string): string => HERE("../../../" + p); // lib -> repo root
const LIVE = (p: string): string => HERE("../surfaces/tools/live/" + p);

// --- Fixtures (mirror design-kit/app-v3/copilot-data.jsx:131-146) ----------

type ToolsItems = SectionResult<{
  readonly connectors: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<ConnectorCatalogEntry>;
}>;

/** Reference instant — freezes `formatRelativeTime` on the cards. */
const NOW = Date.parse("2026-07-22T12:00:00.000Z");

function connector(
  over: Partial<Connector> & Pick<Connector, "id" | "slug" | "display_name">,
): Connector {
  return {
    tenant_id: "tnt_parity" as TenantId,
    description: "",
    status: "connected",
    owner_user_id: "user_parity" as UserId,
    scopes: [],
    last_sync_at: "2026-07-22T11:30:00.000Z",
    created_at: "2026-06-02T09:00:00.000Z",
    updated_at: "2026-07-22T11:30:00.000Z",
    ...over,
  };
}

// Same 6 rows, same order, same sub-copy, same permission mix as the design.
const CONNECTORS: ReadonlyArray<Connector> = [
  connector({
    id: "conn_safe" as ConnectorId,
    slug: "safe" as ConnectorSlug,
    display_name: "Safe{Wallet}",
    description: "3-of-5 multisig · Base",
    access_mode: "read_act",
  }),
  connector({
    id: "conn_sheets" as ConnectorId,
    slug: "google_sheets" as ConnectorSlug,
    display_name: "Google Sheets",
    description: "Treasury workbook",
    access_mode: "read_act",
    last_sync_at: "2026-07-22T10:45:00.000Z",
  }),
  connector({
    id: "conn_x" as ConnectorId,
    slug: "x" as ConnectorSlug,
    display_name: "X",
    description: "@0xcopilot · post + read",
    access_mode: "read_act",
    last_sync_at: "2026-07-22T09:10:00.000Z",
  }),
  connector({
    id: "conn_discord" as ConnectorId,
    slug: "discord" as ConnectorSlug,
    display_name: "Discord",
    description: "Community server · 4 channels",
    access_mode: "read_act",
    last_sync_at: "2026-07-21T22:05:00.000Z",
  }),
  connector({
    id: "conn_fs" as ConnectorId,
    slug: "local_files" as ConnectorSlug,
    display_name: "Local files",
    description: "~/copilot/launch",
    access_mode: "read_act",
    last_sync_at: "2026-07-22T11:52:00.000Z",
  }),
  connector({
    id: "conn_github" as ConnectorId,
    slug: "github" as ConnectorSlug,
    display_name: "GitHub",
    description: "read-only · 3 repos",
    access_mode: "read",
    last_sync_at: "2026-07-21T14:20:00.000Z",
  }),
];

const CATALOG: ReadonlyArray<ConnectorCatalogEntry> = [
  {
    slug: "notion" as ConnectorSlug,
    display_name: "Notion",
    description: "Docs & databases",
  },
  {
    slug: "linear" as ConnectorSlug,
    display_name: "Linear",
    description: "Issues & projects",
  },
  {
    slug: "slack" as ConnectorSlug,
    display_name: "Slack",
    description: "Channels & DMs",
  },
  {
    slug: "google_calendar" as ConnectorSlug,
    display_name: "Google Calendar",
    description: "Events & scheduling",
  },
  {
    slug: "dune" as ConnectorSlug,
    display_name: "Dune",
    description: "On-chain analytics",
  },
  {
    slug: "stripe" as ConnectorSlug,
    display_name: "Stripe",
    description: "Payments & payouts",
  },
];

const ITEMS: ToolsItems = {
  status: "ok",
  data: { connectors: CONNECTORS, available: CATALOG },
};

const COUNTS: ConnectorsFilterCounts = {
  connected: CONNECTORS.length,
  available: CATALOG.length,
  custom: 0,
};

const noop = (): void => undefined;

/**
 * The web route frame (ConnectorsRoute.tsx:531-583): a full-height flex
 * `<section>` with a 240px filter aside and a scrolling main column.
 */
function RouteFrame(): React.ReactElement {
  return (
    <section
      aria-label="Connectors destination"
      data-testid="connectors-route"
      data-state="ready"
      data-item-count={CONNECTORS.length + CATALOG.length}
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        gap: 0,
        boxSizing: "border-box",
      }}
    >
      <aside
        data-testid="connectors-route-panel"
        style={{
          flex: "0 0 240px",
          borderRight: "1px solid var(--color-border)",
          overflow: "auto",
        }}
      >
        <ConnectorsPanel
          filter="connected"
          onFilterChange={noop}
          counts={COUNTS}
          onConnect={noop}
          onOpenWebhooks={noop}
        />
      </aside>
      <div
        data-testid="connectors-route-main"
        style={{ flex: "1 1 auto", overflow: "auto" }}
      >
        <ConnectorsDestination
          items={ITEMS}
          filter="connected"
          onFilterChange={noop}
          counts={COUNTS}
          onConnect={noop}
          onOpenConnector={noop}
          onOpenCatalogEntry={noop}
          onReconnect={noop}
          onSetAccessMode={noop}
          onOpenApprovalSettings={noop}
          onRetry={noop}
          now={NOW}
        />
      </div>
    </section>
  );
}

/** Wrap static markup in the real stylesheet + a design-sized content frame.
 *  1172x756 = the design window's content area (1220x840 minus the 48px rail,
 *  copilot.css:64, the 38px title bar and the 46px topbar, copilot.css:79). */
function shell(inner: string, state: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · tools · LIVE · ${state}</title>
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        position: relative;
        width: 1172px; height: 756px; display: flex; flex-direction: column;
        background: var(--color-bg, #131316); color: var(--color-text, #ededee);
        font-family: var(--font-sans); overflow: hidden;
      }
    </style>
  </head>
  <body>
    <div id="frame">${inner}</div>
  </body>
</html>`;
}

function copyAssets(): void {
  mkdirSync(LIVE("fonts"), { recursive: true });
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );
  // styles.css @font-face-s JetBrains Mono by relative path (styles.css:11-32);
  // copy the woff2 files so the mono subtitle/eyebrow measure the real face.
  for (const font of [
    "jetbrains-mono-latin.woff2",
    "jetbrains-mono-latin-ext.woff2",
  ]) {
    copyFileSync(
      REPO(`packages/design-system/src/fonts/${font}`),
      LIVE(`fonts/${font}`),
    );
  }
}

it("renders the live Tools destination (state: default)", () => {
  copyAssets();
  const html = renderToStaticMarkup(<RouteFrame />);
  expect(html).toContain('data-component="connectors-destination"');
  expect(html).toContain("Safe{Wallet}");
  expect(html).toContain('data-testid="access-mode-segment"');
  writeFileSync(LIVE("default.html"), shell(html, "default"));
});

it("renders the live Connect-a-tool modal (state: connect)", () => {
  copyAssets();
  const html = renderToStaticMarkup(
    <>
      <RouteFrame />
      <ConnectModal
        open
        onClose={noop}
        catalog={CATALOG}
        onSelectEntry={noop}
        onConnect={noop}
        onAddCustomServer={noop}
        pending={false}
        error={null}
      />
    </>,
  );
  expect(html).toContain('data-testid="settings-modal"');
  expect(html).toContain('data-testid="connect-catalog-list"');
  expect(html).toContain('data-testid="connect-catalog-custom"');
  expect(html).toContain("Google Calendar");
  writeFileSync(LIVE("connect.html"), shell(html, "connect"));
});
