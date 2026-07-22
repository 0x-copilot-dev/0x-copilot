/* design-parity · live CHATS destination render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `ChatsArchive` — the exact
 * component BOTH hosts mount for the `chats` slug — to static HTML wrapped
 * with the REAL design-system token sheet, so the browser extractor reads the
 * shipping computed styles. This is the "live" side of the chats parity diff;
 * the "design" side is the vendored Claude Design ChatsSurface
 * (design-kit/app-v3/index.html?dest=chats&state=default).
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-chats.test.tsx
 * Output: surfaces/chats/live/default.html  (+ copied ds.css + fonts/)
 *
 * WHY `ChatsArchive` AND NOT `ChatsDestination`
 * ---------------------------------------------
 * `ChatsDestination` (destinations/chats/ChatsDestination.tsx:28-48) is a
 * 48-line pass-through wrapper around `ChatsArchive` — and it is mounted by
 * NEITHER host. Verified:
 *   * web     — apps/frontend/src/app/App.tsx:1042-1054 renders
 *               `<ChatsArchiveRoute>`, which mounts `<ChatsArchive>` directly
 *               (apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:36,142);
 *   * desktop — apps/desktop/renderer/DestinationOutlet.tsx:188-193 renders
 *               `<ChatsBinder>`, which also mounts `<ChatsArchive>` directly
 *               (apps/desktop/renderer/destinationBinders.tsx:215-231).
 * Rendering the wrapper would add nothing to the DOM (it forwards every prop
 * unchanged) — but rendering `ChatsArchive` is the honest choice because it is
 * what actually ships on both substrates.
 *
 * WHY NO TOPBAR IN THIS FILE
 * --------------------------
 * The design's chats page sits under the shell topbar ("Chats" + subtitle), and
 * the design-side anchor map has a `topbar.title` anchor. The live shell does
 * NOT render one here: `chats` is in `FULL_BLEED_DESTINATIONS`
 * (packages/chat-surface/src/shell/ChatShell.tsx:43-47) and the shell suppresses
 * the Topbar for full-bleed destinations (ChatShell.tsx:236-237 + 304-311 →
 * `{fullBleed ? null : <Topbar …>}`). Neither host adds a replacement bar
 * (apps/frontend/src/app/App.tsx:1047-1053 wraps the route in a bare
 * `<section data-testid="destination-outlet">`; the desktop outlet likewise has
 * no Topbar — `grep -n "Topbar" apps/desktop/renderer/*.tsx` → no match). So a
 * topbar is deliberately absent from the live HTML instead of being fabricated;
 * `topbar.title` has NO live counterpart and must be reported as a structural
 * divergence, not measured. (`rail.badge` is likewise out of scope here — the
 * rail is a separate surface with its own harness.)
 *
 * STYLESHEETS
 * -----------
 * The Chats surface ships ZERO CSS-file rules of its own: `.pg-lead`, `.sect-h`
 * and `.rowlist` are class HOOKS only — every visual property comes from inline
 * `CSSProperties` in `ChatsArchive.tsx` and its
 * `destinations/_shared/{Row,RowList,SectionHeader,PageLead}.tsx` primitives
 * plus `shell/StatusPill.tsx` (verified: `find packages/chat-surface/src -name
 * '*.css'` → only composer/workspace/onboarding, none of which define these
 * selectors). The one real stylesheet in play is the design-system sheet, which
 * supplies BOTH the tokens those inline styles read (`var(--color-*)`,
 * `var(--font-size-*)`, …) AND the `.ui-button--primary.ui-button--sm` rules
 * behind the "New chat" `<Button>` (packages/design-system/src/index.tsx:144-163
 * → styles.css:409-472). So `styles.css` is the only sheet linked here.
 * ========================================================================= */
import { createElement as h } from "react";
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
import { ChatsArchive } from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/chats/live/" + p);

// ---------------------------------------------------------------------------
// Fixture — mirrors the design's CHATS fixture 1:1
// (design-kit/app-v3/copilot-data.jsx:192-201): EIGHT rows bucketed 1 pinned /
// 5 recent / 2 archived, same titles, same previews, same model strings, same
// status mix (running | done, done, paused, done, done | archived, archived).
//
// The design fixture carries pre-formatted `when` strings ("now", "2h", "3h",
// "1d", "Mon"); the live wire type carries an ISO `updated_at` and the component
// derives the relative time itself (util/time.ts formatRelativeTime), so the
// timestamps below are the design's coarse ages rebuilt as real instants against
// a pinned NOW. (The rendered vocabulary therefore differs — live says "2 hr.
// ago" where the design says "2h" — which is a copy divergence for the report,
// not something the fixture should paper over.)
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

// Recent — the 5 unpinned, non-archived rows (design rows recon / investor / lp
// / triage / ama), in fixture order so `paused` lands 3rd exactly as the design
// anchor `.rowlist:nth-child(5) > .lrow:nth-child(3) .chip--warn` expects.
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
 *  frame approximating the full-bleed destination viewport the shell hands
 *  Chats. The inner flex wrapper reproduces the WEB host's own sizing chrome
 *  (apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:53-75 rootStyle +
 *  surfaceStyle, inside App.tsx's `height:100%; overflow:auto` outlet section),
 *  so the surface is measured at the height it really gets. Typography, colour,
 *  border and padding are frame-independent; width/height are comparator noise. */
function shell(inner: string): string {
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
      /* host chrome: ChatsArchiveRoute rootStyle → surfaceStyle */
      #host { height: 100%; width: 100%; min-height: 0; display: flex; flex-direction: column; }
      #host-surface { flex: 1 1 auto; min-height: 0; }
    </style>
  </head>
  <body>
    <div id="frame"><div id="host"><div id="host-surface">${inner}</div></div></div>
  </body>
</html>`;
}

describe("live chats — ChatsArchive → static HTML", () => {
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

  it("default — pinned / recent / archived archive over the 8-row fixture", () => {
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
    // Exactly one live row → exactly one BrandMark icon slot (the design's
    // single `.dotk` / jade affordance).
    expect(
      root.querySelectorAll(
        '[data-testid="chat-archive-row-icon"][data-live="true"]',
      ),
    ).toHaveLength(1);
    expect(screen.getByTestId("chats-new-chat")).not.toBeNull();

    writeFileSync(LIVE("default.html"), shell(root.outerHTML));
  });
});
