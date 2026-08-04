/* design-parity · live COMPOSER DICTATION-FAILURE render (vitest + jsdom)
 * =========================================================================
 * Reproduces, and then pins the fix for, the bug where a FAILED microphone
 * made the composer TALLER: the dictation status message was an in-flow flex
 * item in the bottom row's right cluster with `white-space: nowrap`, no
 * `min-width: 0` and no `overflow` — so its automatic minimum size was the
 * whole sentence, that width joined the cluster's min-content size, and the
 * `flex-wrap: wrap` row dropped model + mic + send onto a second line.
 *
 * Two documents, IDENTICAL DOM, differing only in the CSS for
 * `.aui-composer-dictation-status`:
 *   surfaces/composer/live/dictation-before.html — the pre-fix rule restored
 *       on top of the current sheets (in-flow, unshrinkable).
 *   surfaces/composer/live/dictation-after.html  — the sheets as they ship.
 * Screenshot both at the same frame and the row height is the whole story.
 *
 * GEOMETRY: the frame is 1040px so `.fr-main` (onboarding.css:
 * `width: min(640px, 92%)`) resolves to exactly 640px — the column the bug was
 * reported at. The wrap is a min-content threshold, so the width is load-
 * bearing: widen the frame and the row stops wrapping on its own.
 *
 * The failure state is driven through the REAL port contract — the host's
 * `DictationPort.start` hands back callbacks and we invoke `onError`, exactly
 * as `DesktopSpeechRecognitionDictationPort` does on a `network` error.
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *        lib/render-live-composer-dictation.test.tsx
 * ========================================================================= */
import { createElement as h } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, expect, it } from "vitest";

import {
  BypassPill,
  ComposerToolsTrigger,
  OnboardingComposer,
  TransportProvider,
} from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p);
const LIVE = (p: string) => HERE("../surfaces/composer/live/" + p);

/** Same sheets, same cascade order, as `render-live-composer.test.tsx`. */
const SHEETS = [
  ["design-system/src/styles.css", "packages/design-system/src/styles.css"],
  ["apps/frontend/src/styles.css", "apps/frontend/src/styles.css"],
  [
    "chat-surface/src/onboarding/onboarding.css",
    "packages/chat-surface/src/onboarding/onboarding.css",
  ],
  [
    "chat-surface/src/composer/composer.css",
    "packages/chat-surface/src/composer/composer.css",
  ],
];

function inlinedCss(): string {
  return SHEETS.map(
    ([label, rel]) =>
      `/* ===== ${label} ===== */\n${readFileSync(REPO(rel), "utf8")}`,
  ).join("\n\n");
}

/* The rule as it stood BEFORE the fix, restored by overriding every property
 * the fix introduced back to its initial value. Appended last at the same 0,1,0
 * specificity, so it wins — this is the old computed style, not an impression
 * of it. */
const BEFORE_OVERRIDE = `
/* ===== BEFORE: pre-fix .aui-composer-dictation-status, restored ===== */
.aui-composer-dictation-status {
  background: none;
  border: 0;
  border-radius: 0;
  bottom: auto;
  box-shadow: none;
  max-width: none;
  overflow: visible;
  padding: 0;
  position: static;
  right: auto;
  text-overflow: clip;

  color: var(--color-text-subtle);
  font-size: var(--font-size-2xs);
  white-space: nowrap;
}
.aui-composer-dictation-status[data-state="error"] {
  border-color: currentcolor;
  color: var(--color-danger);
}
`;

const FRAME_CSS = `
html, body { margin: 0; height: 100%; background: #050506; }
#frame {
  width: 1040px; height: 720px; display: flex; flex-direction: column;
  background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
  font-family: var(--font-sans); overflow: hidden;
}
`;

