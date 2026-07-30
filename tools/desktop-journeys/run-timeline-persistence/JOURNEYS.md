# run-timeline-persistence

Live proof for two coupled Run-cockpit fixes. Both concern the strip at the very
bottom of the canvas (the bead timeline + `Live` pill) and the band just above it.

| Bug                                 | Symptom the user reported                             |
| ----------------------------------- | ----------------------------------------------------- |
| **1 — the strip vanished on send**  | "why does the following bar vanish during execution?" |
| **2 — `Listening for run events…`** | "waste of space"                                      |

## Why a screenshot alone is not proof

Bug 1's gap is transient — it lasts from the moment a send starts a new run until
that run's first event lands (**measured at 559ms** on a pre-fix build). A
screenshot on a timer is a coin flip, and a lucky "looks fine" frame proves
nothing. So the evidence here is **frame counting**, with screenshots as the
human-readable companion.

## Scripts

### `timeline_persists.py` — the assertion

Installs a 50ms DOM sampler **before** each send and reads the samples back
afterwards. Every sampled frame where the cockpit is mounted must show the strip
and the pill, and must not show the removed line. One bad frame fails the run.

It also fails a phase that never observed a **zero-bead frame**, since such a
phase never reached the vanishing condition and would be vacuously green.

Steps: sign in → FTUE add key → wait for the model pill to resolve → send (phase 1)
→ switch to Studio and re-send (phase 2, the exact repro) → Focus (phase 3).

| Build   | Result                                                                    |
| ------- | ------------------------------------------------------------------------- |
| pre-fix | FAIL — strip absent 7/248 frames; `Listening…` visible **248/248** frames |
| fixed   | PASS — 0 absent frames across 16 zero-bead frames; line never rendered    |

### `catch_gap.py` — the photograph

A 20ms in-page watcher latches the instant the strip goes missing; the driver
polls the latch and fires a screenshot the moment it trips, then reports the gap
duration. On a fixed build there is nothing to catch, so it takes an
unconditional mid-send frame instead, giving a comparable "after" image.

| Build   | Result                                                  |
| ------- | ------------------------------------------------------- |
| pre-fix | `GAP OBSERVED` — 28 frames, **559ms**, caught on camera |
| fixed   | `NO GAP` — the strip stayed mounted across the send     |

## testIds asserted

- `tc-mini-timeline-slot` — the strip. Must be present in every canvas frame.
- `tc-mini-timeline-now` — the `Live` pill. Had its own second gate; same rule.
- `tc-swimlanes-empty` — the removed empty state. Must never appear.
- `thread-canvas[data-mode]` — distinguishes a real cockpit frame from a
  navigation frame with no opinion about the strip.

Plus a visible-text scan for `Listening for run events`, so the assertion holds
even if that testId were renamed — the point is the absence of a **user-visible
line**, not of an attribute.

## Running

```bash
python3 tools/desktop-journeys/run-timeline-persistence/timeline_persists.py
python3 tools/desktop-journeys/run-timeline-persistence/catch_gap.py
```

From a worktree, point the harness at the checkout that owns the local `.env`
and at main's staged services (these are frontend-only changes — no re-stage):

```bash
COPILOT_JOURNEY_DOTENV=/path/to/main/services/ai-backend/.env \
COPILOT_HOME=/path/to/main/apps/desktop/resources \
  python3 tools/desktop-journeys/run-timeline-persistence/timeline_persists.py
```

## Reproducing the "before"

Both bugs live entirely in four files, so a pre-fix build is a checkout away:

```bash
git checkout <pre-fix-sha> -- \
  packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx \
  packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx \
  packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx \
  packages/chat-surface/src/thread-canvas/TcSwimlanes.styles.ts
npm run build --workspace @0x-copilot/desktop
# …run either script, then: git checkout HEAD -- packages/
```
