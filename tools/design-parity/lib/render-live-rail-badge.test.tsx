/* design-parity · live RAIL + RUN-BADGE render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL `ChatShell` (which mounts the REAL `AppRail`) to static
 * HTML, in the SOLO 6-destination profile view the shipping app uses, so the
 * browser extractor reads the exact computed styles the app produces for the
 * app rail and its Run count badge.
 *
 * Two states mirror the design mock's two rail states:
 *   badge   — active destination = `chats`, `railBadges={{ run: 1 }}`
 *             → the Run item shows the "1" pill (design: ?dest=chats).
 *   nobadge — active destination = `run`, same badges prop
 *             → the rail suppresses the badge on the ACTIVE destination
 *               (design: ?dest=workspace).
 *
 * The badge prop is supplied here deliberately: the harness measures what the
 * rail RENDERS when a host feeds it. Whether each host actually feeds it is a
 * wiring question answered in FINDINGS.md, not something the pixels can show.
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-rail-badge.test.tsx
 * Output: surfaces/rail-badge/live/{badge,nobadge}.html (+ styles.css)
 * ========================================================================= */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import {
  ChatShell,
  destinationsForProfile,
  type KeyValueStore,
  type PresenceSignal,
  type Router,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/rail-badge/live/" + p);

// ---- substrate fakes -------------------------------------------------------
// The rail is a pure controlled view (AppRail takes activeDestination +
// onNavigate and nothing else), but ChatShell installs the four substrate
// providers, so they must exist. None of them does I/O on a static render.
const fakeTransport = {
  request: () => Promise.resolve({}),
  subscribeServerSentEvents: () => ({ close: () => undefined }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "web" as const,
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: false,
    openExternal: false,
  }),
};

const fakeRouter: Router<{ kind: string }> = {
  current: () => ({ kind: "run" }),
  navigate: () => undefined,
  subscribe: () => () => undefined,
};

const fakeKeyValueStore: KeyValueStore = {
  get: () => null,
  set: () => undefined,
  keys: () => [],
};

const fakePresenceSignal: PresenceSignal = {
  current: () => "visible",
  subscribe: () => () => undefined,
};

// The SOLO rail — Run, Chats, Projects, Activity, Tools(slug `connectors`),
// Skills(slug `tools`) — i.e. exactly what the desktop pins and what the web
// host resolves from `VITE_DEPLOYMENT_PROFILE`'s default. This is the list the
// design mock's DEST array corresponds to (copilot-app.jsx:4-11).
const soloDestinations = destinationsForProfile("single_user_desktop");

/** Wrap the shell's static markup with the REAL design-system stylesheet, in a
 *  window-sized frame matching the design mock's `.mw-body` (1220×840 window
 *  minus the 38px title bar → an 800px-tall rail), so elastic rules
 *  (`margin-top:auto`, `flex:1`) resolve the same way on both sides. */
function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · rail-badge · LIVE</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      /* Mirrors the design mock's .mw (1220×840) minus the 38px .mw-bar. */
      #frame {
        width: 1220px; height: 800px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans); overflow: hidden;
      }
      #frame > * { flex: 1; min-height: 0; }
    </style>
  </head>
  <body>
    <div id="frame">${inner}</div>
  </body>
</html>`;
}

function renderState(activeDestination: ShellDestinationSlug): string {
  return renderToStaticMarkup(
    h(
      ChatShell,
      {
        transport: fakeTransport,
        router: fakeRouter,
        keyValueStore: fakeKeyValueStore,
        presenceSignal: fakePresenceSignal,
        activeDestination,
        destinations: soloDestinations,
        onNavigate: () => undefined,
        // Supplying onOpenSettings is what makes the rail render its FOOT
        // (Settings gear + account avatar) — AppRail omits the whole foot
        // without it.
        onOpenSettings: () => undefined,
        onOpenCommandPalette: () => undefined,
        // Design mock's avatar letter is prefs.name.slice(0,1) = "S" (Sasha).
        railIdentity: { initial: "S" },
        // Design mock's DEST[0].badge is the literal "1".
        railBadges: { run: 1 },
      },
      h("div", { style: { minHeight: 0 } }),
    ),
  );
}

it("renders the live app rail (badge + nobadge) to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );

  // badge   → Chats is active, so the Run destination shows its count pill.
  writeFileSync(LIVE("badge.html"), shell(renderState("chats")));
  // nobadge → Run is active, so the rail suppresses its own badge.
  writeFileSync(LIVE("nobadge.html"), shell(renderState("run")));
});
