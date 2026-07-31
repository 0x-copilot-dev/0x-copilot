/* design-parity · live NARROW STUDIO RAIL render (vitest + jsdom)
 * =========================================================================
 * Renders the REAL Run cockpit Studio rail — `RunWorkspaceRail` hosting
 * `TcChat` + `AssistantComposer` + `ComposerToolsTrigger` + `ModelPill` — at
 * every interesting rail width, into static HTML with the REAL stylesheets
 * inlined, so the browser extractor reads the computed styles the app produces.
 *
 * WHY THIS SURFACE EXISTS
 * The Studio rail is user-resizable down to `MIN_RAIL_WIDTH` (300px), and
 * layout bugs there are invisible to unit tests: jsdom has no layout engine, so
 * nothing in the package suites can see a clipped bubble or a crushed pill. Two
 * shipped bugs lived in exactly that blind spot — a long unbreakable token
 * (path/URL) overflowing the user bubble into an `overflow-x: hidden`
 * transcript, and the composer's left cluster clipping its own Tools/model
 * pills. Both are measured here.
 *
 * BOTH HOSTS, because they load DIFFERENT composer sheets: desktop imports
 * `chat-surface/composer/composer.css`, web keeps a private copy in its own
 * `styles.css`. A fix applied to one only would pass a single-host harness.
 *
 * Run:    node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/run-rail-narrow/live/repro-{desktop,web}.html
 *
 * BEFORE/AFTER: point `BEFORE_SHEETS` at a directory holding the pre-change
 * `composer.css` / `markdown.css` / `frontend-styles.css` (`git show HEAD:…`)
 * to render the same DOM against the old cascade into `*-before.html`.
 * ========================================================================= */
import { createElement as h, Fragment } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, it } from "vitest";

import {
  AssistantComposer,
  ComposerToolsTrigger,
  RunWorkspaceRail,
  TcChat,
  TransportProvider,
} from "@0x-copilot/chat-surface";

const HERE = (p) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p) => HERE("../../../" + p);
const OUT = (p) => HERE("../surfaces/run-rail-narrow/live/" + p);

/* Cascade order per host, from apps/desktop/renderer/bootstrap.tsx and
 * apps/frontend/src/app/App.tsx. The two hosts load DIFFERENT composer sheets,
 * which is exactly why both are measured. */
const HOSTS = {
  desktop: [
    "packages/design-system/src/styles.css",
    "packages/chat-surface/src/composer/composer.css",
    "packages/chat-surface/src/workspace/workspace.css",
    "packages/chat-surface/src/onboarding/onboarding.css",
    "packages/chat-surface/src/messages/markdown.css",
    "packages/chat-surface/src/subagents/subagents.css",
    "packages/chat-surface/src/approvals/approvals.css",
    "packages/chat-surface/src/citations/citations.css",
    "packages/chat-surface/src/thread-canvas/review-surfaces.css",
    "packages/chat-surface/src/thread-canvas/surface-language.css",
    "apps/desktop/renderer/desktop.css",
  ],
  web: [
    "packages/design-system/src/styles.css",
    "packages/chat-surface/src/messages/markdown.css",
    "apps/frontend/src/styles.css",
    "packages/chat-surface/src/subagents/subagents.css",
    "packages/chat-surface/src/approvals/approvals.css",
    "packages/chat-surface/src/citations/citations.css",
    "packages/chat-surface/src/workspace/workspace.css",
  ],
};

/* Set BEFORE_SHEETS to a directory holding the pre-fix
 * composer.css / markdown.css / frontend-styles.css (git show HEAD:…) to render
 * the same DOM against the old cascade — the before half of a before/after. */
const BEFORE = process.env.BEFORE_SHEETS;
const BEFORE_MAP = {
  "packages/chat-surface/src/composer/composer.css": "composer.css",
  "packages/chat-surface/src/messages/markdown.css": "markdown.css",
  "apps/frontend/src/styles.css": "frontend-styles.css",
};

