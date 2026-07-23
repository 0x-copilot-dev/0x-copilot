/* design-parity · live-render harness · first-run Ollama runtime states (PRD-P8 §9)
 * =========================================================================
 * Renders the REAL `FirstRunLocalCard` from @0x-copilot/chat-surface in each of
 * PRD-P8's runtime states, wrapped with the REAL design-system `styles.css` +
 * the FTUE `onboarding.css`, so the browser extractor reads the exact computed
 * styles the shipping app produces. This is the "live" side of the diff; the
 * "design" side is the vendored Claude Design mock
 * (../surfaces/first-run/design/ollama.html?state=<state>).
 *
 * Why the hook result is CONSTRUCTED rather than driven through
 * `useFirstRunLocalModel`: the card is presentational — its `state` prop is a
 * plain value object. Building that object directly is the smallest thing that
 * reaches every state; it needs no ports, no fake SSE server and no fake
 * timers, and it cannot drift from the shipping component because the object is
 * typed as the component's own `UseFirstRunLocalModelResult`. (The hook's own
 * behaviour — probing, polling, auto-start, backoff — is covered by
 * packages/chat-surface/src/onboarding/useFirstRunLocalModel.test.ts; a parity
 * harness must not re-test it.)
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/first-run/live/ollama-<state>.html (+ styles.css/onboarding.css)
 *
 * State map (live file → design URL):
 *   ollama-not-installed.html → ollama.html?state=not-installed   (①)
 *   ollama-detected.html      → ollama.html?state=not-installed,
 *                               then CLICK "Get Ollama ↗"          (① → detected)
 *   ollama-installed.html     → ollama.html?state=installed        (②)
 *   ollama-downloading.html   → ollama.html?state=downloading      (③)
 *   ollama-stopped.html       → ollama.html?state=stopped          (④)
 *
 * `detected` has no `?state=` of its own — the mock reaches its `.ok` line only
 * by clicking "Get Ollama ↗" (`setRt("found")` in the vendored jsx). It is
 * rendered here anyway because it is the ONLY state that puts `.ok` on screen,
 * and `.ok` is an anchored element. It is also the one state that needs a
 * TRANSITION rather than a single render: the card distinguishes "① → detected"
 * from "② was already running" by card-local memory of having seen a
 * non-running runtime, so the fixture renders ① and then re-renders at
 * `runtime: "running"`. Hence @testing-library `render`/`rerender` throughout
 * rather than `renderToStaticMarkup` — one mechanism for all five states.
 * ========================================================================= */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import {
  FirstRunLocalCard,
  QWEN3_4B_PRESET,
  type UseFirstRunLocalModelResult,
} from "@0x-copilot/chat-surface";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/first-run/live/" + p);

const noop = (): void => undefined;

/** Neutral, feature-enabled baseline; each fixture overrides only its own axes. */
const BASE: UseFirstRunLocalModelResult = {
  enabled: true,
  runtime: "unknown",
  runtimeManaged: false,
  phase: "idle",
  modelInstalled: false,
  localModelPct: null,
  bytesCompleted: null,
  bytesTotal: null,
  blocked: null,
  modelName: null,
  disabled: false,
  start: noop,
  resume: noop,
  restartRuntime: noop,
  recheck: noop,
};

// ③ byte line. The mock hardcodes "2.4 / 5.6 GB"; ours reads "2.4 / 4.3 GB"
// because PRD-P8 D5 freezes the preset at the VERIFIED Qwen3-4B Q8_0 size
// (4,280,404,704 B). Completed is a real 2.4 GB so `formatBytesPair` renders the
// pair rather than dropping the segment, and the bar's percentage is the honest
// completed/total — NOT the mock's hardcoded 42%, which is 42% of a 5.6 GB total
// that does not exist. Both deltas are declared in anchors-ollama.json.
const BYTES_TOTAL = QWEN3_4B_PRESET.sizeBytes ?? 4_280_404_704;
const BYTES_DONE = 2_400_000_000;
const PCT = Math.round((BYTES_DONE / BYTES_TOTAL) * 100); // 56

