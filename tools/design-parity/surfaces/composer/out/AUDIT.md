# Composer Tools interaction audit

Run on 2026-07-26 after restoring the composer Tools pill.

## Design mock

`lib/audit-composer-tools-design.mjs` drove the vendored composer mock in Chromium.
It verified the closed state, pointer open, the Web Search toggle, Enter open,
Space open, and Escape dismissal. The mock exposes the trigger as a native button
and its small toggle as a separate native button; the Web Search row itself is a
non-focusable `div`.

## Shipping desktop app

`COPILOT_DESKTOP_TEST_TARGET=installed-payload python3 -u
tools/desktop-journeys/composer-tools/tools_popover.py` passed against the packed
npm payload. The journey asserts each of these desktop placements:

- first-run composer;
- bound Run in Studio and Focus;
- New Chat in Studio and Focus.

For every placement it verifies that the Tools pill is present, that the attachment
`+` menu has no duplicate Tools row, opens with a real pointer click, has a visible
and rendered Web Search hit target, toggles its truthful `aria-checked` state, and
closes on click-out. The bound Studio check additionally verifies Enter opens the
pill, Space toggles Web Search, and Escape closes it.

The Focus pass specifically guards the viewport regression found during this audit:
the 318px panel must left-align with the pill rather than open off-screen left.

## Comparable gaps in the design source

The live Web Search row is itself the semantic `button[role=switch]`, so its whole
row is a pointer and keyboard target. The design mock exposes only the small
toggle button and has no switch role. The live body portal uses Menu's document
pointer-dismiss listener rather than a rendered transparent scrim. These are
intentional accessibility/robustness improvements, not visual regressions.
