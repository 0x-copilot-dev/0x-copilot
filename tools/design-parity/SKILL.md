---
name: design-parity
description: Measure pixel/CSS parity between a Claude Design mock and the live app by diffing COMPUTED STYLES (not screenshots). Renders both sides to HTML, extracts getComputedStyle for a curated visual-property set per mapped element, and emits a severity-ranked report. Use when asked to check design fidelity / "does the built UI match the design", to find why a surface "looks off", or to produce a parity punch-list before/after a UI change.
---

# design-parity

Compare a **design mock** (Claude Design / DesignSync) against the **live app** by
diffing _computed styles_, element-for-element. Screenshots are unreliable to read;
`getComputedStyle` gives exact, measurable facts — font-size, color (as a design
token), padding, border, gap, weight — so "looks off" becomes a ranked punch-list.

Tooling lives in `tools/design-parity/` (committed, reusable). The first wired
surface is the first-run FTUE (`surfaces/first-run/`); add more the same way.

## When to use

- "Does the built `<surface>` match the design?" / "check design fidelity".
- A surface was reported as low-fidelity and you need to know _exactly_ what drifted.
- Before/after a CSS refactor, to prove parity didn't regress.

## The pipeline (4 steps)

```
 DESIGN mock ─render→ HTML ─┐
                            ├─ extract computed styles (lib/extract-computed.js)
 LIVE component ─render→ HTML┘        │
                                      ▼
                          compare (lib/compare.mjs) → out/report.md + report.json
```

Both sides render to a URL; the SAME browser-context extractor reads each; a pure-node
comparator diffs them by an explicit anchor map (design and live use different class
names for the same element, so alignment is a hand-authored `{label, design, live}` map).

### 1. Get the design baseline (once per surface)

Vendor the surface's design source with the `DesignSync` tool (`get_file`) into
`surfaces/<name>/design/`: the component `.jsx` and any surface-specific `.css`. The
shared tokens/base (`design-kit/copilot.css`) + kit stubs (`design-kit/stubs.js`)
already exist — link them with `../../../design-kit/…`. Build a self-contained
`index.html` (shared kit + surface css + React/Babel UMD from CDN + the `.jsx`) that
renders states via a `?state=` query param. See `surfaces/first-run/design/` (stub
`useTweaks` drives state) or `surfaces/login/design/` (a `?state=`-seeded initial
view). Refresh shared source via `design-kit/REFRESH.md`.

### 2. Render the live surface

`lib/render-live.test.tsx` is a vitest+jsdom harness that renders the REAL
`@0x-copilot/chat-surface` component with fake ports, via `renderToStaticMarkup`, and
wraps the DOM with the REAL `design-system/src/styles.css` + the surface stylesheet
(e.g. `onboarding.css`). This is the shipping DOM + shipping CSS — no re-authoring.

```bash
# from the repo root; worktrees need node_modules first (see Toolchain below)
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
```

### 3. Extract computed styles (both sides)

Serve the **design-parity root** over HTTP (Babel loads `src=` via XHR — `file://`
won't work; the design harness links `../../../design-kit`, so serve from the root,
not the surface dir):

```bash
cd tools/design-parity && python3 -m http.server 8099
# design: http://127.0.0.1:8099/surfaces/<name>/design/index.html[?state=…]
# live:   http://127.0.0.1:8099/surfaces/<name>/live/<state>.html
```

Open each in a browser (the in-app **Browser** tool, or Playwright/DevTools), then run
`lib/extract-computed.js` in the page context and pass the anchor list. It returns
`{label -> {tag, classes, text, styles}}`. Save each side to
`surfaces/<name>/out/{design,live}-<state>.json`.

With the in-app Browser: `navigate` to the URL, then `javascript_tool` with the
contents of `lib/extract-computed.js` followed by
`JSON.stringify(__extractParity({elements:[{label,selector},…]}))`.

### 4. Compare

```bash
node lib/compare.mjs \
  surfaces/<name>/out/design-<state>.json \
  surfaces/<name>/out/live-<state>.json \
  --anchors surfaces/<name>/anchors.json \
  --out surfaces/<name>/out/report.md --state <state>
```

