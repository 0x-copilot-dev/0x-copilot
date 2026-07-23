# surfaces-v2-canvas — parity report (integration-time)

The 0-HIGH computed-style report for the Generative Surfaces v2 canvas tab strip
is an **integration-time** step, not a PR blocker (PRD-B1 UI DoD note: "if it
needs the live app, vendor the baseline + document the visual-parity run as an
integration-time step").

Why deferred: the vendored design mock (`../design/index.dc.html`) is a
**design-compiler template** — it contains `{{ }}` placeholders and `<sc-if>`
tags, so it is not directly renderable to the static HTML the computed-style
extractor reads. The other sibling baselines ship an already-compiled
`design/index.html`; this one ships the raw `.dc.html` (SDR §9 "mirrored
locally") plus its `copilot.css` / `copilot-v3.css` / `support.js`.

## To produce the report (when the design compiler / DesignSync is available)

1. Stage the LIVE side (writes `../live/tabs.html` + `styles.css`):

   ```bash
   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
     lib/render-live-surfaces-v2.test.tsx
   ```

2. Compile the DESIGN side to static HTML (DesignSync, Claude Design project
   `ceb081f6`, the "Generative Surfaces v2" frame — see `../../../SKILL.md`), or
   render `design/index.dc.html` through the design compiler, into
   `../design/index.html`.

3. Serve + extract computed styles for both sides, then compare against the
   mapping in `../anchors.json`:

   ```bash
   cd tools/design-parity && python3 -m http.server 8099   # then extract per SKILL.md
   node lib/compare.mjs <design.json> <live.json> \
     --anchors surfaces/surfaces-v2-canvas/anchors.json \
     --out surfaces/surfaces-v2-canvas/out/report.md
   ```

Target: **0 HIGH**. The active-tab treatment and the pinned-dot color are
pre-declared `expectDivergence` in `anchors.json` (shipped `TcTabs` kit
treatment vs the mock's pill tab) and file as INFO, not HIGH.
