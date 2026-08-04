# surface-follow-live — switching between artifacts must not raise an alert

**User story.** "I published two artifacts. When I switch between them — both at
their latest version — a bright banner appears saying
`PINNED TO <TAB> · THE RUN HAS MOVED ON` with a `Follow live →` button. The run
finished minutes ago. What is it telling me?"

## Why this journey exists rather than a unit test

The banner's condition (`showFollowLive`) never consulted run status, so it
depended on three things that only meet in a real run:

1. a run that reaches a **terminal** status (unit tests pin `runStatus` by hand),
2. **two or more** real surfaces on the strip, whose order is by
   `lastSeq` — most-recently-**mutated**, not most-recently-created,
3. a **user click** on a tab, which is itself the pin gesture.

The unit suite had a test for this exact interaction and it passed, because it
only ever drove a running run. The defect was a missing term, not a wrong one —
and a missing term is invisible to a test that never enters the state it would
have excluded.

The second defect this journey covers was introduced by the fix and found while
writing the journey: gating only the chip on run status left the tab's pin glyph
rendering with no `onFollowLive` to call. The glyph REPLACES the close button, so
that state is a dead control on a tab the user can then no longer close.

## Steps

1. Sign in, add a BYOK key, ask the agent to publish **two** artifacts.
2. Wait for the run to reach a terminal status.
3. Screenshot the strip with the newest tab active.
4. Click the **older** (non-leftmost) tab — the exact user action in the report.
5. Screenshot again and read the strip out of the live DOM.

## What it asserts

| #   | Claim                                                                                             |
| --- | ------------------------------------------------------------------------------------------------- |
| 1   | The run reached a terminal status (otherwise the journey proves nothing)                          |
| 2   | The strip carries **≥2** tabs, so an older tab exists to switch to                                |
| 3   | Clicking the older tab activates it — switching still works                                       |
| 4   | **No `run-follow-live-banner`** anywhere in the document (the reported defect)                    |
| 5   | **No `tc-tabs-follow-live` chip** — a terminal run has no live tail to follow                     |
| 6   | **No tab reports `data-pinned="true"`**, so no pin glyph renders without its chip                 |
| 7   | Every tab keeps a reachable close button — the pin never silently swallows one                    |
| 8   | **No tab reports `data-live="true"`** — a terminal run cannot land work anywhere                  |
| 9   | The strip's height is unchanged by the click (the banner used to add ~33px and reflow the canvas) |

Claim 9 is measured, not asserted from CSS: the banner's real cost was that a
plain tab click moved the surface the user was reading.

## What blocks fuller coverage

- **The live-pinned state (a pin taken while the run is still producing) is not
  covered here.** It needs a click to land in the window between the second
  surface appearing and the run sealing, which is a race this harness cannot win
  deterministically. It is covered by the unit interaction tests in
  `RunDestination.test.tsx` ("pins on a manual tab click and un-pins via the
  strip's follow-live chip"), where the run status is controlled.
- The model chooses how many artifacts to publish. The journey asserts ≥2 tabs
  and reports what it saw rather than pinning exact titles, so a differently
  worded plan does not turn into a false failure.
