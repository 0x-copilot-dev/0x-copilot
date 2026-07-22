/* design-parity · live RUN-EMPTY composer render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface `OnboardingComposer` — the exact
 * component the Run cockpit's empty state mounts ("What should we run first?"
 * hero + starter chips + AssistantComposer) — to static HTML, wrapped with the
 * REAL design-system styles.css + the FTUE onboarding.css + the composer.css,
 * so the browser extractor reads the shipping computed styles. This is the
 * "live" side of the run-empty parity diff; the "design" side is the vendored
 * Claude Design composer stage (../surfaces/run-empty/design, ?state=key).
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/run-empty/live/composer.html  (+ copied stylesheets)
 * ========================================================================= */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import {
  OnboardingComposer,
  TransportProvider,
} from "@0x-copilot/chat-surface";

const HERE = (p) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p) => HERE("../surfaces/run-empty/live/" + p);

// Minimal substrate fakes — the parity render only needs the DOM + classes, so
// the ports never actually do I/O.
const fakeTransport = {
  request: () => Promise.resolve({}),
  subscribeServerSentEvents: () => ({ close: () => undefined }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "web",
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: false,
    openExternal: false,
  }),
};

const fakeFilePicker = { pick: () => Promise.resolve([]) };

// One curated model so the pill announces a concrete name (mirrors the design
// mock's "Claude Sonnet 4.5" pill on the composer stage).
const models = [
  {
    id: "claude-sonnet-4-5",
    provider: "anthropic",
    model_name: "claude-sonnet-4-5",
    name: "Claude Sonnet 4.5",
    configured: true,
    supports_streaming: true,
  },
];

/** Wrap the composer's static markup with the real stylesheets + the centered
 *  FTUE `.fr` / `.fr-main` frame the surface mounts inside (typography/color/
 *  border/padding are container-independent; width/height are comparator noise). */
function shell(inner) {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · run-empty · LIVE</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./onboarding.css" />
    <link rel="stylesheet" href="./composer.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1040px; height: 720px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans); overflow: hidden;
      }
    </style>
  </head>
  <body>
    <div id="frame"><div class="fr"><main class="fr-main">${inner}</main></div></div>
  </body>
</html>`;
}

it("renders the live run-empty composer to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );
  copyFileSync(
    REPO("packages/chat-surface/src/onboarding/onboarding.css"),
    LIVE("onboarding.css"),
  );
  copyFileSync(
    REPO("packages/chat-surface/src/composer/composer.css"),
    LIVE("composer.css"),
  );

  const composer = renderToStaticMarkup(
    h(
      TransportProvider,
      { transport: fakeTransport },
      h(OnboardingComposer, {
        connectors: { servers: [], loading: false },
        skills: { skills: [], loading: false },
        filePicker: fakeFilePicker,
        renderPlusMenu: () => null,
        skillInstructionPrompt: (n) => n,
        mcpServerInstructionPrompt: (n) => n,
        onShowConnectors: () => undefined,
        onOpenSkillsSettings: () => undefined,
        onOpenMcpSettings: () => undefined,
        models,
        selectedModel: "claude-sonnet-4-5",
        onModelChange: () => undefined,
        onSubmit: () => undefined,
      }),
    ),
  );
  writeFileSync(LIVE("composer.html"), shell(composer));
});
