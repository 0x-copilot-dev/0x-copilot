# Design-parity findings — first-run **Ollama runtime states** (PRD-P8)

Design baseline: Claude Design project `ceb081f6`, mock
_"0xCopilot First Run - Ollama States"_ (`copilot-firstrun-ollama.jsx`).
Live: the real `FirstRunLocalCard` from `@0x-copilot/chat-surface`, rendered with
the real `design-system/src/styles.css` + `onboarding.css`.

Measured by computed style, element-for-element, via `lib/extract-computed.js`
in a Chrome page context at a fixed 1440×1000 viewport. Both sides pin the
mock's card geometry (940px stage → 304px card) so the diff is not
viewport-dependent.

## Result

| State             | 🔴 HIGH | 🟠 MED | 🟡 LOW | ⚪ INFO |
| ----------------- | ------- | ------ | ------ | ------- |
| ① not installed   | 0       | 0      | 20     | 12      |
| ① → detected      | 0       | 0      | 8      | 5       |
| ② installed       | 0       | 0      | 11     | 5       |
| ③ downloading     | 0       | 0      | 17     | 10      |
| ④ runtime stopped | 0       | 0      | 18     | 6       |

Every remaining LOW is `width`/`height` (container-dependent — the skill treats
these as noise), an inherited `lineHeight: normal`, or a DOM `tag` change where
the live app is _more_ semantic than the mock (`<div>`→`<section>`,
`<span>`→`<p>`). No LOW describes a visual difference a user could see.

## What the harness actually caught (and we fixed)

The first run was **11 HIGH / 31 MEDIUM**. These were real: several
`onboarding.css` values carried a `/* design-exact */` comment while being
measurably wrong — the foot vocabulary had been eyeballed, not measured.

| Element         | Was                                | Design                                              |
| --------------- | ---------------------------------- | --------------------------------------------------- |
| `.dling`        | 10.5px / 400 / `--mut`, gap 7px    | **11.5px / 600 / `--tx`, gap 8px, line-height 1.4** |
| `.dling svg`    | unsized                            | **13×13**                                           |
| `.dling .spin`  | 11px, 1.5px `--line3`              | **12px, 2px `--line2`**                             |
| `.fr-dep .ok`   | 10.5px / 400, gap 6px, unsized svg | **11.5px / 600, gap 7px, 12×12 svg**                |
| `.fr-dep .acts` | gap 8px                            | **gap 10px**                                        |
| ② action        | no icon                            | **download icon**                                   |

State ③ went from 4 HIGH / 10 MED to **0 / 0**. Unit tests could not have found
any of this — they assert copy and branch selection, not computed style.

## Declared divergences (⚪ INFO, not defects)

Each is traceable to a decision recorded in
`docs/plan/first-run-onboarding/phases/PRD-P8-ollama-runtime-states.md`:

1. **D5 — "4.3 GB", not the mock's "5.6 GB".** No standard Qwen3-4B GGUF quant
   is 5.6 GB; 4.3 GB is the verified Q8_0 size (4,280,404,704 B). Honesty over
   parity. Also shifts the ③ bar from the mock's hard-coded 42% to an honest
   56% (2.4 / 4.3).
2. **D4a-1 — ③ has a "Continue →" action the mock lacks.** The mock auto-advances
   to the composer ~1.4s after detection, so its ③ foot needs no button. D4
   deliberately does not steal the stage, so the user needs a way forward.
3. **D4a-2 — ③'s note tail reads "· downloading in the background"**, not the
   mock's "· type your first prompt while it lands". There is no composer on the
   gate; the mock's line would be false there. The mock's wording is used
   verbatim once the user has advanced.
4. **`.ok` jade.** The mock hard-codes `#6aa88f`; no design-system token equals
   it, so the live rule uses `--color-success` — the same substitution
   `.ln__check` already makes. Surfaces on `color`, `borderColor` and the icon
   (all `currentColor`).
5. **Primary-button ink.** The mock hard-codes `#0b0a0e`; `.gbtn--pri` uses
   `--color-accent-contrast`. **Pre-existing** — verified present at `5c890515`,
   before this branch — and it applies to every FTUE primary button, not just
   these states. Changing it is a separate, surface-wide decision.
6. **Harness artifacts.** Inherited base font (13px vs 13.6px — every text node
   in the card sets its own size, so nothing renders differently), `flex-grow`
   from the mock's five-column catalog chrome, an intentional `flex-wrap` on the
   narrower live card, and `border-radius: 50%` vs `999px` (identical on a
   square).

## Reproducing

```bash
# 1. live fixtures
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
# 2. serve the design-parity root
cd tools/design-parity && python3 -m http.server 8099
# 3. extract both sides for every state (Chrome via playwright channel)
#    design: /surfaces/first-run/design/ollama.html?state=<state>
#    live:   /surfaces/first-run/live/ollama-<state>.html
# 4. compare
node lib/compare.mjs surfaces/first-run/out/design-ollama-<state>.json \
  surfaces/first-run/out/live-ollama-<state>.json \
  --anchors surfaces/first-run/anchors-ollama.json \
  --out surfaces/first-run/out/report-ollama-<state>.md --state <state>
```

Note `detected` is not a `?state=` mode in the mock — it is ① after clicking
"Get Ollama ↗" (the mock flips a local `rt` to `"found"`), so the extractor
drives that click rather than inventing a mode the design does not have.
