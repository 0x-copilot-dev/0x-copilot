# Run-empty composer — design-parity findings (`composer` state)

**Surface:** the Run cockpit's empty state ("no active run"). The design baseline
is the Claude Design **"What should we run first?"** composer stage
(`copilot-firstrun.jsx` at `?state=key`); the live side is the shared
`OnboardingComposer` the cockpit mounts via `RunDestination.renderEmptyComposer`.

Method: computed-style diff (`getComputedStyle`, headless Chromium), 8 anchors,
`8/8` matched on both sides. Raw report: `report.md`
(🔴 HIGH 9 · 🟠 MEDIUM 23 · 🟡 LOW 11 · ⚪ INFO 2).

## Headline

**The elements this surface introduces — the hero H1 + the starter chips — match
the design.** Every load-bearing computed value is identical; the composer
_chrome_ deltas are an intended architectural divergence, not a regression.

### Hero H1 — MATCH

`fontSize 23px`, `fontWeight 600`, `fontFamily` (system sans), `color
rgb(236,236,241)`, `letterSpacing -0.345px`, `lineHeight 27.6px` all identical
design↔live. The only delta is `margin 0 0 7px → 0` — the live composer spaces
the hero from the chips with the `.fr-compose` flex `gap` instead of a bottom
margin (same visual result, different mechanism). Not a defect.

### Starter chips — MATCH

`fontSize 11px`, `color rgb(212,212,219)`, `background transparent`, `padding 6px
12px`, `border 1px rgba(255,255,255,.06)` all identical. The sole delta is
`border-radius 99px → 999px` — both fully round a ~28px-tall pill, so they are
**visually identical** (a token difference: the mock's `99px` vs the design
system's `--radius-full`). This is the definitive, rendered confirmation of the
chip styling (the adversarial code review flagged then refuted a chip-type drift;
the harness measures no real difference).

## The 9 HIGH / 23 MEDIUM: shared-composer chrome, by design

All HIGH findings and most MEDIUM findings are on `composer.box`,
`composer.textarea`, `composer.send`, and `model.pill`. These compare the design
mock's **bespoke, simplified `.cmp-*` composer** against the live app's **real,
shared `AssistantComposer`** (`aui-*` / `atlas-*`) — the SAME composer the app
mounts in chat and the in-run cockpit. The app deliberately ships one composer
everywhere rather than re-skinning the mock's stand-in per surface, so:

- `composer.send` is a filled accent square in the mock vs the app's accent-soft
  ghost send button; `model.pill` is a transparent mono pill in the mock vs the
  app's bordered `atlas-model-pill`; the textarea/box borders + radii differ by a
  token or two.

None of these are introduced or changed by this work — `RunEmptyComposer` mounts
the unchanged shared composer. They are the shared composer's own (separate)
parity story. `anchors.json` marks `composer.box` / `composer.send` /
`model.pill` `expectDivergence` for this reason.

## Conclusion

The run-empty surface **faithfully reproduces the design's "What should we run
first?" composer stage** for everything it owns (hero + chips). The composer
internals are the app's shared `AssistantComposer` and diverge from the mock's
simplified representation by intent. No parity fix is warranted for this change.
