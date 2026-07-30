# Artifact editing — independent verification notes

Written by a verification pass over the working tree at `a603d881` (descendant of
`6d515523`), after three agents implemented PRD-03 D1/D2 and PRD-04 D4. Nothing
here is copied from their reports: every number below was measured by running the
command, and every claim about a guard was checked by deleting the guard and
watching a test fail.

## Suite numbers I measured myself

| Suite                                               | Command                                    | Result                                       |
| --------------------------------------------------- | ------------------------------------------ | -------------------------------------------- |
| ai-backend unit                                     | `pytest tests/unit -q`                     | **7048 passed, 97 skipped** (7145 collected) |
| ai-backend evals (default, `evals` marker excluded) | `pytest tests/evals -q`                    | **37 passed, 2 deselected**                  |
| ai-backend evals (live arm)                         | `pytest tests/evals -m evals -q`           | **2 skipped, 37 deselected** (no model env)  |
| ai-backend evals (publication family only)          | `pytest tests/evals/publication -q`        | **28 passed, 1 deselected**                  |
| chat-surface                                        | `vitest run --root packages/chat-surface`  | **3333 passed, 1 failed** (315 files)        |
| chat-surface typecheck                              | `tsc --noEmit -p tsconfig.json`            | **exit 0, no output**                        |
| chat-surface lint (`src/artifacts`)                 | `eslint src/artifacts`                     | **exit 0**                                   |
| format                                              | `prettier@3.8.3 --check .../src/artifacts` | **clean**                                    |

The single chat-surface failure is `src/destinations/run/canvasLifecycle.test.ts`
("differentially matches the Python fold"), which fails identically on an
untouched checkout because its runner wants a PYTHONPATH this environment does
not set. It is pre-existing and not attributable to this work.

### Did the ai-backend count go up?

Yes, by **+98 tests**, and the accounting is exact rather than inferred. Only two
test files under `tests/unit` changed:

- `tests/unit/agent_runtime/artifacts/test_execution_mode.py` — new, **70** tests.
- `tests/unit/agent_runtime/artifacts/test_artifact_service.py` — **28 → 56**
  (I collected the `HEAD` copy of the file to get the 28).

`tests/unit/runtime_adapters/_artifact_fixtures.py` also changed but defines no
tests. So the pre-change tree collected 7047 (6950 passed, 97 skipped).

**The "6938 baseline" does not reconcile against this**, and that is a
bookkeeping artifact, not a missing-test problem: `STATUS.md` attributes 6938 to
commit `9fa5b836` (the PRD-04 D1/D2 commit), which is several commits behind
`HEAD`. Measured against the tree these agents actually started from, the suite
went up by exactly the 98 tests they wrote.

## What landed, and what I verified about it

### PRD-03 D1 — revision review (chat-surface)

`ArtifactRevisionReview.tsx` (new) + wiring in `ArtifactSurface.tsx`, with 8
tests in `ArtifactSurface.review.test.tsx`. Verified by reading: the tests assert
real rendered content (the diff's deleted/inserted words, the `r1 → r2` label,
the exact `createArtifactRevision` request body and its parent revision), not
mere presence. Revert goes through the existing bounded `restore(parent)`, so it
appends and every revision stays retrievable — asserted directly.

**Finding — guard coverage was overstated.** The report claimed "each of those
five guards is proven load-bearing". I deleted each guard and re-ran:

| Guard                                | Removed alone | Verdict           |
| ------------------------------------ | ------------- | ----------------- |
| `!REVIEWED_ARTIFACT_AUTHORS.has(…)`  | 1 test fails  | load-bearing      |
| `shown.revision !== latestRevision`  | 1 test fails  | load-bearing      |
| `deliberate` (reader navigated)      | 1 test fails  | load-bearing      |
| `previous === null` (first paint)    | all 8 pass    | **was uncovered** |
| `shown.parent_revision !== previous` | all 8 pass    | **was uncovered** |

Removing the last two _together_ also left all 8 green, so neither was exercised
at all — they are partially redundant and each was masking the other's absence.
I added two tests to close this, and mutation-checked both:

- _"raises no review when the reader opens an artifact already sitting at a model
  revision"_ — fails when both guards are removed (pins the pair; this is the
  deep-link/first-paint case, where `previous` is `null` and the panel would
  otherwise announce a change against a revision never on screen).
- _"raises no review when the head skips past the revision on screen — the
  multi-revision gap"_ — fails when `parent_revision !== previous` alone is
  removed (pins that guard individually).

