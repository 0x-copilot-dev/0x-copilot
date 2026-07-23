/* design-parity · live COMPOSER BOTTOM-ROW + MODEL-PICKER render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL shipping composer — `@0x-copilot/chat-surface`
 * `OnboardingComposer` → `AssistantComposer` → `ModelPill` — into jsdom via
 * @testing-library/react, then serialises the resulting DOM into two static
 * HTML documents with the REAL stylesheets INLINED, so the browser extractor
 * reads the exact computed styles the app produces.
 *
 * Two states:
 *   surfaces/composer/live/closed.html — the full bottom row: `+` button,
 *       Tools trigger (a real <ComposerToolsButton open={false} activeCount={1}/>,
 *       wrapped in the same relative/inline-flex span the web host's
 *       `ChatToolsTrigger` mounts), mic, model pill, send button, hint row.
 *   surfaces/composer/live/model.html — the SAME composer with the ModelPill
 *       popover OPEN: "Your keys" (2 cloud models) + "Local · on-device"
 *       (1 ollama model) + the footer.
 *
 * HOW THE OPEN STATE IS PRODUCED (option (i) from the brief): the popover is a
 * click-driven `useState`, so `renderToStaticMarkup` can never reach it. We
 * render with @testing-library/react and `fireEvent.click` the pill, exactly as
 * `packages/chat-surface/src/composer/ModelPill.test.tsx` does.
 *
 * PORTAL: the design-system `<Menu>` primitive `createPortal`s to
 * `document.body` (packages/design-system/src/index.tsx). So the menu is NOT in
 * the RTL container — we serialise the container for the composer subtree AND
 * every sibling `document.body` child for the portal, and re-emit the portal as
 * a direct child of `<body>`, which is where it lives at runtime.
 *
 * ANCHOR GEOMETRY: `Menu.computePosition()` reads
 * `anchorRef.current.getBoundingClientRect()` and writes fixed-viewport
 * coordinates + `min-width: <anchor width>px` as an INLINE style. jsdom has no
 * layout, so every rect is 0 — which would emit `bottom: <innerHeight>px`
 * (menu off-screen) and `min-width: 0px` (inline, so it beats
 * `.atlas-model-pill__menu{min-width:280px}` — the same override that happens in
 * the real browser, just with a bogus number). We therefore stub the rect on the
 * pill button ONLY, with the geometry the pill actually has inside the 640px
 * `.fr-main` column, so the shipping positioning code runs on realistic input.
 * Nothing else about the DOM is synthesised.
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/composer/live/{closed,model}.html (+ live/fonts/*.woff2)
 * ========================================================================= */
import { createElement as h } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, expect, it } from "vitest";

import {
  ComposerToolsButton,
  OnboardingComposer,
  TransportProvider,
} from "@0x-copilot/chat-surface";

const HERE = (p) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p) => HERE("../surfaces/composer/live/" + p);

/* ── the REAL stylesheets, inlined ─────────────────────────────────────────
 * Every sheet that defines `.aui-composer*` / `.atlas-model-pill*` (grepped),
 * in the cascade order that makes the harness reflect what SHIPS:
 *   1. design-system/styles.css   — tokens, @font-face, .ui-dropdown__menu,
 *                                   .ui-section-label (both hosts load this)
 *   2. apps/frontend/src/styles.css — the WEB host's private copy of the
 *                                   `aui-*` composer chrome + `--chat-content-width`
 *   3. chat-surface/onboarding.css  — the `fr-*` FTUE frame (.fr / .fr-main /
 *                                   .fr-compose / .fr-hero); web loads it in RunRoute
 *   4. chat-surface/composer/composer.css — the PACKAGE SSOT for the composer +
 *                                   model pill. LAST on purpose: it is a superset of
 *                                   the web host's copy (the v3 rows —
 *                                   `__group`, `__badge-lg`, `__nm`, `__rad`,
 *                                   `__footer*` — exist ONLY here), and it is what
 *                                   apps/desktop actually imports
 *                                   (renderer/bootstrap.tsx). NOTE the live-side
 *                                   drift this exposes: apps/frontend never imports
 *                                   composer.css, so on WEB those v3 sub-elements
 *                                   currently have no rules at all.
 * ------------------------------------------------------------------------ */
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

function inlinedCss() {
  return SHEETS.map(
    ([label, rel]) =>
      `/* ===== ${label} ===== */\n${readFileSync(REPO(rel), "utf8")}`,
  ).join("\n\n");
}

/* Harness frame only — mirrors the app window the composer mounts inside. The
 * 640px constraint the design uses is NOT invented here: `.fr-main` is already
 * `width: min(640px, 92%)` in onboarding.css, so a ≥696px frame yields exactly
 * the design's 640px column. */
const FRAME_CSS = `
html, body { margin: 0; height: 100%; background: #050506; }
#frame {
  width: 1040px; height: 720px; display: flex; flex-direction: column;
  background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
  font-family: var(--font-sans); overflow: hidden;
}
`;

