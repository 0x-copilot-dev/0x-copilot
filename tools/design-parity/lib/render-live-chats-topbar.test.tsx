/* design-parity · live CHATS destination render — TOPBAR + archive (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `Topbar` (resolved for the `chats`
 * slug) STACKED ABOVE the REAL `ChatsArchive` — i.e. the exact page both hosts
 * mount for `chats` AFTER PRD-09 — to static HTML wrapped with the REAL
 * design-system token sheet, so the browser extractor reads the shipping
 * computed styles. This is the "live" side of the chats parity diff; the
 * "design" side is the vendored Claude Design ChatsSurface under its topbar
 * (design-kit/app-v3/index.html?dest=chats&state=default).
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-chats-topbar.test.tsx
 * Output: surfaces/chats/live/default.html  (+ copied ds.css + fonts/)
 *
 * WHY THE TOPBAR NOW BELONGS IN THIS RENDER
 * -----------------------------------------
 * The design's chats page sits UNDER the shell topbar ("Chats" + the subtitle
 * "every conversation with the agent"), and the design URL `?dest=chats`
 * extracts that topbar as the `topbar.title` anchor. Until PRD-09, `chats` was
 * a full-bleed destination and the shell suppressed the Topbar, so the old
 * body-only harness (`render-live-chats.test.tsx`, now superseded by this file)
 * left `topbar.title` with NO live counterpart — a structural HIGH. PRD-09
 * narrows `SUPPRESS_TOPBAR` to `{"run"}` (packages/chat-surface/src/shell/
 * ChatShell.tsx:47), so `chats` now renders the shell Topbar on BOTH hosts.
 * The live render therefore must include it, matched anchor-for-anchor against
 * the design, instead of fabricating an absence.
 *
 * WHY `Topbar` + `ChatsArchive` DIRECTLY, NOT `ChatShell`
 * ------------------------------------------------------
 * `ChatShell` mounts the active destination's body from its registry, which
 * pulls the archive over the transport port; with a static fake that yields no
 * rows, so the 8-row fixture the design measures would be lost. Composing the
 * two REAL leaf components (`Topbar` + `ChatsArchive`) is the honest way to get
 * BOTH the shell topbar AND the populated body into one measurable document —
 * `Topbar` is the same component `ChatShell` renders for `chats`
 * (ChatShell.tsx:35 import; SUPPRESS_TOPBAR excludes `chats`), and
 * `ChatsArchive` is the same body both hosts mount (web
 * ChatsArchiveRoute.tsx:36; desktop destinationBinders.tsx). No prop of either
 * is faked for looks.
 *
 * STYLESHEETS
 * -----------
 * The Chats surface + the Topbar ship ZERO CSS-file rules of their own beyond a
 * few class HOOKS — every visual property comes from inline `CSSProperties` in
 * `Topbar.tsx` / `ChatsArchive.tsx` and the `destinations/_shared/*` primitives
 * plus `shell/StatusPill.tsx`. The one real stylesheet in play is the
 * design-system sheet, which supplies BOTH the tokens those inline styles read
 * AND the `.ui-button--primary.ui-button--sm` rules behind the "New chat"
 * `<Button>`. So `styles.css` is the only sheet linked here.
 * ========================================================================= */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  ConversationId,
  SectionResult,
} from "@0x-copilot/api-types";
import { ChatsArchive, Topbar } from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/chats/live/" + p);

// ---------------------------------------------------------------------------
// Fixture — mirrors the design's CHATS fixture 1:1
// (design-kit/app-v3/copilot-data.jsx:192-201): EIGHT rows bucketed 1 pinned /
// 5 recent / 2 archived, same titles, previews, model strings, status mix.
// The design fixture carries pre-formatted `when` strings; the live wire type
// carries an ISO `updated_at` and the component derives the relative time
// itself, so the timestamps below are the design's coarse ages rebuilt as real
// instants against a pinned NOW.
// ---------------------------------------------------------------------------

const NOW = Date.parse("2026-07-17T12:00:00Z"); // Fri 17 Jul 2026, 12:00 UTC

const ago = (ms: number): string => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const id = (s: string): ConversationId => s as unknown as ConversationId;

// Pinned — the single live/running conversation (design row `launch`).
const PINNED: ReadonlyArray<ChatArchiveRow> = [
  {
    id: id("conv_launch"),
    title: "Launch Week ops",
    status: "running",
    preview: "Streaming the launch thread",
    model: "Claude Sonnet 4.5",
    updated_at: ago(20_000), // design "now"
    pinned: true,
  },
];