function shell(title: string, composerHtml: string, extraCss: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>${title}</title>
    <style>
${inlinedCss()}
${FRAME_CSS}
${extraCss}
    </style>
  </head>
  <body>
    <div id="frame"><div class="fr"><main class="fr-main">${composerHtml}</main></div></div>
  </body>
</html>`;
}

const noop = () => undefined;

const fakeTransport = {
  request: () => Promise.resolve({}),
  subscribeServerSentEvents: () => ({ close: noop }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "web" as const,
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: false,
    openExternal: false,
  }),
};

const fakeFilePicker = { pick: () => Promise.resolve([]) };

const fakeConnectorsPort = {
  listServers: () => Promise.resolve([]),
  listCatalog: () => Promise.resolve([]),
  installFromCatalog: () => Promise.reject(new Error("not used")),
  beginAuth: () => Promise.resolve(),
};

/* Present, so `folderControlsVisible` is true — that is what mounts the
 * "Attach a folder" bar AND the Manual execution-mode pill. The pill is part of
 * the repro, not decoration: it widens the left cluster by ~65px, which is what
 * puts the row over its wrap threshold at 640px. */
const fakeWorkspaceGrantPort = {
  requestGrant: () => Promise.resolve({ status: "cancelled" as const }),
  listGrants: () => Promise.resolve([]),
  revokeGrant: () => Promise.resolve({ status: "revoked" as const }),
};

const MODELS = [
  {
    id: "anthropic/claude-haiku-4-5",
    provider: "anthropic",
    model_name: "claude-haiku-4-5",
    name: "Claude Haiku 4.5",
    description: "Fast",
    configured: true,
    supports_streaming: true,
  },
];
const SELECTED = "anthropic/claude-haiku-4-5";

/** The copy `DesktopSpeechRecognitionDictationPort` emits for a `network` error. */
const ERROR_COPY = "Voice transcription is unavailable right now.";

/** Mounts the composer and drives the mic into its failed state. */
function mountFailedDictation() {
  let callbacks: { onError: (m: string) => void } | null = null;
  const dictationPort = {
    start: (next: never) => {
      callbacks = next;
      return { stop: noop, cancel: noop };
    },
  };

  const view = render(
    h(
      TransportProvider,
      { transport: fakeTransport },
      h(OnboardingComposer, {
        connectors: { servers: [], loading: false },
        skills: { skills: [], loading: false },
        filePicker: fakeFilePicker,
        renderPlusMenu: () => null,
        skillInstructionPrompt: (n: string) => n,
        mcpServerInstructionPrompt: (n: string) => n,
        onShowConnectors: noop,
        onOpenSkillsSettings: noop,
        onOpenMcpSettings: noop,
        toolsTrigger: h(ComposerToolsTrigger, {
          port: fakeConnectorsPort,
          webSearchEnabled: true,
          onToggleWebSearch: noop,
          activeConnectorIds: [],
          onToggleConnector: noop,
          onConnectCatalog: noop,
          onAddCustom: noop,
        }),
        workspaceGrantPort: fakeWorkspaceGrantPort,
        bypassTrigger: h(BypassPill, {
          mode: "manual",
          enabled: true,
          onChange: noop,
        }),
        dictationPort,
        models: MODELS,
        selectedModel: SELECTED,
        onModelChange: noop,
        onSubmit: noop,
      }),
    ),
  );

  fireEvent.click(view.container.querySelector(".atlas-composer-mic")!);
  expect(callbacks).not.toBeNull();
  act(() => callbacks!.onError(ERROR_COPY));

  const status = view.container.querySelector(
    '[data-testid="assistant-composer-dictation-status"]',
  );
  expect(status).not.toBeNull();
  expect(status!.textContent).toBe(ERROR_COPY);
  expect(status!.getAttribute("data-state")).toBe("error");
  // The message must stay INSIDE the right cluster: the fix is that it is out
  // of FLOW, not out of the DOM. If a later change moves it, the CSS anchor
  // (`.aui-composer-action-wrapper`, the nearest positioned ancestor) still
  // holds, but this is the arrangement the rule was written against.
  expect(
    view.container
      .querySelector(".aui-composer-action-wrapper__right")!
      .contains(status),
  ).toBe(true);

  return view;
}

afterEach(() => cleanup());

it("renders the failed-dictation composer with the PRE-FIX status rule", () => {
  mkdirSync(LIVE(""), { recursive: true });
  const { container } = mountFailedDictation();
  writeFileSync(
    LIVE("dictation-before.html"),
    shell(
      "composer · voice failed · BEFORE (in-flow status)",
      container.innerHTML,
      BEFORE_OVERRIDE,
    ),
  );
});

it("renders the failed-dictation composer as it now ships", () => {
  mkdirSync(LIVE(""), { recursive: true });
  const { container } = mountFailedDictation();
  writeFileSync(
    LIVE("dictation-after.html"),
    shell(
      "composer · voice failed · AFTER (out-of-flow status)",
      container.innerHTML,
      "",
    ),
  );
});