function sheetPath(rel) {
  const swap = BEFORE === undefined ? undefined : BEFORE_MAP[rel];
  return swap === undefined ? REPO(rel) : `${BEFORE}/${swap}`;
}

function inlinedCss(host) {
  return HOSTS[host]
    .map(
      (rel) =>
        `/* ===== ${rel} ===== */\n${readFileSync(sheetPath(rel), "utf8")}`,
    )
    .join("\n\n");
}

const FRAME_CSS = `
html, body { margin: 0; background: #050506; font-family: var(--font-sans); }
#frame { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
.railbox { height: 640px; flex: none; outline: 1px solid #444; }
.railcap { color: #9aa0a6; font: 11px/1.6 monospace; padding-bottom: 4px; }
`;

/* The "app window" frame. Popover placement is clamped against the VIEWPORT
 * (design-system `Menu.computePosition` writes fixed coords), so measuring a
 * dropdown inside a rail that floats in the middle of a wide page proves
 * nothing. This mirrors `ThreadCanvas`'s Studio grid — `minmax(0,1fr) 1px
 * <rail>px` — with the rail hard against the window's right edge, which is
 * where the real cockpit puts it and the only place the clamp actually bites. */
const WINDOW_CSS = `
html, body { margin: 0; height: 100%; background: #050506; font-family: var(--font-sans); overflow: hidden; }
#win { position: fixed; inset: 0; display: grid; background: var(--color-bg, #0e1015); }
#surface { background: var(--color-bg, #0e1015); border-right: 1px solid var(--color-border, #22252e); }
#railcol { min-width: 0; overflow: hidden; }
`;

const noop = () => undefined;

const fakeTransport = {
  request: () => Promise.resolve({ messages: [] }),
  subscribeServerSentEvents: () => ({ close: noop }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "desktop",
    nativeSecretStorage: true,
    fileSystemAccess: true,
    clipboardWrite: true,
    openExternal: true,
  }),
};

const fakeFilePicker = { pick: () => Promise.resolve([]) };

/* The catalog from the bug report's screenshot, so the Tools panel is rendered
 * at a realistic content height rather than empty. */
const CATALOG = [
  ["asana", "Asana"],
  ["atlassian", "Atlassian"],
  ["cloudflare-bindings", "Cloudflare Bindings"],
  ["cloudflare-observability", "Cloudflare Observability"],
  ["github", "GitHub"],
  ["linear", "Linear"],
  ["notion", "Notion"],
].map(([slug, display_name]) => ({
  slug,
  display_name,
  url: `https://mcp.example/${slug}`,
  transport: "streamable_http",
  auth_mode: "oauth",
  description: `Connect ${display_name}`,
  logo_url: null,
  brand_color: null,
  scopes_summary: "read",
  requires_pre_registered_client: false,
  verified: true,
}));

const fakeConnectorsPort = {
  listServers: () => Promise.resolve([]),
  listCatalog: () => Promise.resolve(CATALOG),
  installFromCatalog: () => Promise.reject(new Error("unused")),
  beginAuth: () => Promise.resolve(),
};

function modelsNamed(name) {
  return [
    {
      id: "openai/gpt-5.4-mini",
      provider: "openai",
      model_name: "gpt-5.4-mini",
      name,
      description: "Fast cloud model",
      configured: true,
      supports_streaming: true,
    },
  ];
}

/* The user's actual message from the bug report: one very long unbreakable
 * path token inside otherwise ordinary prose. */
const LONG_PATH =
  "/private/var/folders/0g/rz9_fhp17kz3jl6z74tn0j0h0000gn/T/claude-501/ungranted-geqhbbun";
const MESSAGES = [
  {
    message_id: "m1",
    role: "user",
    parts: [
      {
        type: "text",
        text: `List the files in the directory ${LONG_PATH}. Report exactly what you find. If you cannot read it, say so plainly and do not guess.`,
      },
    ],
    created_at_ms: 1,
  },
  {
    message_id: "m2",
    role: "assistant",
    parts: [
      {
        type: "text",
        text: `I could not read ${LONG_PATH} — permission denied.`,
      },
    ],
    created_at_ms: 2,
  },
];