interface Fixture {
  /** Output file stem: surfaces/first-run/live/ollama-<file>.html */
  readonly file: string;
  /** Why this fixture is shaped the way it is (emitted into the HTML). */
  readonly note: string;
  /**
   * Renders in order; only the LAST is serialized. More than one entry means
   * the state is reachable only through a transition (see `detected`).
   */
  readonly steps: readonly UseFirstRunLocalModelResult[];
  /**
   * `data-testid`s that MUST be in the final render — the anchored elements the
   * report for this state is built from. Without them this file is a fixture
   * generator with no assertions: the card's foot is chosen by a subtle
   * precedence chain (capability → probing → ready → ④ → ③ → ①), so a
   * regression that swapped two branches would emit a green, wrong-state
   * baseline and every downstream anchor would silently measure the wrong
   * element. Asserting the marker makes the harness fail loudly instead.
   */
  readonly markers: readonly string[];
  /** Optional copy that must appear — pins a DECLARED divergence's live half. */
  readonly text?: string;
  /**
   * The optional props are OMITTED-MEANS-NO-BUTTON by design — the card never
   * renders a control that cannot work. Each fixture passes what a CORRECTLY
   * WIRED host passes in that state, so the report measures the intended
   * surface rather than a degraded one. It follows that this harness cannot
   * detect an UNWIRED host (it supplies the callbacks itself) — that is what
   * the host tests (FirstRunGate / FirstRunSurfaceMount) are for.
   */
  readonly onContinue?: () => void;
  readonly onGetOllama?: () => void;
}

const NOT_INSTALLED: UseFirstRunLocalModelResult = {
  ...BASE,
  runtime: "not_installed",
};

const FIXTURES: readonly Fixture[] = [
  {
    // ① Ollama not installed — "Get Ollama ↗" + the watch line.
    file: "not-installed",
    note: "① runtime absent; the card polls, so the watch line replaces a Re-check button",
    steps: [NOT_INSTALLED],
    markers: ["first-run-local-watch", "first-run-local-get-ollama"],
    onGetOllama: noop,
  },
  {
    // ① → detected — the runtime we were watching came up; the hook auto-starts
    // the pull, so the foot states that instead of offering a button that would
    // race it. Needs the ① render first (that is what arms the card's memory).
    file: "detected",
    note: "① → detected — the `.ok` settled-good line, reached by a runtime edge",
    steps: [NOT_INSTALLED, { ...BASE, runtime: "running" }],
    // Guards the transition itself: without the ① step first this renders ②'s
    // Start button, and the `.ok` anchor would resolve to nothing.
    markers: ["first-run-local-detected"],
  },
  {
    // ② Ollama installed, model absent — the design's explicit Start button.
    file: "installed",
    note: "② runtime running and already was at first probe — explicit Start download",
    steps: [{ ...BASE, runtime: "running" }],
    markers: ["first-run-start-download"],
  },
  {
    // ③ downloading — spinner + bar + byte line + D4a-1 "Continue →".
    file: "downloading",
    note: "③ pull in flight; `onContinue` is D4a-1, which the mock has no analog for",
    steps: [
      {
        ...BASE,
        runtime: "running",
        phase: "downloading",
        localModelPct: PCT,
        bytesCompleted: BYTES_DONE,
        bytesTotal: BYTES_TOTAL,
      },
    ],
    markers: [
      "first-run-local-progress",
      "first-run-local-bar",
      "first-run-local-note",
      "first-run-local-continue",
    ],
    // Pins the live half of the D5 + D4a-2 note declaration: if `formatBytesPair`
    // or the preset total ever changed, the anchor's "expected: 2.4 / 4.3 GB"
    // reason would quietly become a lie the report still files as INFO.
    text: "Qwen 3 4B · 2.4 / 4.3 GB · downloading in the background",
    onContinue: noop,
  },
  {
    // ④ runtime stopped — amber line + "Restart Ollama" + resume watch line.
    // `runtimeManaged: true` is the DESKTOP posture; the card deliberately hides
    // Restart where the server may not manage the runtime (web / containerised),
    // and the mock only ever draws the managed variant.
    file: "stopped",
    note: "④ daemon died mid-pull; progress is kept, Restart renders (runtimeManaged)",
    steps: [
      {
        ...BASE,
        runtime: "stopped",
        runtimeManaged: true,
        localModelPct: PCT,
        bytesCompleted: BYTES_DONE,
        bytesTotal: BYTES_TOTAL,
      },
    ],
    markers: [
      "first-run-local-stopped",
      "first-run-local-stopped-msg",
      "first-run-local-restart",
      "first-run-local-stopped-watch",
    ],
  },
];

