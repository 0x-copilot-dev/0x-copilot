/* design-parity · live SURFACES-V2 canvas tab strip (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `TcTabs` — the exact kit tab strip
 * the Run cockpit's ThreadCanvas mounts for Generative Surfaces v2 named
 * surfaces — to static HTML, wrapped with the REAL design-system styles.css, so
 * the browser extractor reads the shipping computed styles. This is the "live"
 * side of the surfaces-v2-canvas parity diff; the "design" side is the vendored
 * Claude Design mock (../surfaces/surfaces-v2-canvas/design, `.sheet-tabs`).
 *
 * The DESIGN side is a design-compiler template (`index.dc.html`, `{{ }}`
 * placeholders), so producing the 0-HIGH computed-style report is an
 * INTEGRATION-time step (compile the mock via DesignSync, then run
 * `lib/compare.mjs`). See ../surfaces/surfaces-v2-canvas/out/README.md. This
 * test stages the live HTML so that follow-up is mechanical.
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/surfaces-v2-canvas/live/tabs.html (+ copied stylesheet)
 * ========================================================================= */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import { TcTabs } from "@0x-copilot/chat-surface";

const HERE = (p) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p) => HERE("../surfaces/surfaces-v2-canvas/live/" + p);

// Two named surfaces (a record + a table), the newest active, one pinned — the
// same shape the ledger fold hands the cockpit (record://surfaces-v2/<id>).
const tabs = [
  { uri: "table://surfaces-v2/s_list", title: "Sprint backlog", pinned: false },
  {
    uri: "record://surfaces-v2/s_issue",
    title: "ENG-142 Fix reconnect",
    pinned: true,
  },
];

function shell(inner) {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · surfaces-v2-canvas · LIVE</title>
    <link rel="stylesheet" href="./styles.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1040px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans);
      }
    </style>
  </head>
  <body>
    <div id="frame">${inner}</div>
  </body>
</html>`;
}

it("renders the live surfaces-v2 tab strip to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );

  const strip = renderToStaticMarkup(
    h(TcTabs, {
      tabs,
      activeUri: "table://surfaces-v2/s_list",
      onActivate: () => undefined,
      onClose: () => undefined,
    }),
  );
  writeFileSync(LIVE("tabs.html"), shell(strip));
});