function railChat(modelName) {
  const toolsTrigger = h(ComposerToolsTrigger, {
    port: fakeConnectorsPort,
    webSearchEnabled: true,
    onToggleWebSearch: noop,
    pausedConnectorIds: [],
    onToggleConnector: noop,
    onConnectCatalog: noop,
    onAddCustom: noop,
  });
  const chat = h(TcChat, {
    conversationId: "c1",
    mode: "studio",
    messages: MESSAGES,
    renderComposer: ({ disabled, placeholder }) =>
      h(AssistantComposer, {
        connectors: { servers: [], loading: false },
        skills: { skills: [], loading: false },
        filePicker: fakeFilePicker,
        renderPlusMenu: () => null,
        skillInstructionPrompt: (n) => n,
        mcpServerInstructionPrompt: (n) => n,
        onShowConnectors: noop,
        onOpenSkillsSettings: noop,
        onOpenMcpSettings: noop,
        toolsTrigger,
        models: modelsNamed(modelName),
        selectedModel: "openai/gpt-5.4-mini",
        onModelChange: noop,
        onSubmit: noop,
        disabled,
        placeholder,
      }),
  });
  return chat;
}

function railAt({ width, modelName = "GPT-5.4 mini", key }) {
  const chat = railChat(modelName);
  return h(
    "div",
    { key: key ?? width },
    h("div", { className: "railcap" }, `${key ?? width} · ${modelName}`),
    h(
      "div",
      {
        className: "railbox",
        style: { width },
        "data-rail-width": width,
        "data-case": key ?? String(width),
      },
      h(RunWorkspaceRail, { mode: "studio", chatSlot: chat }),
    ),
  );
}

const CASES = [
  { width: 300, key: "300" },
  { width: 340, key: "340" },
  { width: 420, key: "420" },
  { width: 584, key: "584-default" },
  { width: 300, key: "300-long-model", modelName: "Claude Sonnet 4.5" },
  {
    width: 300,
    key: "300-local-model",
    modelName: "qwen2.5-coder:32b-instruct",
  },
];

afterEach(() => cleanup());

for (const host of Object.keys(HOSTS)) {
  it(`renders the narrow Studio rail repro (${host} sheets)`, () => {
    mkdirSync(OUT(""), { recursive: true });
    const { container } = render(
      h(
        TransportProvider,
        { transport: fakeTransport },
        h(Fragment, null, ...CASES.map(railAt)),
      ),
    );
    writeFileSync(
      OUT(`repro-${host}${BEFORE === undefined ? "" : "-before"}.html`),
      `<!doctype html>
<html lang="en" data-theme="dark">
<head><meta charset="utf-8" /><title>narrow studio rail repro · ${host}</title>
<style>
${inlinedCss(host)}
${FRAME_CSS}
</style></head>
<body><div id="frame">${container.innerHTML}</div></body>
</html>`,
    );
  });
}

/* One rail, hard against the right edge of a real app window — the layout a
 * dropdown's viewport clamp has to survive.
 *
 * The composer pills' popovers are click-driven `useState`, so a serialized
 * render can never reach them. They are opened here with `fireEvent.click`, the
 * same way `render-live-composer.test.tsx` does — but the anchor rect is the
 * one thing jsdom cannot supply (every rect is 0), and the rect is precisely
 * what `Menu.computePosition()` turns into the panel's fixed coordinates. So
 * the rect is STUBBED with geometry MEASURED from the closed `window-*.html`
 * render in a real browser (1280x800 viewport), and `window.innerWidth/Height`
 * are pinned to that same viewport. The shipping positioning code then runs on
 * true input and the inline `left`/`bottom` it writes is what actually ships.
 * Nothing else about the DOM is synthesised. */
