# PRD-01 — A causal lane for user-authored artifact mutations

**Status:** specified
**Closes:** live Bug 1 (Save patched revision → 409)
**Ledger impact:** new; postdates the v2.1 audit baseline `e96d55d5`

## Implementer brief

A user cell-edit on the Studio canvas is refused with HTTP 409 and the UI reports
"A newer revision exists" — which is false. Make the causal subject of an
artifact mutation explicit: agent work is caused by a **run**, user work is
caused by a **conversation**. Derive the lane from server-held authorship, never
from the client.

## Context

### Observed failure (live, 2026-07-29)

- `POST /v1/agent/artifacts/{id}/revisions` × 2, both **409**; the only two 409s
  in `~/Library/Application Support/0xCopilot/logs/ai-backend.log`.
- `art_41618344-…` has `current_revision: 1`, `parent_revision: null`. **No newer
  revision ever existed.** The message shown to the user is counterfactual.
- Run `4435d40de4834163a151e3ddc12dbeb4` was `status = completed` at
  `2026-07-29T05:34:27.803802Z`, ~2s after publication and well before the edit.

### Mechanism

1. `RunDestination.tsx:3434` sends `actingRunId: session.runId` — the run on screen.
2. `service.py:337` — `if request.acting_run_id is not None and scope.run_is_terminal: raise ArtifactConflictError()`.
3. `completed ∈ TERMINAL_RUN_STATUSES` (`conversation_query_service.py:120`) → 409.
4. `ArtifactSurface.tsx:109` maps **any** 409 → `"conflict"` → the false message.

The guard is gated on `acting_run_id is not None`. Omitting the field skips the
check entirely, so `828a8d17` — which wired the field end to end — is what turns
a working save into a guaranteed 409. Its reasoning was sound about the _creating_
run being sealed; it did not account for the _acting_ run being sealed too, which
is the ordinary case because editing happens after a turn ends.

### Root cause

Authorship and causality are independent axes, and the code collapses them.

- Authorship **is** modelled: `ArtifactAuthor = MODEL | SUBAGENT | USER | SYSTEM | IMPORT`
  (`ledger_models.py:235`), and `artifacts.py:332` correctly stamps a cell-edit `USER`.
- Causality has **one lane**: `ArtifactScope` (`contracts.py:155`) is documented as
  _"Verified run ownership used by every mutation and dereference"_ and requires a
  non-empty `run_id`.

So a mutation that belongs to no run must borrow one. Borrowing a sealed one is
correctly refused, because `RunTerminationCoordinator` is _"the seal authority …
the only place that can honestly promise 'everything this run caused is already in
the ledger'"_ (`run_termination.py:106`). A user edit made minutes after the run
ended **was not caused by that run**. Attributing it there would make the seal lie.

**The guard is right. Claiming the run is wrong.**

## Interfaces consumed

- `ArtifactService.append_revision` — unchanged compare-and-append semantics.
- `RuntimeArtifactScopeResolver.resolve_run` (`artifact_repository.py:288`).
- `GET /v1/agent/conversations/{conversation_id}/canvas` (`routes.py:988`) — the
  conversation-scoped read path already shipped in PR #418.

## Interfaces exposed

```python
class ArtifactCausalLane(StrEnum):
    RUN = "run"                    # caused by agent activity inside a live run
    CONVERSATION = "conversation"  # caused by a user acting on the canvas
```

`ArtifactScope` gains `lane: ArtifactCausalLane = RUN` and relaxes `run_id` to
`str | None`. Validators make both halves unconstructable-if-wrong rather than
merely discouraged: a `RUN`-lane scope must name a run, and a `CONVERSATION`-lane
scope must **not** — carrying one would let a downstream reader re-derive the run
causality the lane just denied.

**No `acting_conversation_id` is added to the wire.** The first draft of this PRD
proposed one for symmetry with `acting_run_id`; implementing it showed that to be
wrong. The artifact record already carries its `conversation_id`, so the server
derives the subject authoritatively. Accepting one would add a forgeable input for
a fact already held — the opposite of the direction this change is going.
`acting_run_id` survives unchanged for RUN-lane callers and is simply not
consulted for user-authored revisions, which a test pins.

`ArtifactAppendCommand.ledger_event` becomes optional, with a validator binding it
to the lane: required for `RUN`, forbidden for `CONVERSATION` (see D3).

## Design

### D1. The lane is derived, never supplied

The server picks the lane from `ArtifactProvenance.author`, which is already
_"Trusted server-derived authorship; never deserialize from an app body"_
(`contracts.py:218`):

| author                        | lane           | sealed by                |
| ----------------------------- | -------------- | ------------------------ |
| `MODEL`, `SUBAGENT`, `SYSTEM` | `RUN`          | the run's terminal event |
| `USER`                        | `CONVERSATION` | never                    |

A client cannot route a model-authored write into the unsealed conversation lane,
because it does not choose the lane — authorship is decided server-side from the
authenticated route (`artifacts.py:332` sets `USER` for the human HTTP path; the
worker sets `MODEL`). This is the security property that makes the relaxation safe.

### D2. The terminal guard narrows, it does not weaken

`service.py:337` becomes: refuse a claimed run that is terminal **in the RUN lane**.
The CONVERSATION lane never consults `run_is_terminal` because it never claims a run.

