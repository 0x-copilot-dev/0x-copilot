# Live verification — what the packaged app actually showed

Both reported bugs were fixed and merged on **unit-test evidence alone**. This
records what happened when the packaged app was driven the way the reporter
drove it, including the part that is still **not** verified.

**Method:** re-stage the runtime, build the desktop bundle, run
[`g2d_artifact_edit_regressions.py`](../../../tools/desktop-journeys/generative-workflows/g2d_artifact_edit_regressions.py)
against the real supervised app with a real OpenAI key. Journeys run in a
throwaway `userData` subdir, so no real user data was touched.

## The headline: live testing found a second bug that tests could not

**BUG 1 was still broken after the fix that was supposed to fix it** — for a
different reason, which only became reachable _because_ the first fix worked.

Run 1 evidence:

- The dataset surface showed **"This dataset could not be saved. Your in-memory
  cell edits are still here."** with the edited cell (`edited-by-journey`)
  intact and "1 unsaved edit" — screenshot `02-g2d-after-save.png`.
- The run's `ai-backend.log` carried exactly one
  `POST /v1/agent/artifacts/{artifact_id}/revisions` → **422**, not 409.

422 is `ArtifactDigestMismatchError`. `ArtifactSurface` was sending
`expectedDigest: parent.content_digest`, but the server hashes the **incoming**
stream and compares (`artifact_blob_store.py:89-91`). So the digest described
the wrong bytes and **every genuine edit failed its own integrity check**.

This was invisible before, because the sealed-run 409 fired earlier in the same
request. Fixing the causal lane removed the mask and exposed the defect behind
it. Fixed and merged separately (PR #430).

Note what the surface did _not_ say: the old counterfactual "A newer revision
exists" never appeared. The typed-reason work did its job — the message stopped
lying even while the write was still failing.

## Status of each claim

| Claim                                                   | Status                                                                           |
| ------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Dataset artifact renders in the packaged app            | ✅ **verified** — G2A passed end to end with a real provider                     |
| The false "A newer revision exists" message is gone     | ✅ **verified** — absent from the page even when the save failed                 |
| The save 409 (sealed run) is fixed                      | ✅ **verified** — the failure moved to 422, a different cause; no 409 in any run |
| **A cell edit on a completed run actually saves**       | ❌ **NOT VERIFIED** — see below                                                  |
| **"Add one more row" revises instead of re-publishing** | ❌ **NOT VERIFIED** — the journey never reached this assertion                   |

## Why the last two are unverified

Five runs, none of which reached a clean pass:

1. failed at the save — **the real 422 finding above**;
2. the model did not create a dataset artifact at all (live-model tool-choice
   non-determinism, not a code fault);
3. the driver exited during app boot;
4. after the digest fix + a desktop rebuild, failed **earlier** than the save,
   at `_open_artifact_from_sources` — a helper that passes in G2A, so most
   likely timing;
5. the driver control server returned HTTP 500 — environment instability after
   repeated Electron launches.

So the digest fix is **committed, unit-tested, and reasoned from the blob
store's own comparison — but never observed working in the app.** Anyone
reading this should treat BUG 1 as fixed-in-principle and unproven-in-practice
until run 6 passes.

A trap worth recording: the fix appeared not to work until the **desktop bundle
was rebuilt**. `stage.mjs` stages the Python runtime and the frontend dist, not
`apps/desktop/out`. A renderer-only change needs
`npm run build --workspace @0x-copilot/desktop` as well, or the journey silently
exercises the previous renderer and reports a stale result as a regression.

## To finish this

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
npm run build --workspace @0x-copilot/desktop
python3 tools/desktop-journeys/generative-workflows/g2d_artifact_edit_regressions.py
```

Run it from the **main checkout** — the journey resolves `services/ai-backend/.env`
relative to its own path, and the worktree has no `.env`.

The journey asserts facade truth, not DOM opinion: BUG 1 passes only when the
artifact's `current_revision` actually advances, and BUG 2 passes only when the
conversation canvas still holds exactly **one** dataset artifact whose revision
advanced again. Both failure messages name the bug they are re-detecting.