`out/report.md` is a table ranked 🔴 HIGH / 🟠 MEDIUM / 🟡 LOW / ⚪ INFO. Colors are
annotated with their design-token name (`rgb(236,236,241) (--tx)`), so a token swap
reads directly. INFO covers expected divergences (declared in `anchors.json` via
`expectDivergence`) + copy differences.

### Declaring an expected divergence

`expectDivergence` on an anchor takes two forms:

```jsonc
"expectDivergence": "reason"                    // PRESENCE, either direction
"expectDivergence": { "absent": "…", "extra": "…", "text": "…", "color": "…" }
```

The object form is **scoped**: `absent`/`extra` are the two presence directions,
`text` is a copy difference, and any other key is a computed-style property whose
diff is expected. Only the declared keys drop to INFO — every other property on that
element still scores normally, so one intended delta can never launder a whole
element's drift. The reason string is printed in the report next to the measured
delta, so an INFO row still says exactly what differs and why.

**Get this right or the report is worthless.** A deliberate product decision filed as
a defect wastes the reader's time; drift filed as intent hides a real bug. Cite the
decision (PRD §, ADR, a code comment) in the reason — if you cannot cite one, it is
drift.

## Severity model (in `lib/compare.mjs`)

- 🔴 **HIGH** — wrong typeface class (mono↔sans), font-size Δ ≥ 2px, any color/token swap, an element present in design but absent in live (unless `expectDivergence`).
- 🟠 **MEDIUM** — font-size Δ 0.5–2px, font-weight change, padding/margin/gap/border-radius/border-width change, a layout property change (display/flex/align/justify/flex-grow).
- 🟡 **LOW** — sub-0.5px line-height/letter-spacing, DOM tag change (`<b>`→`<h2>`).
- ⚪ **INFO** — copy text differences, declared expected divergences, live-only extras.

Tune thresholds/weights in `classify()`.

## Add a new surface

1. `DesignSync get_file` the mock's jsx (+ any surface-specific css) → `surfaces/<name>/design/`, build `index.html` linking `../../../design-kit/copilot.css` + `../../../design-kit/stubs.js` (shared). Refresh shared tokens via `design-kit/REFRESH.md`.
2. Add a render block to `lib/render-live.test.tsx` (or a new test) that mounts the live component with fake ports and writes `surfaces/<name>/live/<state>.html`.
3. Author `surfaces/<name>/anchors.json` — the `{label, design, live}` map (+ `expectDivergence` for known-good deltas). Tip: render the live side first, outline its DOM/classes, then write the live selectors.
4. Run steps 3–4 above.

## Delegate to an agent (copy-paste prompt)

To have an agent produce a parity report for a surface, fill the four `<…>` blanks
and hand it this. It's self-contained — the agent loads this skill and follows it.

```
Run a design-parity check for the <SURFACE> surface (e.g. "settings", "add-keys").
Repo root: <ROOT>. Load the `design-parity` skill and follow its 4 steps; commit to
a branch when done. Do NOT run npm/rm against the MAIN checkout's node_modules.

1. DESIGN: DesignSync get_file from project 73f810d9-7b77-4849-9087-f7f8e366c48a —
   <DESIGN JSX/CSS files> → tools/design-parity/surfaces/<SURFACE>/design/. Build an
   index.html linking ../../../design-kit/copilot.css + ../../../design-kit/stubs.js
   (copilot.css already has most component styles; if a class is missing, fetch that
   section per design-kit/REFRESH.md). Drive multiple states with ?state= if needed.
2. LIVE: write tools/design-parity/lib/render-live-<SURFACE>.test.tsx that renders the
   REAL app component <LIVE COMPONENT PATH> with faked ports/context (crib the mocking
   from the nearest existing *.test.tsx next to it), wrapped with the real
   packages/design-system/src/styles.css + the surface's stylesheet, writing
   surfaces/<SURFACE>/live/<state>.html. Add the file to vitest.config.mjs `include`.
   Run it from the MAIN checkout (has node_modules):
   (cd <MAIN> && node_modules/.bin/vitest run --config <ROOT>/tools/design-parity/vitest.config.mjs)
3. EXTRACT: cd tools/design-parity && python3 -m http.server 8099. Open each side in a
   browser; run lib/extract-computed.js in the page with the anchor list; save both to
   surfaces/<SURFACE>/out/{design,live}-<state>.json.
4. COMPARE: write surfaces/<SURFACE>/anchors.json (design↔live selector map), then
   node lib/compare.mjs surfaces/<SURFACE>/out/design-<state>.json
   surfaces/<SURFACE>/out/live-<state>.json --anchors surfaces/<SURFACE>/anchors.json
   --out surfaces/<SURFACE>/out/report.md. Report HIGH/MED counts + any missing screens.
```

