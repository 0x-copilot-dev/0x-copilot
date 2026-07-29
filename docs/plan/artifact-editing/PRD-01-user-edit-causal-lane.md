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

`ArtifactRevisionRequest` gains `acting_conversation_id: str | None`, mutually
exclusive with `acting_run_id` (model validator, not documentation).

`ArtifactScope` gains `lane: ArtifactCausalLane = RUN` and relaxes `run_id` to
`str | None` **only** when `lane is CONVERSATION`; a `RUN`-lane scope still
requires a non-empty `run_id`, enforced by a model validator so no caller can
construct a run-lane scope without a run.

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

### D3. Conversation-lane events

`artifact.revised` in the CONVERSATION lane is emitted against the conversation,
not a run. The artifact record already carries `conversation_id` (verified in the
live store), and the canvas read path is already conversation-scoped, so the
client observes the new revision through the surface it already polls. This is the
**write half** of the conversation-scoping that PR #418 landed on reads.

### D4. Typed refusal reasons

`ArtifactConflictError` and `ArtifactIdempotencyConflictError` are distinct
server-side but both collapse to a bare 409 (`artifacts.py:104`), and the client
assumes staleness. Add a machine-readable reason code to the error body:

```
REVISION_STALE        parent_revision/expected_digest no longer current
IDEMPOTENCY_REPLAY    same key, different request digest
RUN_SEALED            claimed run is terminal (RUN lane only)
```

`ArtifactSurface` surfaces the actual reason. The current UI states something
demonstrably false; that is worse than an opaque error.

## Implementation plan

1. `ledger_models.py` / `contracts.py` — `ArtifactCausalLane`; `ArtifactScope.lane`
   with its validator; `ArtifactRevisionRequest.acting_conversation_id` with an
   exclusivity validator.
2. `artifact_repository.py` — `resolve_conversation(org_id, user_id, conversation_id)`
   returning a CONVERSATION-lane `ArtifactScope`.
3. `service.py` — derive lane from provenance; route scope resolution; narrow the
   terminal guard to the RUN lane; emit the conversation-lane event.
4. `runtime_api/http/artifacts.py` — accept `acting_conversation_id` in the
   multipart metadata; typed error body.
5. `packages/api-types` + `chat-transport` — contract + zod (`.strict()`, so the
   field must be declared) + `WebTransport`/`IpcTransport` form fields.
6. `ArtifactSurface.tsx` — send `actingConversationId` for user edits; stop sending
   `actingRunId`; render the typed reason.
7. `RunDestination.tsx:3434` — pass the conversation identity.

## Test plan

- User edit while the viewed run is `completed` → **201**, revision 2 appended.
  (Direct regression test for the live repro.)
- User edit while a run is live → still CONVERSATION lane; does not enter the run ledger.
- Model-authored revision claiming a terminal run → still **409 `RUN_SEALED`**.
- A client attempting `acting_conversation_id` on a model-authored path cannot
  change the lane (lane derived from provenance, asserted directly).
- Both `acting_run_id` and `acting_conversation_id` set → 422.
- `RUN`-lane `ArtifactScope` with `run_id=None` is unconstructable.
- Stale parent → `REVISION_STALE`, not `RUN_SEALED`.
- Seal invariant: no conversation-lane event is appended to any run ledger.

## Definition of done

- [ ] Editing a cell and saving succeeds on a completed run — the live repro passes.
- [ ] The "A newer revision exists" message appears only when a newer revision
      actually exists; the seal refusal reports `RUN_SEALED`.
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