`previous === null` remains provably redundant on its own (removing only it still
passes). It is defensive, and I left it.

**Known gap, now pinned rather than invisible.** Two agent revisions landing in
one turn (r1 on screen, head jumps to r3) still swap content silently, because
D1 is scoped to a revision "whose `parent_revision` is the revision currently on
screen". That is the PRD's stated contract, so I did not widen it — but it is now
a named test that says so, instead of an untested branch.

Not verified: dataset-kind revise shows a text diff rather than changed cells
(the PRD's stated fallback).

### PRD-03 D2 — execution mode recorded (ai-backend)

`artifacts/execution_mode.py` (new), `execution_mode` as a required un-defaulted
field on all three write commands, derivation via
`ArtifactExecutionModeResolver` in `service.py`, `ArtifactOperationAuditPort`,
wired at `api/artifact_repository.py`.

Verified by reading the tests: they are strong. In particular
`test_the_table_accounts_for_every_public_method_on_the_service` is reflective
over `vars(ArtifactService)`, so a new public method forces classification as
write/read/neither; `TestEveryWriteRecordsItsMode` drives all 9 public writes and
asserts the operation and event type each records. The claim that the mode cannot
be client-supplied is structural (frozen `extra="forbid"` contracts) and asserted
per-contract. The route strings persisted in idempotency keys are unchanged by
the refactor (`Routes.BY_OPERATION` is an identity map onto the old constants),
so no durable key moved.

The reported `_request_digest` fix is real and correctly reasoned — the mode is
excluded from the persisted idempotency key, and
`test_the_idempotency_digest_does_not_move_when_the_mode_does` is non-vacuous
(it asserts the monkeypatched mode actually reached the commands before comparing
digests).

**Finding — a transient audit-sink failure permanently loses the audit row.** I
reproduced this. `_record_operation` deliberately raises when the sink fails, but
it also short-circuits on `replayed`. The sequence:

1. Attempt 1: the artifact commits, ledger events publish, the audit sink is
   down, `_record_operation` raises, the caller sees the error.
2. Attempt 2 (the only recovery available — same idempotency key): the store
   returns `replayed=True` (real behaviour;
   `runtime_adapters/in_memory/artifact_metadata_store.py:668`), so
   `_record_operation` returns before reaching the sink.

Measured outcome: artifact durably committed, **0 audit rows, and no retry of
that key can ever produce one**. The module docstring's premise — that the record
cannot go missing without anyone noticing — does not hold past the first retry.
I did not fix this: the remedies (write the row in the same transaction, or an
outbox, or re-record when a replay finds no existing row) are design decisions
beyond a verification pass, and each changes behaviour the authors chose
deliberately.

**Finding — the composed wiring is not covered.** `api/artifact_repository.py`
passes `cast(ArtifactOperationAuditPort, persistence)`, so the type checker
proves nothing there. The test that looks like it covers this,
`test_the_runtime_audit_log_satisfies_the_port_unchanged`, is weaker than it
reads: `runtime_checkable` `isinstance` checks only that a `write_audit_log`
attribute _exists_, not that its signature matches. It would pass for an object
with a `write_audit_log` of any shape. The underlying claim (all three adapters
declare the identical signature) is true by inspection, but nothing executes the
real composition.

Minor, not a defect: `create_draft_from_bytes` is audited as `artifact.publish`.
That matches its pre-existing durable route, but an auditor reading the log sees
"publish" for a draft.

Minor, cosmetic: in `test_every_operation_commits_under_a_distinct_durable_route`
the first assertion is tautological (it looks the operation up in a list built
from every operation). The distinctness assertion beside it is the real one.

### PRD-04 D4 — publication eval (ai-backend)

`tests/evals/publication/` as a second family alongside `surfaces/`, sharing a
hoisted `tests/evals/report_io.py`.

**Verified it has teeth**, by simulating a D1 regression myself — a pytest plugin
wrapping `PublicationTurn.tool_result` to drop `stored_in` /
`wrote_to_filesystem`, with no `src/` edit. Result: **7 failed, 21 passed**,
including the headline no-filesystem-claim assertion, the literal-phrase check,
and the committed baseline. I also confirmed the harness imports and runs the
real `PublishArtifactTool` / `ReviseArtifactTool` through the real
`OperationGateway`, so a genuine D1 regression does reach the corpus.

The assertions are honest. `test_an_adversarial_ask_is_answered_not_dodged`
requires a _negated_ match on adversarial turns, so a narration that scores clean
by saying nothing at all fails — that is the failure mode this kind of eval
usually misses.

**Stated limitation, which the authors disclosed and I confirmed.** The hermetic
arm uses a replay narrator that branches on whether the tool result states a
destination. It therefore pins the detector and the real tool-result shape — not
model cognition. The arm that would exercise a model is `test_evals_live.py`,
which skips without a model env var; I did not run it (it would spend the user's
provider key).

## What did NOT land

- **PRD-04 D3** — the capability-honest system phrasing does not exist in the
  codebase. The eval supplies that posture in its own `SYSTEM_PROMPT`, so it
  pins no production string. Recorded, not papered over.
- **Live end-to-end verification** — not attempted here.
- **The audit-durability hole above** — open.
- **A test over the real `ArtifactServiceComposition` wiring** — open.

## Documentation state

`docs/plan/artifact-editing/STATUS.md` is now **materially wrong** and I left it
alone rather than edit a file another job may be touching. It still reads:

> ## PRD-03 — revision review ⬜ NOT STARTED
>
> … Nothing here is built.

Both D1 and D2 are built and tested. Whoever commits this should correct that
section — and should not tick "Execution mode is recorded and auditable" without
first deciding what to do about the retry-loses-the-row finding, or tick the D1
box without noting the multi-revision gap.

---

# Second verification pass — the idempotency crash, the widened D1 rule, D4, and the audit wiring

Written by a second, independent verification pass over the working tree on top of
`16ebc7ed`, after three agents worked concurrently. Same rule as above: every
number below came from running the command in this environment, and the one claim
that mattered most was checked by **removing the fix and watching the test fail**.

Two of the three agents filed reports; the third did not, so the `chat-surface` /
`surface-renderers` half (the widened D1 rule and the D4 cell diff) was verified
cold, with no author account of it to read.

Pleasingly, this round closes three items the first pass left open: the
multi-revision gap it "pinned rather than hid", the missing test over the real
`ArtifactServiceComposition` wiring, and the unverified dataset text-diff
fallback.

## Suite numbers I measured myself

| Suite                       | Command                                          | Result                                      |
| --------------------------- | ------------------------------------------------ | ------------------------------------------- |
| ai-backend unit             | `pytest tests/unit -q -p no:randomly`            | **8269 passed, 99 skipped, 0 failed**       |
| chat-surface                | `vitest run --root packages/chat-surface`        | **3354 passed, 1 failed** (318 files)       |
| surface-renderers           | `vitest run --root packages/surface-renderers`   | **404 passed, 0 failed** (18 files)         |
| chat-surface typecheck      | `tsc --noEmit -p tsconfig.json`                  | **exit 0, no output**                       |
| surface-renderers typecheck | `tsc --noEmit -p tsconfig.json`                  | **exit 0**                                  |
| ruff check + ruff format    | `ruff` 0.15.12, all 9 changed Python files       | **All checks passed / 9 already formatted** |
| format                      | `prettier@3.8.3 --check`, all 9 changed TS + doc | **clean**                                   |

The single `chat-surface` failure is the same pre-existing one the first pass
recorded: `src/destinations/run/canvasLifecycle.test.ts` ("differentially matches
the Python fold"), which throws `Canvas lifecycle differential test requires the
ai-backend venv at …/.claude/services/ai-backend/.venv/bin/python` — a path this
environment does not have. Not attributable to this work.

`test_approval_serialisation.py` passed inside the full run; it did not need
isolating this time.

### Tests added, counted per file

+18 in ai-backend, +8 in the frontend packages:

| File                                                         | Before → after     |
| ------------------------------------------------------------ | ------------------ |
| `tests/unit/agent_runtime/api/test_artifact_audit_wiring.py` | new, **9**         |
| `tests/unit/runtime_worker/test_artifact_event.py`           | 5 → **8** (+3)     |
| `tests/unit/runtime_adapters/test_store_conformance.py`      | 141 → **147** (+6) |
| `surface-renderers/…/DatasetRevisionDiff.test.tsx`           | new, **6**         |
| `chat-surface/…/ArtifactSurface.datasetReview.test.tsx`      | new, **2**         |

(The conformance file gains 2 test functions × 3 backend params. The `postgres`
param is collected-then-skipped, so it is named by the contract without needing a
database.) I did **not** attribute the `7048 → 8269` unit-suite delta to these
agents: the tree moved from `a603d881` to `16ebc7ed` with merges from `main` in
between, so most of that jump is other people's work.

## The idempotency fix — the one that mattered, and it is real

**The regression test genuinely fails without the fix. Verified by experiment, not
by reading.** I copied the two CI-reachable adapters aside, ran
`git checkout HEAD --` on them to restore the old inline
`matches_envelope`-or-raise comparison (leaving the new untracked
`_event_idempotency.py` in place but unused), and re-ran:

```
FAILED …/test_artifact_event.py::TestCrossLaneRedeliveryIsIdempotent::test_recovery_command_after_inline_publish_is_a_replay
FAILED …/test_artifact_event.py::TestCrossLaneRedeliveryIsIdempotent::test_recovery_command_after_pre_seal_drain_is_a_replay
FAILED …/test_artifact_event.py::TestCrossLaneRedeliveryIsIdempotent::test_inline_publish_after_recovery_lane_is_a_replay
FAILED …/test_store_conformance.py::TestStableEventIdConformance::test_redelivery_ignores_the_amendment_delivery_annotation[in_memory]
FAILED …/test_store_conformance.py::TestStableEventIdConformance::test_redelivery_ignores_the_amendment_delivery_annotation[file]
5 failed, 8 passed, 5 skipped
```

with the reported live error verbatim —
`RuntimeEventIdempotencyConflict: runtime event artevt_dddd… for run … conflicts
with an existing event body`, raised from `file/runtime_api_store.py:2156`. I then
restored both files from the copies and confirmed the SHA-256 of each matches the
pre-revert byte-for-byte, and that `git status --short` is unchanged. The tests are
load-bearing, and the fix is what makes them pass.

Two further things I checked rather than took on faith:

- **The strip did not widen acceptance.**
  `test_annotated_redelivery_with_a_different_body_still_fails_closed` passes both
  with and without the fix — correct for a fail-closed control, and it does pin
  that a genuinely different body under an annotated id still raises.
- **The error identity did not move.** `EventRedeliveryResolver.resolve` raises
  with `existing.event_id` where the old code used `event.event_id`. In all three
  adapters the existing row is looked up _by_ that id (Postgres:
  `SELECT * FROM runtime_events WHERE id = %s`), so the two are equal and no
  message changed.

The reasoning in the fix holds up: a `LedgerAmendment` describes the append
attempt, and `LedgerAmendment.METADATA_KEYS` gives the store one place to name
what an attempt stamps. Routing all three adapters through one resolver is the
right call — the failure mode of not doing so is a redelivery that succeeds on
Postgres and crash-loops on the file store.

## The other three items — the test that proves each, and whether it says anything

### Widened D1 rule (`shown.parent_revision !== previous` → `shown.revision <= previous`)

`ArtifactSurface.review.test.tsx:354`, _"raises the r1→r3 review when a turn
writes two revisions and the head skips past the one on screen"_.

**This is the first pass's `KNOWN GAP` test, flipped from asserting _no_ review to
asserting the review — a strengthening, not a weakening.** It now asserts the
`r1 → r3` label, that the diff is against r1 (the revision the reader actually
had) and not r2 which they never saw, and that the button reads `Revert to r1`. I
confirmed the base really is the previously-shown revision and not
`parent_revision`: the effect sets `baseRevision: previous`.

All three guards the PRD says bound the rule still have their own tests, and the
diff shows **none of them was touched**: reader-navigated (`:279`),
already-sitting-at-a-model-revision (`:331`), superseded-by-a-newer-head (`:301`),
user-authored (`:268`). The only other change to that file is a fixture widening
(`store()` gained a per-test `texts` override) — additive.

### D4 dataset cell diff

`surface-renderers/…/DatasetRevisionDiff.test.tsx`, 6 tests, all rendering through
the **real** `DatasetArtifactRenderer`. They assert cell-level content, not
presence: a changed cell as `<del>12</del> <ins>15</ins>` at `row 2` with the
untouched `Ada` cell marked `data-changed="false"`, an added row, a removed row,
and the two documented fallbacks (not-a-grid, and a change that moved no cell
value — the `"hello"` → `hello` requoting case). The last test feeds a malformed
payload (`baseRevision: "1"`) and asserts the panel does not render, so the
structural narrowing is covered.

`chat-surface/…/ArtifactSurface.datasetReview.test.tsx` covers the seam with a
stub adapter that echoes the payload. That is the honest construction, not a
shortcut: `chat-surface` must not import `surface-renderers`, so the seam under
test really is the field on the mounted render state. It asserts the exact echoed
object, that the panel drops its own text diff, and that both actions remain.

### Audit composition

`tests/unit/agent_runtime/api/test_artifact_audit_wiring.py`, 9 tests. This closes
the first pass's open item, and it closes it properly rather than by restating the
`isinstance` check the first pass called out as too weak. Ports come from the real
`RuntimeAdapterFactory.from_store(...)`, the call shape is checked keyword-only and
awaitable with a positional bind asserted to `TypeError`, and one real
`create_from_bytes` is followed to the signed row _and back out through_
`list_audit_log_for_export`. It ships its own negative control —
`test_the_shape_check_refuses_a_sink_the_protocol_alone_accepts` — so the
conformance assertions are demonstrably non-vacuous. Nothing here is weakened.

## Findings

**1. Two `chat-surface` tests are load-sensitive, not broken.** My first full run
(taken while the 8269-test Python suite was running concurrently) failed
`src/refs/registry.test.ts` ("hasItemRoute is false for every ItemKind…", a 30 s
import timeout) and `src/artifacts/ArtifactSurface.revisions.test.tsx` ("compares a
historical revision…", `findByRole` missing "Compare to current" against a
1 s default). Both pass in isolation and both pass in a clean full run
(3354 passed / 1 failed). The config is plain `environment: "jsdom"` with default
isolation, so this is CPU starvation, not cross-file pollution from the new
adapter-registering test. Worth knowing because a loaded CI runner can reproduce
it: `ArtifactSurface.revisions.test.tsx` in particular depends on an async
transport resolving inside a 1 s implicit timeout.

**2. The first pass's audit-durability hole is still open, and the new test does
not close it or block closing it.** `test_an_idempotent_replay_adds_no_second_row`
pins the _happy_ replay (one commit, one row). It does **not** cover the failure
sequence the first pass reproduced — sink down on attempt 1, `replayed=True` on
attempt 2, zero rows forever. I checked that the obvious remedy (re-record when a
replay finds no existing row) would still leave exactly one row in the happy case,
so this new test is not an obstacle to fixing it. Do not read the new audit test
file as evidence that audit rows are durable under sink failure.

**3. Unfixed, and correctly flagged by its author:**
`runtime_adapters/postgres/runtime_api_store.py:6063` raises
`RuntimeEventIdempotencyConflict` from a `UniqueViolation` after
`_AppendEventRetry.MAX_ATTEMPTS` **without comparing bodies at all**. I confirmed
the code path. It is not reachable by the artifact scenario — every attempt re-runs
the `SELECT`, which now resolves through `EventRedeliveryResolver` — so reaching it
needs a concurrent insert landing between the `SELECT` and the `INSERT` on every
attempt. Latent and race-only, but it is a body-blind conflict path, and the
Postgres suite is DB-gated so nothing in CI exercises it either way.

**4. Cosmetic, not acted on.** All three adapters narrow `write_audit_log`'s
`record` to `dict[str, object]` while both ports declare `record: object`. Harmless
today because the composed service always passes `to_audit_record()`.

## Definition-of-done status — only what I personally verified

From `PRD-03-revision-review.md`. I left the boxes unticked there and record the
evidence here instead:

- **A model revision presents a diff with keep/revert rather than a silent swap,
  including when the head skips past the revision on screen** — verified
  (`ArtifactSurface.review.test.tsx`, 10 tests incl. `:354`).
- **Revert appends rather than rewrites; all revisions remain retrievable** —
  verified by the first pass and still green; I did not re-derive it.
- **A revised dataset presents changed cells; the word diff is the fallback** —
  verified (6 + 2 tests).
- **Execution mode is recorded and auditable on artifact operations** — the
  _composition_ is now verified; **do not tick this**, because finding 2 above
  (retry loses the row permanently) is unresolved and is exactly what "auditable"
  would be claiming.
- **No pre-commit gate was added to the artifact write path** — not re-checked in
  this pass.
- **`chat-surface` / `surface-renderers` / `ai-backend` suites green** — verified,
  with the one pre-existing environmental failure named above.

Nothing in this pass was fixed by me: I found nothing genuinely broken. The
working tree is exactly as the three agents left it (verified by SHA-256 after the
revert experiment), with this section appended.
