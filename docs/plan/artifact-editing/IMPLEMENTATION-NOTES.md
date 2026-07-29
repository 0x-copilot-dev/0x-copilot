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