/**
 * Wrap a card's markup in the REAL surface chain the app mounts it in
 * (`.fr` → `.fr-main` → `.fr-gate`), inside a desktop-sized dark frame, so the
 * card resolves the same grid column and inherited type the shipping FTUE gives
 * it. Typography/color/border/padding do not depend on the container;
 * width/height are layout noise.
 */
function shell(title: string, inner: string): string {
  return `<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <title>design-parity · first-run · Ollama ${title} · LIVE</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="./styles.css" />
    <link rel="stylesheet" href="./onboarding.css" />
    <style>
      html, body { margin: 0; height: 100%; background: #050506; }
      #frame {
        width: 1040px; height: 720px; display: flex; flex-direction: column;
        background: var(--color-bg, #09090b); color: var(--color-text, #ececf1);
        font-family: var(--font-sans); overflow: hidden;
      }
      /* onboarding.css folds .fr-gate to ONE column under a 560px VIEWPORT.
         The extractor's viewport is not ours to control (a headless context can
         report width 0, which MATCHES that query), and a folded gate hands the
         card the full 640px column instead of its real ~315px one - which is
         what decides text wrapping and .acts flex-wrap. Pin the two-column
         desktop layout so the fixture is viewport-independent. */
      #frame .fr-gate { grid-template-columns: 1fr 1fr; }
    </style>
  </head>
  <body>
    <div id="frame">
      <div class="fr">
        <div class="fr-main">
          <div class="fr-gate">${inner}</div>
        </div>
      </div>
    </div>
  </body>
</html>`;
}

afterEach(cleanup);

it("renders the live first-run local card in every PRD-P8 runtime state", () => {
  mkdirSync(LIVE(""), { recursive: true });
  // Copy the REAL stylesheets next to the harness so the browser serves them.
  // (render-live.test.tsx copies the same two files; the write is idempotent and
  // repeated here so this harness stands alone whichever test runs first.)
  copyFileSync(
    REPO("packages/design-system/src/styles.css"),
    LIVE("styles.css"),
  );
  copyFileSync(
    REPO("packages/chat-surface/src/onboarding/onboarding.css"),
    LIVE("onboarding.css"),
  );

  for (const fixture of FIXTURES) {
    const card = (state: UseFirstRunLocalModelResult) =>
      h(FirstRunLocalCard, {
        state,
        preset: QWEN3_4B_PRESET,
        onStartDownload: noop,
        onContinue: fixture.onContinue,
        onGetOllama: fixture.onGetOllama,
      });

    const [first, ...rest] = fixture.steps;
    const { container, rerender, unmount } = render(card(first));
    for (const step of rest) rerender(card(step));

    // The baseline is only worth measuring if it is the state it claims to be.
    for (const marker of fixture.markers) {
      expect(
        container.querySelector(`[data-testid="${marker}"]`),
        `${fixture.file}: expected [data-testid="${marker}"] in the rendered foot`,
      ).not.toBeNull();
    }
    if (fixture.text !== undefined) {
      expect(container.textContent).toContain(fixture.text);
    }

    writeFileSync(
      LIVE(`ollama-${fixture.file}.html`),
      shell(fixture.file, `<!-- ${fixture.note} -->${container.innerHTML}`),
    );
    unmount();
  }
});