// Recent — the 5 unpinned, non-archived rows, in fixture order so `paused`
// lands 3rd exactly as the design anchor expects.
const RECENT: ReadonlyArray<ChatArchiveRow> = [
  {
    id: id("conv_recon"),
    title: "Weekly treasury reconciliation",
    status: "done",
    preview: "Balanced 3 accounts, flagged 1 variance",
    model: "Claude Sonnet 4.5",
    updated_at: ago(2 * HOUR), // design "2h"
    pinned: false,
  },
  {
    id: id("conv_investor"),
    title: "Investor update — July",
    status: "done",
    preview: "Draft saved to Local files",
    model: "Local · Llama 3.3 70B",
    updated_at: ago(3 * HOUR), // design "3h"
    pinned: false,
  },
  {
    id: id("conv_lp"),
    title: "Rebalance LP positions",
    status: "paused",
    preview: "Paused — a swap needs your approval",
    model: "Claude Sonnet 4.5",
    updated_at: ago(1 * DAY), // design "1d"
    pinned: false,
  },
  {
    id: id("conv_triage"),
    title: "Triage new GitHub issues",
    status: "done",
    preview: "Labeled 3, escalated 1",
    model: "Qwen 2.5 Coder 32B",
    updated_at: ago(1 * DAY + 2 * HOUR), // design "1d"
    pinned: false,
  },
  {
    id: id("conv_ama"),
    title: "Summarize Discord AMA",
    status: "done",
    preview: "Posted recap to #announcements",
    model: "Claude Sonnet 4.5",
    updated_at: ago(1 * DAY + 5 * HOUR), // design "1d"
    pinned: false,
  },
];

// Archived — the 2 rows the design parks under "Archived · history".
const ARCHIVED: ReadonlyArray<ChatArchiveRow> = [
  {
    id: id("conv_digest"),
    title: "Competitor launch digest",
    status: "archived",
    preview: "6 sources · saved 1 page",
    model: "Claude Sonnet 4.5",
    updated_at: ago(4 * DAY), // design "Mon"
    pinned: false,
  },
  {
    id: id("conv_invoices"),
    title: "Vendor invoice batch",
    status: "archived",
    preview: "You rejected 2 of 6 payouts",
    model: "Claude Sonnet 4.5",
    updated_at: ago(4 * DAY + 6 * HOUR), // design "Mon"
    pinned: false,
  },
];

const ARCHIVE: SectionResult<ChatsArchiveData> = {
  status: "ok",
  data: { pinned: PINNED, recent: RECENT, archived: ARCHIVED },
};

/** Wrap the captured markup with the REAL design-system sheet and a fixed dark
 *  frame approximating the destination viewport the shell hands Chats: a
 *  46px-tall Topbar (flex:none) above the archive body (flex:1, its own
 *  scroll), mirroring the design mock's `.topbar` + `.pg` column. Typography,
 *  colour, border and padding are frame-independent; width/height are
 *  comparator noise. */
function shell(topbar: string, body: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · chats · LIVE</title>
    <link rel="stylesheet" href="./ds.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1180px; height: 820px; display: flex; flex-direction: column;
        background: var(--color-bg); color: var(--color-text);
        font-family: var(--font-sans); overflow: hidden;
      }
      /* host chrome: ChatsArchiveRoute rootStyle → surfaceStyle, under the topbar */
      #host { flex: 1 1 auto; min-height: 0; width: 100%; display: flex; flex-direction: column; }
      #host-surface { flex: 1 1 auto; min-height: 0; }
    </style>
  </head>
  <body>
    <div id="frame">${topbar}<div id="host"><div id="host-surface">${body}</div></div></div>
  </body>
</html>`;
}

describe("live chats — Topbar + ChatsArchive → static HTML", () => {
  beforeAll(() => {
    mkdirSync(LIVE(""), { recursive: true });
    copyFileSync(REPO("packages/design-system/src/styles.css"), LIVE("ds.css"));
    // ds.css @font-face's the vendored JetBrains Mono at a path relative to
    // itself — copy the woff2s alongside so the mono section heads / model tags
    // / times measure with the REAL face instead of a fallback metric.
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
    cleanup();
  });

  it("default — chats topbar over the 8-row pinned / recent / archived archive", () => {
    // The topbar is a leaf view (no ports); render it to static markup. Its
    // title resolves to "Chats" and its subtitle to "every conversation with
    // the agent" straight from destinations.ts — the same source ChatShell uses.
    const topbar = renderToStaticMarkup(
      h(Topbar, {
        activeDestination: "chats",
        onOpenCommandPalette: () => undefined,
      }),
    );

    render(
      h(ChatsArchive, {
        archive: ARCHIVE,
        now: NOW,
        onReopen: () => undefined,
        onNewChat: () => undefined,
        onRetry: () => undefined,
      }),
    );

    // Sanity: the REAL surface rendered its ready state, all three sections and
    // every fixture row — not a skeleton, an error card, or the empty state.
    const root = screen.getByTestId("chats-archive");
    expect(root.getAttribute("data-state")).toBe("ready");
    expect(screen.getByTestId("chats-sections")).not.toBeNull();
    expect(screen.getByTestId("chats-section-pinned")).not.toBeNull();
    expect(screen.getByTestId("chats-section-recent")).not.toBeNull();
    expect(screen.getByTestId("chats-section-archived")).not.toBeNull();
    expect(screen.getAllByTestId("chat-archive-row")).toHaveLength(8);
    // Exactly one live row → exactly one BrandMark icon slot.
    expect(
      root.querySelectorAll(
        '[data-testid="chat-archive-row-icon"][data-live="true"]',
      ),
    ).toHaveLength(1);
    expect(screen.getByTestId("chats-new-chat")).not.toBeNull();
    // The topbar carries the destination title + subtitle the design measures.
    expect(topbar).toContain('data-testid="topbar-title"');
    expect(topbar).toContain("Chats");
    expect(topbar).toContain("every conversation with the agent");

    writeFileSync(LIVE("default.html"), shell(topbar, root.outerHTML));
  });
});