Net effect on the invariant: **strengthened**. Today a `USER` edit with
`acting_run_id=None` silently falls back to `current.artifact.run_id` and appends
with no terminal check at all — a real hole. After this change a user edit never
targets a run ledger, so that fallback disappears.

### D3. Conversation-lane mutations emit no run-ledger event

`RuntimeArtifactEventCommand` is documented as: _"The worker appends it to the
existing run event store … **no second event transport exists**"_
(`commands.py:170`). So a conversation-lane event has nowhere to go that is not a
run ledger, and appending to a sealed run is the exact thing the guard prevents.

Adding a second event transport is rejected — that "no second transport" line is a
deliberate architectural statement, not an oversight.

Therefore a CONVERSATION-lane mutation **emits no run-ledger event**, and that is
correct rather than a gap: the run ledger records what a _run_ caused, and this was
not caused by a run. Durability and auditability are unaffected because the record
lives where it belongs:

- the artifact repository — an immutable revision carrying `author=USER`,
  timestamp, byte size, and content digest;
- the audit log, which records the mutation independently of the run ledger.

Nothing observable regresses, because no consumer depends on that event:

- `ArtifactSurface.appendRevision` already updates from the **HTTP response**
  (`setSelectedRevision(...)` + `data.reload()`), not from a stream event.
- Canvas tabs already come from the conversation-canvas **endpoint**
  (`useConversationCanvas`, `RunDestination.tsx:2238`) — a query over the artifact
  repository, which reflects the new `current_revision` immediately.

This is the write-side counterpart of the conversation-scoping PR #418 landed on
reads: reads stopped being run-scoped, and now user writes stop being run-caused.

### D4. Typed refusal reasons

`ArtifactConflictError` and `ArtifactIdempotencyConflictError` are distinct
server-side but both collapse to a bare 409 (`artifacts.py:104`), and the client
assumes staleness. Every `ArtifactError` already carries a stable
`ArtifactErrorCode`; the HTTP layer was dropping it. Send it in the body:

```
artifact_conflict              parent_revision/expected_digest no longer current
artifact_idempotency_conflict  same key, different request digest
artifact_sealed_run            claimed run is terminal (RUN lane only)
```

`TransportHttpError` already exposes a `code` getter over the structured detail,
so the client side is wiring, not new machinery. `ArtifactSurface` reports a lost
update only when the server actually said staleness; anything else becomes a plain
failure. Stating something demonstrably false is worse than an opaque error.

## Implementation plan

1. `ledger_models.py` — `ArtifactCausalLane`.
2. `contracts.py` — `ArtifactScope.lane` + `run_id` relaxation, both bound by a
   validator; `ArtifactAppendCommand.ledger_event` optional, bound to the lane.
3. `ports.py` / `artifact_repository.py` — `resolve_conversation(...)` returning a
   CONVERSATION-lane scope, proving ownership with the same tenant-and-owner
   filtered lookup the conversation surface uses.
4. `errors.py` — `ArtifactSealedRunError` + `SEALED_RUN` code.
5. `service.py` — `_require_revision_scope` derives the lane from provenance,
   routes scope resolution, narrows the terminal guard to the RUN lane, and omits
   the ledger event in the conversation lane.
6. Store adapters (file / in-memory / postgres) — enqueue no outbox row when the
   command carries no ledger event.
7. `runtime_api/http/artifacts.py` — map `ArtifactSealedRunError` to 409; carry
   `code` in the error body.
8. `ArtifactSurface.tsx` — stop claiming a run; report a lost update only on
   `artifact_conflict`.
9. `RunDestination.tsx` — stop passing `actingRunId`.

## Test plan

- User edit while **every** run is terminal → succeeds, revision 2 appended.
  (Direct regression test for the live repro.)
- User edit claims no run and emits no ledger event; scope is CONVERSATION lane.
- Supplying `acting_run_id` on a user edit does not drag it back into a run.
- Identical request, different server-held author → different lane (the lane is
  not client-controllable).
- Model-authored revision claiming a terminal run → `ArtifactSealedRunError`,
  refused before any blob is written.
- A sealed run is not reported as a stale revision — the error type and code are
  distinct from `ArtifactConflictError`.
- Foreign acting run → not found; claims are verified, not trusted.
- `RUN`-lane `ArtifactScope` without a run, and `CONVERSATION`-lane with one, are
  both unconstructable.

## Definition of done

- [ ] Editing a cell and saving succeeds on a completed run — the live repro passes.
- [ ] The "A newer revision exists" message appears only when a newer revision
      actually exists; the seal refusal reports `artifact_sealed_run`.
- [ ] Model-authored writes to a sealed run remain refused.
- [ ] Lane is provably not client-controllable.
- [ ] `ai-backend` unit suite green; `chat-surface`, `chat-transport`, `api-types`
      typecheck and tests green.

## Out of scope

- `artifact.revise` for the model (PRD-02).
- Any diff/approve affordance (PRD-03).
- Postgres adapter parity beyond what the file store needs — desktop is file-native.

## Guardrails

- Do **not** relax `run_is_terminal` for the RUN lane.
- Do **not** let the client choose the lane.
- Do **not** delete the `acting_run_id` field; it remains correct for in-run
  agent-authored revisions.