function shell(title, composerHtml, portalHtml) {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>${title}</title>
    <style>
${inlinedCss()}
${FRAME_CSS}
    </style>
  </head>
  <body>
    <div id="frame"><div class="fr"><main class="fr-main">${composerHtml}</main></div></div>
${portalHtml}
  </body>
</html>`;
}

/* ── substrate fakes (no I/O; the parity render only needs DOM + classes) ── */
const noop = () => undefined;

const fakeTransport = {
  request: () => Promise.resolve({}),
  subscribeServerSentEvents: () => ({ close: noop }),
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

// Both hosts (apps/frontend RunEmptyComposer, apps/desktop RunEmptyComposer)
// pass a providerKeysPort down to the ModelPill — that is what makes the
// popover's `.atlas-model-pill__footer` ("Add a provider key →") render at all.
const fakeProviderKeys = {
  list: () => Promise.resolve([]),
  save: (provider) =>
    Promise.resolve({
      provider,
      key_hint: "…zzzz",
      updated_at: new Date(0).toISOString(),
    }),
  remove: () => Promise.resolve(),
};

/* Two configured cloud models ("Your keys") + one local ollama model
 * ("Local · on-device"), so both groups and both badge variants render. */
const MODELS = [
  {
    id: "anthropic/claude-sonnet-4-5",
    provider: "anthropic",
    model_name: "claude-sonnet-4-5",
    name: "Claude Sonnet 4.5",
    description: "Balanced default",
    configured: true,
    supports_streaming: true,
    supports_reasoning: true,
  },
  {
    id: "openai/gpt-5.4",
    provider: "openai",
    model_name: "gpt-5.4",
    name: "GPT-5.4",
    description: "Fast cloud model",
    configured: true,
    supports_streaming: true,
  },
  {
    id: "llama3.3:70b",
    provider: "ollama",
    model_name: "llama3.3:70b",
    name: "Llama 3.3 70B",
    description: "On-device",
    configured: true,
    supports_streaming: true,
  },
];
const SELECTED = "anthropic/claude-sonnet-4-5";

/* The web host mounts the tools pill as `ChatToolsTrigger`: a
 * position:relative / display:inline-flex span wrapping <ComposerToolsButton>
 * plus a floated <ToolsPopover>. The popover returns null while closed
 * (ToolsPopover.tsx:130), so the closed-state DOM is exactly this. */
function toolsTrigger() {
  return h(
    "span",
    { style: { position: "relative", display: "inline-flex" } },
    h(ComposerToolsButton, { open: false, onClick: noop, activeCount: 1 }),
  );
}

function mountComposer() {
  return render(
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
        onShowConnectors: noop,
        onOpenSkillsSettings: noop,
        onOpenMcpSettings: noop,
        models: MODELS,
        selectedModel: SELECTED,
        onModelChange: noop,
        providerKeysPort: fakeProviderKeys,
        toolsTrigger: toolsTrigger(),
        onSubmit: noop,
      }),
    ),
  );
}

/** Everything `document.body` holds that is NOT the RTL container — i.e. the
 *  `<Menu>` portal. Re-emitted as a direct <body> child, its runtime home. */
function portalMarkup(container) {
  return Array.from(document.body.children)
    .filter((el) => el !== container)
    .map((el) => el.outerHTML)
    .join("\n");
}

function writeFonts() {
  mkdirSync(LIVE("fonts"), { recursive: true });
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

afterEach(() => cleanup());

it("renders the live composer bottom row (popover closed) to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  writeFonts();

  const { container } = mountComposer();

  // Sanity: the shipping bottom row is actually present before we serialise.
  expect(container.querySelector(".aui-composer")).not.toBeNull();
  expect(
    container.querySelector(".aui-composer-add-attachment"),
  ).not.toBeNull();
  expect(
    container.querySelector('[data-testid="first-run-tools-button"]'),
  ).not.toBeNull();
  expect(container.querySelector(".atlas-composer-mic")).not.toBeNull();
  expect(container.querySelector(".atlas-model-pill")).not.toBeNull();
  expect(container.querySelector(".aui-send-button")).not.toBeNull();
  expect(container.querySelector(".aui-composer__hint")).not.toBeNull();

  writeFileSync(
    LIVE("closed.html"),
    shell(
      "design-parity · composer bottom row · LIVE",
      container.innerHTML,
      portalMarkup(container),
    ),
  );
});

it("renders the live model picker popover (open) to static HTML", () => {
  mkdirSync(LIVE(""), { recursive: true });
  writeFonts();

  const { container } = mountComposer();
  const pill = container.querySelector(".atlas-model-pill");
  expect(pill).not.toBeNull();

  // See ANCHOR GEOMETRY in the header. These numbers are not invented: they are
  // the pill's REAL rect, measured with Playwright on the sibling `closed.html`
  // at the same 1040×720 frame — so the shipping `Menu.computePosition()` runs
  // on the geometry the browser actually produces instead of jsdom's all-zero
  // rect. Re-measure if the bottom row's layout changes:
  //   document.querySelector(".atlas-model-pill").getBoundingClientRect()
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: 720,
  });
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1040,
  });
  const anchorRect = {
    x: 397,
    y: 413,
    left: 397,
    top: 413,
    right: 543,
    bottom: 439,
    width: 146,
    height: 26,
  };
  pill.getBoundingClientRect = () => ({
    ...anchorRect,
    toJSON: () => anchorRect,
  });

  fireEvent.click(pill);

  // The popover portals to <body>; assert it exists and is fully populated.
  const menu = document.body.querySelector(".atlas-model-pill__menu");
  expect(menu).not.toBeNull();
  expect(menu.querySelectorAll(".atlas-model-pill__group-head").length).toBe(2);
  expect(menu.querySelectorAll(".atlas-model-pill__item").length).toBe(3);
  expect(menu.querySelector(".atlas-model-pill__badge-lg")).not.toBeNull();
  expect(menu.querySelector(".atlas-model-pill__nm")).not.toBeNull();
  expect(menu.querySelector(".atlas-model-pill__sub")).not.toBeNull();
  expect(menu.querySelector(".atlas-model-pill__rad")).not.toBeNull();
  expect(menu.querySelector(".atlas-model-pill__footer")).not.toBeNull();
  expect(menu.querySelector(".atlas-model-pill__footer-link")).not.toBeNull();

  writeFileSync(
    LIVE("model.html"),
    shell(
      "design-parity · composer model picker (open) · LIVE",
      container.innerHTML,
      portalMarkup(container),
    ),
  );
});
