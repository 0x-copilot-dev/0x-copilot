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

## Toolchain notes

- **Worktrees have no `node_modules`.** Symlink the main checkout's:
  `ln -s <main-checkout>/node_modules node_modules` (it's gitignored). Then vitest,
  and `@0x-copilot/*` workspace resolution, work from the worktree.
- The extractor is browser-agnostic: it runs under the in-app Browser `javascript_tool`,
  Playwright `page.evaluate`, or pasted into DevTools. Only the _rendering_ differs.
- `anchors.json` alignment is by design (the two sides genuinely use different class
  names). Prefer stable anchors: `data-testid`, semantic classes, or `:nth-child`.
- The design mock's `.mw` window chrome + Tweaks panel are mock-only; anchor selectors
  target the surface subtree (`.fr…`), so the frame never pollutes the diff.
- Layout-dependent `width`/`height` are captured but treated as noise (they vary with
  the container); typography/color/spacing/border are container-independent and load-bearing.

## Coverage / TODO

- **first-run**: the **gate** state is fully wired (design + live harness + anchors +
  report). The **composer** and **ack** states need: (a) the design harness `?state=`
  extended (composer needs the vendored `copilot-v3.css` `.cmp`/`.pop` rules; ack needs
  a click-through to `sent`), and (b) the live harness to render those states (drive
  with `@testing-library` `fireEvent`, or pass `initialStage`), then extend `anchors.json`.
- **login**: FULLY WIRED. Design baseline (8 states via `?state=`) + live render
  (`lib/render-live-login.test.tsx` renders `apps/frontend` `LoginScreen`'s `SignInCard`
  with mocked auth/SIWE/EIP-6963 to 6 state HTMLs) + `anchors.json` + `out/report.md`
  (36 HIGH / 31 MED) + `out/FINDINGS.md`. Headline: the live app has NO dedicated
  wallet-error (`werr`) or Google recovery views — errors are an inline `.login-card__error`
  (cleared immediately on wallet failures) and Google is a bare redirect; the design's
  recovery screens have no live analog.