const MEASURED = {
  300: {
    viewport: { w: 1280, h: 800 },
    model: { left: 1114, right: 1233, top: 721, bottom: 747, width: 119 },
    tools: { left: 1032, right: 1109, top: 721, bottom: 747, width: 77 },
  },
  360: {
    viewport: { w: 1280, h: 800 },
    model: { left: 1054, right: 1173, top: 752, bottom: 778, width: 119 },
    tools: { left: 972, right: 1049, top: 752, bottom: 778, width: 77 },
  },
  /* MAX_RAIL_WIDTH. Nothing overflows here, so this case is the no-regression
   * proof: the clamp must leave the panel exactly at the anchor's left edge. */
  760: {
    viewport: { w: 1280, h: 800 },
    model: { left: 654, right: 773, top: 752, bottom: 778, width: 119 },
    tools: { left: 572, right: 649, top: 752, bottom: 778, width: 77 },
  },
};

/* Rendered width of each open panel, also measured in the real browser. The
 * viewport clamp in `Menu.computePosition()` reads the panel's own rendered
 * width off its class (`.atlas-model-pill__menu` is 300px; the Tools panel's
 * 318px inner box plus the popover frame comes to 336px), and jsdom reports 0
 * for that too — so the panel rect is stubbed alongside the anchor rect and a
 * `resize` (which `Menu` already listens for) drives one more position pass on
 * the true numbers. Every input is measured; the arithmetic is the shipping
 * code's. */
const PANEL_WIDTH = { model: 300, tools: 336 };

function stubRect(el, r) {
  el.getBoundingClientRect = () => ({
    x: r.left,
    y: r.top,
    left: r.left,
    right: r.right,
    top: r.top,
    bottom: r.bottom,
    width: r.width,
    height: r.bottom - r.top,
    toJSON: () => ({}),
  });
}

function portalMarkup(container) {
  return [...document.body.children]
    .filter((el) => el !== container)
    .map((el) => el.outerHTML)
    .join("\n");
}

for (const width of [300, 360, 760]) {
  for (const open of ["closed", "model", "tools"]) {
    it(`renders the app-window rail at ${width}px (${open})`, () => {
      mkdirSync(OUT(""), { recursive: true });
      const measured = MEASURED[width];
      globalThis.innerWidth = measured.viewport.w;
      globalThis.innerHeight = measured.viewport.h;

      const { container } = render(
        h(
          TransportProvider,
          { transport: fakeTransport },
          h(RunWorkspaceRail, {
            mode: "studio",
            chatSlot: railChat("GPT-5.4 mini"),
          }),
        ),
      );

      if (open !== "closed") {
        if (open === "model") {
          const pill = container.querySelector(".atlas-model-pill");
          stubRect(pill, measured.model);
          fireEvent.click(pill);
        } else {
          const btn = container.querySelector(
            '[data-testid="first-run-tools-button"]',
          );
          // `ComposerToolsTrigger` anchors the Menu on the WRAPPER span.
          stubRect(btn.parentElement, measured.tools);
          fireEvent.click(btn);
        }
        // Give the panel its real width, then let `Menu`'s own resize listener
        // re-run the shipping placement pass on it.
        const panel = document.querySelector(".ui-dropdown__menu");
        if (panel !== null) {
          const w = PANEL_WIDTH[open];
          const left = Number.parseFloat(panel.style.left) || 0;
          stubRect(panel, {
            left,
            right: left + w,
            top: 0,
            bottom: 200,
            width: w,
          });
          fireEvent(globalThis, new Event("resize"));
        }
      }

      const suffix = BEFORE === undefined ? "" : "-before";
      writeFileSync(
        OUT(`window-${width}-${open}${suffix}.html`),
        `<!doctype html>
<html lang="en" data-theme="dark">
<head><meta charset="utf-8" /><title>studio rail in app window · ${width}px · ${open}</title>
<style>
${inlinedCss("desktop")}
${WINDOW_CSS}
#win { grid-template-columns: minmax(0, 1fr) 1px ${width}px; }
</style></head>
<body><div id="win">
  <div id="surface"></div><div></div>
  <div id="railcol" data-rail-width="${width}">${container.innerHTML}</div>
</div>
${portalMarkup(container)}
</body>
</html>`,
      );
    });
  }
}
