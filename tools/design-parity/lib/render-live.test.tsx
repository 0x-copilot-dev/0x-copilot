/* design-parity · live-render harness (vitest + jsdom)
 * =========================================================================
 * Renders the REAL @0x-copilot/chat-surface first-run components to static
 * HTML, wrapped with the REAL design-system `styles.css` + the FTUE
 * `onboarding.css`, so the browser extractor reads the exact computed styles
 * the shipping app produces. This is the "live" side of the parity diff; the
 * "design" side is the vendored Claude Design mock (../surfaces/.../design/).
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/first-run/live/<state>.html  (+ copied styles.css/onboarding.css)
 *
 * Why renderToStaticMarkup: the FTUE gate renders fully on the first pass
 * (no effect needed — see FirstRunSurface.test.tsx), so a single synchronous
 * render captures the real DOM + classes deterministically, no act()/timers.
 * States that need interaction (keyform-open, composer, ack) are TODO — drive
 * them with @testing-library `fireEvent` then serialize `container.innerHTML`.
 * ========================================================================= */
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { it } from "vitest";

import { FirstRunSurface } from "@0x-copilot/chat-surface";
import type {
  ProviderKeysPort,
  ProviderKeySummary,
} from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/first-run/live/" + p);

// Minimal fake — the gate only calls providerKeys.list(); save/remove are unused
// on the gate path but the port shape requires them.
const fakeProviderKeys: ProviderKeysPort = {
  list: () => Promise.resolve([]),
  save: (provider: string) =>
    Promise.resolve({
      provider: provider as ProviderKeySummary["provider"],
      key_hint: "…zzzz",
      updated_at: new Date(0).toISOString(),
    }),
  remove: () => Promise.resolve(),
};

/** Wrap a surface's static markup with the real stylesheets + a sized, dark
 *  container mimicking the desktop window the FTUE mounts inside (1040×720),
 *  so flex/grid layout resolves the same way the app does. Typography/color/
 *  border/padding do not depend on the container; width/height are treated as
 *  noise by the comparator. */
function shell(inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · first-run · LIVE</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./onboarding.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      /* The desktop/web host mounts the FTUE inside a full-height app surface.
         Mirror that: a fixed-size, column-flex, ink-backed frame. */
      #frame {
        width: 1040px; height: 720px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans); overflow: hidden;
      }
    </style>
  </head>
  <body>
    <div id="frame">${inner}</div>
  </body>
</html>`;
}

it("renders the live first-run gate to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  // Copy the REAL stylesheets next to the harness so the browser serves them.
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );
  copyFileSync(
    REPO("packages/chat-surface/src/onboarding/onboarding.css"),
    LIVE("onboarding.css"),
  );

  const gate = renderToStaticMarkup(
    h(FirstRunSurface, {
      providerKeys: fakeProviderKeys,
      onSkip: () => undefined,
      onComplete: () => undefined,
    }),
  );
  writeFileSync(LIVE("gate.html"), shell(gate));
});