Source hints for common surfaces:

| Surface             | DesignSync file(s)                                                                                                                 | Live component                                                                                                                               |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **settings**        | `copilot-settings.jsx` + `settings.css` (`.set-*`)                                                                                 | `packages/chat-surface/src/settings/SettingsSurface.tsx` (+ section bodies)                                                                  |
| **add-keys** (BYOK) | it's the `.fr-kf` block in `copilot-firstrun.jsx` (FTUE key form) **or** the Settings Provider-keys card in `copilot-settings.jsx` | `packages/chat-surface/src/onboarding/KeyForm.tsx` (FTUE) **or** `settings/.../ProviderKeysPage` (Settings) — pick which "add keys" you mean |
| **loading / boot**  | `copilot-loading.jsx` (`.boot-*`)                                                                                                  | `apps/desktop` boot screen                                                                                                                   |

Heavier surfaces (settings/run cockpit) also need `copilot-v3.css` (`.cmp`/`.pop`/`.ws3`)
in `design-kit/` — fetch it per `design-kit/REFRESH.md` and link it in the harness.

## Toolchain notes

- **Worktrees have no `node_modules`.** Symlink the main checkout's:
  `ln -s <main-checkout>/node_modules node_modules` (it's gitignored). Then vitest,
  and `@0x-copilot/*` workspace resolution, work from the worktree.
  - ⚠️ **DANGER — clean the symlink up carefully.** While this symlink exists,
    prettier writes its cache through it and can create a symlink cycle
    (`node_modules/.cache/prettier` → …), which then makes commits fail with
    `ELOOP: too many symbolic links`. Worse, a careless `rm -rf` around the symlink
    can leave the MAIN checkout's `node_modules` as a **self-referential symlink**
    (`node_modules -> node_modules`), nuking the real dir (recover with `npm install`).
    Safe removal: `rm node_modules` (plain `rm`, no `-rf`, no trailing slash — it's a
    symlink; `rm -rf node_modules/` would delete the TARGET's contents). If a commit
    hits `ELOOP`, `ls -ld <main>/node_modules` — if it's a self-symlink, `rm` it and
    `npm install`. Prefer running vitest from the MAIN checkout to avoid all this.
- The extractor is browser-agnostic: it runs under the in-app Browser `javascript_tool`,
  Playwright `page.evaluate`, or pasted into DevTools. Only the _rendering_ differs.
- `anchors.json` alignment is by design (the two sides genuinely use different class
  names). Prefer stable anchors: `data-testid`, semantic classes, or `:nth-child`.
- The design mock's `.mw` window chrome + Tweaks panel are mock-only; anchor selectors
  target the surface subtree (`.fr…`), so the frame never pollutes the diff.
- Layout-dependent `width`/`height` are captured but treated as noise (they vary with
  the container); typography/color/spacing/border are container-independent and load-bearing.
- **Pin the layout on BOTH sides — the extraction viewport is not yours.** A headless
  context reports `innerWidth: 0`, so `html`/`body` are 0px wide: every `max-width`
  media query matches (you silently measure the mobile layout) and every
  percentage-derived width collapses. Measured, not assumed: the design mock at
  `innerWidth: 0` rendered `.mw` at **2px** and `.ol-main` at **0px**, leaving the card
  overflowing at 166px with its watch line wrapped to 4 rows. Pin the geometry you mean
  to measure in each side's own `<style>` block:
  - live — `render-live-ollama.test.tsx` pins the gate to two columns, so the card
    resolves its real 315px column instead of a folded 640px one;
  - design — `surfaces/first-run/design/ollama.html` pins `.mw` / `.ol-main` / `.ol-grid`
    (with `!important`, to beat the harness block's inline styles) to the mock's catalog
    geometry, so the card is a deterministic 304px in single-card mode too.

  Both were verified at `innerWidth: 0`. `lib/render-live.test.tsx` (the gate harness)
  has the same latent exposure and is deliberately NOT changed — that would invalidate
  the committed gate baseline; re-pin it when that baseline is next regenerated.

- **No backticks inside the harness's inlined `<style>`/HTML comments.** `shell()` is a
  JS template literal; a stray backtick in a CSS comment terminates it and the failure
  surfaces as an unrelated `ReferenceError`.

## Coverage / TODO

- **first-run · gate**: fully wired (design + live harness + anchors + report).
- **first-run · Ollama runtime states (PRD-P8)**: BOTH SIDES RENDERABLE + ANCHORED;
  extract/compare not yet run, so there is no `out/report-ollama-*.md` yet.
  - Design: `surfaces/first-run/design/ollama.html?state=…` renders exactly ONE card
    for `not-installed` · `installed` · `downloading` · `stopped` (verified). The mock's
    `.ok` line has no `?state=` — reach it from `?state=not-installed` by clicking
    `Get Ollama ↗`. `?state=fail` is the DROPPED "Download failed" state (PRD-P8 D1)
    and is deliberately unmapped.
  - Live: `lib/render-live-ollama.test.tsx` renders the REAL `FirstRunLocalCard` to
    `surfaces/first-run/live/ollama-{not-installed,detected,installed,downloading,stopped}.html`
    by constructing the hook result directly (the card is presentational — no ports, no
    fake SSE, no fake timers). `detected` is the one state that needs a TRANSITION, so
    the harness uses `render`/`rerender`.
  - Anchors: `surfaces/first-run/anchors-ollama.json` (SEPARATE from `anchors.json` —
    per-state labels would collide with the gate's `card.local.*`). **Filter `elements`
    by the `state` field** when building an extraction spec; a few selectors legitimately
    match in more than one state.
  - Declared-intent deltas (do NOT re-file as defects): D5 4.3 GB vs the mock's 5.6 GB ·
    D4a-1 the live ③ "Continue →" · D4a-2 the ③ note tail · the `.ok` colour
    (`--color-success` vs the mock's literal `#6aa88f`). Everything else scores normally —
    including the ③ `.dling` type/weight/colour and the ② Start button's missing leading
    icon, which ARE drift.
  - TODO: run steps 3–4 per state → `out/design-ollama-<state>.json` /
    `out/live-ollama-<state>.json` / `out/report-ollama-<state>.md`. Two known blind
    spots are recorded in `anchors-ollama.json`'s `blindSpots` (both concern the
    `.watch` dot, which is a pseudo-element live and an unstyled 0×0 span in the mock).
- **first-run · composer / ack**: still TODO — (a) extend the design harness `?state=`
  (composer needs the vendored `copilot-v3.css` `.cmp`/`.pop` rules; ack needs a
  click-through to `sent`), (b) render those states live (`fireEvent`, or an
  `initialStage` prop), then extend `anchors.json`.
- **login**: FULLY WIRED. Design baseline (8 states via `?state=`) + live render
  (`lib/render-live-login.test.tsx` renders `apps/frontend` `LoginScreen`'s `SignInCard`
  with mocked auth/SIWE/EIP-6963 to 6 state HTMLs) + `anchors.json` + `out/report.md`
  (36 HIGH / 31 MED) + `out/FINDINGS.md`. Headline: the live app has NO dedicated
  wallet-error (`werr`) or Google recovery views — errors are an inline `.login-card__error`
  (cleared immediately on wallet failures) and Google is a bare redirect; the design's
  recovery screens have no live analog.
- **run-empty**: FULLY WIRED (`lib/render-live-run-empty.test.tsx` + `anchors.json` +
  `out/report.md`, 9 HIGH / 23 MED, + `out/FINDINGS.md`).
