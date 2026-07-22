# design-parity

**The design source-of-truth + the CSS-parity toolkit, in one folder.** Any agent
working on a UI surface can (a) grab that surface's committed Claude Design spec as
the build target, and (b) measure how close the live app is by diffing **computed
styles** (not screenshots). Full workflow: [`SKILL.md`](./SKILL.md) (also installed
to `.claude/skills/design-parity/` for Claude Code discovery).

## Layout

```
tools/design-parity/
  design-kit/                 SHARED design source, linked by every surface
    copilot.css               v2 "quiet" tokens + base + window chrome + login + shared primitives
    stubs.js                  minimal Icon/Mark/useTweaks globals for a parity render
    REFRESH.md                how to re-pull from DesignSync (project id + file map)
  lib/
    extract-computed.js       browser-context getComputedStyle walker (anchor-mapped)
    compare.mjs               node diff → severity-ranked report (token-annotated colors)
    render-live.test.tsx      vitest+jsdom renders the REAL app component to static HTML
  surfaces/
    <name>/
      design/                 vendored Claude Design mock (jsx + surface css + index.html harness)
      live/                   (generated) live render — gitignored
      anchors.json            design↔live selector map (+ expectDivergence)
      out/report.md           the parity punch-list
  SKILL.md · README.md
```

## Surfaces

| Surface              | Design spec                  | Parity report                                                                                       | States                                                                                     |
| -------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| **first-run** (FTUE) | `surfaces/first-run/design/` | `surfaces/first-run/out/report.md` (gate: 12 HIGH / 30 MED)                                         | gate ✅ · composer/ack TODO                                                                |
| **login**            | `surfaces/login/design/`     | `surfaces/login/out/report.md` (36 HIGH / 31 MED) + [`FINDINGS.md`](surfaces/login/out/FINDINGS.md) | pick·connecting·sign·done ✅ · werr/gerr/google = design-only (live has no recovery views) |

## Using it for the surface you're working on

- **Building/refining a surface?** Its design target is `surfaces/<name>/design/`. Render it: serve the `design-parity` root and open `surfaces/<name>/design/index.html` (drive states with `?state=`). That's the pixel-exact intent.
- **Checking fidelity?** Run the `design-parity` skill (or the 4 steps in `SKILL.md`) → `out/report.md` is the ranked punch-list.
- **New surface?** `SKILL.md` → "Add a new surface" (vendor the mock, add a live render block, write `anchors.json`).

## Render (both design harnesses)

```bash
# serve from THIS dir so ../../../design-kit resolves
cd tools/design-parity && python3 -m http.server 8099
# open http://127.0.0.1:8099/surfaces/<name>/design/index.html[?state=…]
```

## Installing the skill for Claude Code

`.claude/` is gitignored, so the discovery copy at `.claude/skills/design-parity/SKILL.md`
is machine-local. The canonical, version-controlled skill doc is `tools/design-parity/SKILL.md`.
Reinstall locally: `mkdir -p .claude/skills/design-parity && cp tools/design-parity/SKILL.md .claude/skills/design-parity/`.
