# PRD-D3 — Bulk row-set staging + per-row decisions + partial apply 🎨

Give the staged-write engine its second shape: a **row-set** (bulk) write. The agent
proposes N item-level changes as one staged table surface with per-row old→new diffs; the
agent pre-holds risky rows with a visible reason; the user toggles rows between
approve/hold (override keeps the warning visible), then applies with an action that names
its scope ("Apply 7 changes →"). The D2 CommitEngine executes **only** the approved rows,
per-row idempotent; held rows are never dispatched; partial results are a first-class
ledger outcome (`result: "partial"`). Under an allow-always connector policy, unflagged
rows auto-apply (ledgered with `actor: "policy"`) while agent pre-holds **still hold**
(FR-C8). Flag off ⇒ byte-identical behavior to today.

## Implementer brief

You are implementing this in a monorepo. Work in a **fresh git worktree branched off
`main`** (never commit on `main` directly). Repo root contains `services/ai-backend`
(Python 3.13, FastAPI + LangGraph), `services/backend-facade` (Python proxy),
`packages/api-types`, `packages/chat-surface` (TypeScript, vitest),
`packages/service-contracts` (Python + JSON constants). Run `make setup` once if the
service `.venv`s / `node_modules` are missing.

Test commands (from repo root unless noted):

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/tools/test_stage_rowset_write.py
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_worker/test_stage_commit_rowset.py
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/
cd services/ai-backend && .venv/bin/python -m pytest            # full suite before PR
cd services/backend-facade && .venv/bin/python -m pytest
npm run test --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/chat-surface && npm run lint --workspace @0x-copilot/chat-surface
npm run test --workspace @0x-copilot/api-types && npm run typecheck --workspace @0x-copilot/api-types
```

Read these files first (paths relative to repo root):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative,
   verbatim), §7 S4 (this PR's sequence), §10 invariants 2–3, §11 compat.
2. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-C6–C9, NFR-4/6/7.
3. `docs/plan/generative-surfaces-v2/prds/PRD-D1-staged-write-engine.md` and
   `PRD-D2-commit-engine.md` — the engine you extend; both merge before D3; their
   "Exposed" sections are your consumed contract.
4. Merged D1/D2 code: `services/ai-backend/src/agent_runtime/surfaces_v2/staging.py`
   (`WriteStager`, `StagedWriteFold`, decision matrix), `commit_engine.py` (four-invariant
   `CommitEngine.commit`, `StageCommitRequest.commit_key()`), and
   `src/runtime_worker/handlers/stage_commit.py` (`RuntimeStageCommitHandler`) — locate
   before writing code.
5. `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/ask_a_question.py`
   — the builtin-tool pattern (`RuntimeContract` input, `_Fields`/`_Limits` constants).
6. `services/ai-backend/src/agent_runtime/execution/factory.py` — unconditional builtin
   tools appended via `_structured_tool(AskAQuestionTool(...))` (~L410); flag-gated
   Wave-1 tools (`code_mode_tool` / `sandbox_execute_tool`) are built per-run by the
   worker and appended **last** as injected params (~L421–428) — your tool follows that
   gated-injection pattern, not an inline flag check.
7. `services/ai-backend/src/runtime_api/schemas/events.py` — payload allow-list pattern
   (`_surface_spec_generated_payload`, L670) for the new payload keys.
8. `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` (PRD-B1) — the client
   fold (`projectLedger`, `toParitySnapshot`) that D1 extended with `stages`; D3 adds
   row state to it.
9. `packages/chat-surface/src/surfaces/staged/` (PRD-D1) — `ApproveBar.tsx`,
   `StagedDraftSurface.tsx`; your table surface is their sibling.
10. `services/ai-backend/CLAUDE.md`, `services/ai-backend/tests/CLAUDE.md`,
    `packages/chat-surface/CLAUDE.md` — binding engineering rules (see Guardrails).
11. `tools/design-parity/SKILL.md` — the parity pipeline for the UI DoD.

## Context

Generative Surfaces v2 renders an agent's work on real SaaS tools as live artifact
surfaces on a per-run canvas; reads flow, **writes stage** and are decided on the
artifact with a what-you-approve-is-what-executes guarantee; everything is an event on a
typed, append-only **Work Ledger** whose projections give the canvas, receipt, and
sources (`../01-problem-and-requirements.md` §1–2, `../02-sdr.md` §2–§5). Wave A landed
contracts (A1), the UsageMeter (A2), and ledger emission behind the `SURFACES_V2` runtime
flag (A3); Wave B the canvas; Wave C classification + gates; PRD-D1 the single-artifact
staging engine (`write.staged`/`revision.added`/`decision.recorded`, rev-pinned approve
bar); PRD-D2 the CommitEngine — the single legitimate producer of `write.applied`, with
precondition re-check, idempotency claims before side effects, and a durable
API→queue→worker command pipeline (apply is never inline).

This PR (`../03-prds.md` PRD-D3, SDR §7 S4) generalizes staging to **row-sets**: one
staged write containing N per-row changes, each row individually decidable. It closes
FR-C6 (bulk decomposes to row decisions), FR-C7 (agent self-flagging survives override),
FR-C8 (bypass still leaves a trail and flags still hold), and FR-C9 (partial application
is a first-class outcome). Mid-apply failure **UX** is Phase-2 designer work; the states
and ledger events must be correct now (`../02-sdr.md` §7 S4 note).

## Interfaces consumed / exposed

**Consumed (must already exist on `main`):**

- PRD-A1: `StagedWrite`/`Revision`/`Decision` TS contracts + pydantic mirrors; event-type
  constants; ledger-id formatter (`r<short>·<seq>`); golden fixture
  `work_ledger_golden_events.json` in `packages/service-contracts`. VERIFY AT IMPL:
  exact symbol names / fixture path as merged.
- PRD-A3 (`services/ai-backend/src/agent_runtime/surfaces_v2/`): `SurfacesV2Flag.enabled`
  (env `SURFACES_V2`), `WorkLedgerEmitter` + `bind_for_run`/`active()` ContextVar seam.
- PRD-B1 (`packages/chat-surface`): `projectLedger` + `toParitySnapshot` in
  `src/thread-canvas/ledgerProjection.ts`; canvas tab mount. PRD-B2: provenance footer.
- PRD-C1: `EffectiveActionPolicyResolver.resolve(...)` → `EffectiveActionPolicy`
  (fields `mode`, `hold`, `bypass`), `ConnectorWritePolicy` (`ask_first`/`allow_always`).
  VERIFY AT IMPL: resolver signature as merged, and how the run's per-connector
  write-policy map reaches worker-side stager code.
- PRD-D1: `WriteStager`, `StagedWriteFold`, `StagedWriteState`, `StagedWriteStatus` in
  `src/agent_runtime/surfaces_v2/staging.py`;
  `RuntimeApiEventType.WRITE_STAGED/REVISION_ADDED/DECISION_RECORDED`; routes
  `GET /v1/agent/stages/{stage_id}`, `POST .../decisions` in
  `src/runtime_api/http/stages.py`; client `src/surfaces/staged/` components + the
  `stages` extension of the ledger fold.
- PRD-D2 (binding; the apply machinery D3 row-scopes): `CommitEngine` +
  `StageCommitRequest` (`commit_key() = "{stage_id}:{rev}:{decision_seq}"`,
  `tool_arguments()`) + `StageCommitConnector` (`read_remote_state`/`execute`) +
  `StageCommitLedgerPort` (`load`/`claim`/`complete`, claim atomic and durable) in
  `src/agent_runtime/surfaces_v2/commit_engine.py`; `McpStageCommitConnector`
  (`surfaces_v2/mcp_connector.py`); `RuntimeStageCommitCommand`
  (`src/runtime_api/schemas/commands.py`) + `RuntimeQueuePort.enqueue_stage_commit` +
  `Values.EventType.STAGE_COMMIT_REQUESTED`; worker handler
  `src/runtime_worker/handlers/stage_commit.py::RuntimeStageCommitHandler` (fold-based
  approval gate, sole `write.applied` producer); `RuntimeApiEventType.WRITE_APPLIED`
  with additive `failure`/`decided_by` keys; the "apply is never inline" discipline
  (API/stager enqueues, worker executes). D2 registered **no** `/apply` route —
  single-artifact approve itself enqueues; D3 defines the rowset apply route below.
  VERIFY AT IMPL: exact merged signatures — D2 carried its own VERIFY markers.

**Exposed (later PRDs rely on these; do not rename after merge):**

- Row-scoped payload shapes on `write.staged` / `decision.recorded` / `write.applied` —
  consumed by E1 (receipt rows "you approved"/"you held"/auto-applied from
  `actor: "policy"`) and E2 (staged row-set approval cards with pending counts).
- `StagedRow`, `RowState`, `RowStance`, `AgentHold`, `RowCounts` + the row-aware
  `StagedWriteState` (incl. `APPLY_PENDING`/`PARTIALLY_APPLIED`) — E1/E2 fold these;
  Phase-2 amendments style the `partial` states defined here.
- The builtin tool `stage_rowset_write` (agent-facing propose seam for bulk shapes).
- Client: `StagedTableSurface`, `BulkApplyBar`, row state in `ledgerProjection.ts`.

## Design

### Ledger events (SDR §5 verbatim; payloads carry `v: 1`)

No new event **types**. D3 activates the row-scoped fields the SDR already defines and
D1/D2 left dormant, plus three additive keys (marked):

```text
write.staged      {v, stage_id, surface_id, target: {connector, op}, proposal_ref,
                   rows: 8,                                   # count (SDR §5 "rows?: n")
                   agent_holds: [{row_key, reason}]}          # FR-C7 pre-holds
revision.added    {v, stage_id, rev: 1, author: "agent", diff_ref, proposal_ref,
                   rowset: {rows: [StagedRow…]}}              # ADDITIVE key; full row data
decision.recorded {v, stage_id, decision: "approve"|"hold"|"reject"|"restore",
                   scope: {rev: N} | {row_keys: [str…]},      # row scope activates here
                   actor: "user"|"policy",                    # "policy" = allow-always
                   apply?: true}                              # ADDITIVE; only on the
                                                              # apply-scoped approve (below)
write.applied     {v, stage_id, rev, row_keys: [str…],        # emitted ONLY by CommitEngine
                   result: "applied"|"partial"|"failed",
                   connector_receipt_ref, decided_by,          # D2's additive keys, kept
                   row_results: [{row_key, outcome: "applied"|"failed", detail?}]}  # ADDITIVE
```

- Two flavors of row-scoped approve exist and fold differently: a **stance toggle**
  (`record_row_decision`, no `apply` key — sets rows to will-apply, never triggers
  execution) and the **apply decision** (`apply_rows`, `apply: true` — freezes the stage
  and authorizes exactly that set). SDR §5 gains the additive keys note (Docs DoD).
- `proposal_ref = "stage://<stage_id>/v<rev>"` — logical address of the rev's rowset,
  resolvable by folding the ledger (mirrors D1's `draft://<id>/v<n>`; rowsets have no
  draft row). VERIFY AT IMPL: that the inline `rowset` payload stays under the event
  pipeline's payload-offload threshold at the caps below, or that the fold hydrates
  offloaded payloads via A3's payload-ref mechanics.
- New payload keys (`rowset`, `agent_holds`, `row_keys`, `row_results`, `reason`,
  `row_key`, `outcome`, `apply`) become `Keys.Field` / `_Fields` constants and enter the
  allow-list projections in `src/runtime_api/schemas/events.py` — never raw literals.
- Ledger ids shown in UI are `r<short>·<seq>` via the A1 formatter.

### Row contracts (NEW, `src/agent_runtime/surfaces_v2/rowset.py`)

```python
class RowFieldChange(RuntimeContract):
    field: str
    old: JsonValue | None               # value before (as read; None = absent)
    new: JsonValue | None               # proposed value

class StagedRow(RuntimeContract):
    row_key: str                        # stable, unique within the stage (target record id)
    title: str                          # human row label ("Acme Corp — renewal")
    target_args: JsonObject             # EXACT connector-op args for THIS row (WYSIWYG unit)
    changes: tuple[RowFieldChange, ...]

class AgentHold(RuntimeContract):
    row_key: str
    reason: str                         # 1..200 chars, shown inline (FR-C7)

class RowStance(StrEnum):
    WILL_APPLY = "will_apply"
    HELD = "held"

class RowState(RuntimeContract):        # fold output, per row
    row_key: str
    stance: RowStance
    agent_hold_reason: str | None       # STICKY — survives user override (FR-C7)
    decided_by: Literal["agent", "user", "policy"] | None  # "agent" = a write.staged
                                        # agent_hold (not a decision.recorded event);
                                        # "user"/"policy" from decision.recorded.actor
    apply_outcome: Literal["applied", "failed"] | None   # set by write.applied.row_results

class RowCounts(RuntimeContract):       # projection summary (fold output, per stage)
    total: int                          # len(rows)
    will_apply: int                     # rows whose stance == WILL_APPLY
    held: int                           # rows whose stance == HELD (agent or user)
    applied: int                        # rows with apply_outcome == "applied"
    failed: int                         # rows with apply_outcome == "failed"

class RowsetValidator:                  # _Limits: MAX_ROWS=200, MAX_CHANGES_PER_ROW=20,
    ...                                 # REASON_MAX=200; unique row_keys; holds ⊆ rows
```

Validation failure = typed domain error → 422 at the tool/API edge; **no event emitted**.

### Stager + fold extensions (`src/agent_runtime/surfaces_v2/staging.py`, modified)

```python
class StagedWriteStatus(StrEnum):             # D1/D2 members unchanged; two additive
    ...
    APPLY_PENDING = "apply_pending"           # apply decided, write.applied not yet folded
    PARTIALLY_APPLIED = "partially_applied"   # some approved rows failed mid-apply

class StagedWriteState(RuntimeContract):      # additive fields
    ...
    rows: tuple[RowState, ...] | None = None  # None = single-artifact stage (D1)
    row_counts: RowCounts | None = None       # {total, will_apply, held, applied, failed}

class WriteStager:                            # ctor gains policy_resolver (C1), additively
    async def stage_rowset(self, *, org_id, user_id, run, target_connector, target_op,
                           rows: Sequence[StagedRow], agent_holds: Sequence[AgentHold],
                           title: str) -> StagedWriteState:
        # validate → emit surface.created {kind: "table"} → write.staged (count + holds)
        # → revision.added {rev 1, author: "agent", rowset inline}
        # → allow-always branch (below)
    async def record_row_decision(self, *, org_id, user_id, stage_id, decision,
                                  row_keys: Sequence[str]) -> StagedWriteState:
        # decision ∈ {approve, hold}; precondition: status == STAGED, every key exists;
        # emits decision.recorded {scope: {row_keys}, actor: "user"} — NEVER enqueues
        # (amends D2's rule: only rev-scoped approve and apply_rows enqueue)
    async def apply_rows(self, *, org_id, user_id, stage_id, rev,
                         row_keys: Sequence[str]) -> StagedWriteState:
        # the ONLY rowset path to execution — see apply pipeline below
```

Decision-matrix changes vs D1/D2 (all other cells unchanged):

| Request                                                                | Precondition                                                                        | Effect                                                                          |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| approve/hold `{row_keys}`                                              | status==STAGED; keys exist                                                          | stance flips; event emitted; no enqueue, no execution                           |
| hold `{rev}` (D1's 422 cell)                                           | still 422                                                                           | hold is row-scoped only                                                         |
| any decision with an unknown row key                                   | —                                                                                   | 404 `unknown_row_key`, no event                                                 |
| apply (rev, row_keys)                                                  | status==STAGED; rev == latest_rev; `row_keys` == current will-apply set **exactly** | apply decision emitted (`apply: true`) → APPLY_PENDING → enqueue                |
| apply with mismatched set                                              | —                                                                                   | 409 `apply_set_mismatch`, no event (WYSIWYG: you apply exactly the set you saw) |
| duplicate apply (same rev + set) during APPLY_PENDING or after APPLIED | idempotent                                                                          | 200, zero additional events, zero enqueues                                      |
| row decision while APPLY_PENDING / after APPLIED / PARTIALLY_APPLIED   | —                                                                                   | 409 `stage_frozen`                                                              |
| reject / restore (whole stage)                                         | D1 semantics; status==STAGED                                                        | voids/restores the entire rowset stage                                          |

Fold state machine for the terminal event (mirrors D2's rows):

| Status        | Event                               | New state                                                                                        |
| ------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| APPLY_PENDING | `write.applied {result: "applied"}` | APPLIED — terminal                                                                               |
| APPLY_PENDING | `write.applied {result: "partial"}` | PARTIALLY_APPLIED — terminal in D3; per-row outcomes on `RowState.apply_outcome`                 |
| APPLY_PENDING | `write.applied {result: "failed"}`  | STAGED, apply consumed (stances intact) — fresh apply may retry (D2's approval-consumed pattern) |

### Apply pipeline (API → durable queue → worker; never inline — D2 discipline)

1. `apply_rows` emits `decision.recorded {approve, scope: {row_keys}, actor: "user",
apply: true}`, then enqueues via the D2 `commit_queue` duck-type
   (`enqueue_stage_commit`) a `RuntimeStageCommitCommand` extended **additively** with
   `row_keys: tuple[str, ...] | None = None` (None = D2 single-artifact),
   `decision_seq` = the apply event's `sequence_no`. `commit_queue is None` ⇒ decision
   records, nothing executes (fail-open to no-commit, never to execution).
2. `RuntimeStageCommitHandler.handle` branches on `command.row_keys is not None` →
   rowset gate (fail-closed): fold status == APPLY_PENDING, apply decision at
   `command.decision_seq` covers exactly `command.row_keys`, every key folds
   `will_apply`, none already has an `apply_outcome`. Any mismatch ⇒ warn-log + no-op,
   no event.
3. Per-row loop (handler orchestrates; engine keeps the four invariants per row): build a
   `StageCommitRequest` per row with additive fields `row_key: str | None = None`,
   `row_args: JsonObject | None = None` (from `StagedRow.target_args`, verbatim —
   WYSIWYG); `commit_key()` appends `:{row_key}` when set
   (`"{stage_id}:{rev}:{decision_seq}:{row_key}"`); `tool_arguments()` returns
   `row_args` verbatim when set. Each row runs `engine.commit(...)`: replay check →
   precondition re-check (`read_remote_state`; `None` ⇒ skip, per D2 — the seam exists
   for connectors with read-back) → claim **before** side effect → one MCP call via
   `McpStageCommitConnector` → complete. Row failures (`FAILED`/`DRIFT_ABORTED`/
   `INDETERMINATE`) become `row_results` entries — the loop never aborts.
4. Terminal emission (handler, via the A3 emitter, `source=SYSTEM`): one `write.applied`
   with `row_keys` = the dispatched set, `result` `"applied"` (all succeeded) /
   `"partial"` (mixed) / `"failed"` (all failed), `row_results` per row, audit rows per
   D2's constants. Held rows are **never dispatched** — not on apply, not on retry, not
   under bypass.

### Allow-always branch (FR-C8)

At the end of `stage_rowset`, resolve `EffectiveActionPolicyResolver.resolve(...)` for
`(target_connector, target_op)`. If `bypass` is true: emit `decision.recorded {approve,
scope: {row_keys: <all rows minus agent_holds>}, actor: "policy", apply: true}` and
enqueue the same command — same pipeline, same gate, same engine. Rows named in
`agent_holds` are excluded unconditionally; **there is no code path from `agent_holds`
to auto-approval**. E1 derives "auto-applied under allow-always" from `actor: "policy"`.
If `bypass` is false, nothing auto-applies.

### Propose seam — builtin agent tool (NEW)

`src/agent_runtime/capabilities/tools/builtin/stage_rowset_write.py`, modeled on
`ask_a_question.py` (input model + `_Fields`/`_Limits`/`_Messages` nested constants):

```python
class StageRowsetWriteInput(RuntimeContract):
    target_connector: str; target_op: str; title: str
    rows: tuple[StagedRow, ...]             # the rowset.py contracts, reused verbatim as
    agent_holds: tuple[AgentHold, ...] = () # tool input — they already validate every
                                            # field (row_key/title/target_args/changes,
                                            # row_key/reason); no separate "*Input" mirror
                                            # types are introduced. Tool input is untrusted
                                            # until RowsetValidator runs (Guardrails).

class StageRowsetWriteTool:
    name: str = Values.Tool.STAGE_ROWSET_WRITE   # NEW constant "stage_rowset_write"
                                                 # in agent_runtime/api/constants.py
                                                 # (class Tool, L230; beside ASK_A_QUESTION)
    async def ainvoke(self, ...) -> dict: ...
```

The tool validates, calls `WriteStager.stage_rowset(...)`, and returns
`{stage_id, surface_id, rows_staged, rows_pre_held, status}` to the model. It does
**not** interrupt the graph — staging is non-blocking (NFR-7); decisions happen on the
surface while the run continues (same as D1's draft path, which never suspends
LangGraph). Registration follows the established **Wave-1 gated-tool pattern** (confirmed
in the repo): the factory takes fully-built gated tools as injected parameters and appends
them **last** — see `src/agent_runtime/execution/factory.py` ~L421–428, where
`code_mode_tool` / `sandbox_execute_tool` are appended when not `None` — rather than
constructed inline beside `AskAQuestionTool`. So `stage_rowset_write` is built per-run by
the worker when `SurfacesV2Flag.enabled()` (mirroring the wiring in
`src/runtime_worker/capability_tool_wiring.py` and its injection into the graph state in
`src/runtime_worker/handlers/run.py` ~L1067–1071) and passed to the factory as a parameter;
flag off ⇒ the tool is `None`, is never appended, and does not exist in the model's tool
surface. The stager reaches the ledger via the run-bound `WorkLedgerEmitter.active()` seam.
VERIFY AT IMPL: the exact factory parameter name / graph-state key for the injected
`stage_rowset_write` tool and the A3 emitter-seam symbol, as merged.

### HTTP API

Extend `src/runtime_api/http/stages.py` + `src/runtime_api/schemas/stages.py` (D1
files); all routes `RequireScopes(RUNTIME_USE)`, registered only when `SURFACES_V2` on:

- `POST /v1/agent/stages/{stage_id}/decisions` — body gains the row scope:
  `{decision: "approve"|"hold"|"reject"|"restore", rev?: int, row_keys?: [str]}` with a
  model validator: exactly one of `rev`/`row_keys`. Per-verb scope on a **rowset** stage
  (derived from the decision matrix; the stager enforces once the stage kind is known):
  `approve`/`hold` require `row_keys` (stance toggle — a `rev`-scoped `approve`/`hold` on a
  rowset is 422; rowset execution is the `/apply` route only), `reject`/`restore` require
  `rev` (whole-stage, D1 semantics). On a **single-artifact** (D1) stage the D1 rules are
  unchanged (`approve {rev}` still enqueues). This route **never** enqueues a rowset apply.
- `POST /v1/agent/stages/{stage_id}/apply` — NEW, defined here (D2 added no apply
  route); body `{rev: int, row_keys: [str]}` → `StagedWriteView`.
- `GET /v1/agent/stages/{stage_id}` — `StagedWriteView` now carries `rows` +
  `row_counts`.

Facade: passthroughs beside D1's in `services/backend-facade/src/backend_facade/app.py`
(pure proxy, no logic). TS mirrors added **additively** to
`packages/api-types/src/index.ts` (`StagedRow`, `RowState`, `RowStance`,
request/response shapes).

### Client (chat-surface + hosts) 🎨

- Extend the D1 `stages` fold in `packages/chat-surface/src/thread-canvas/ledgerProjection.ts`:
  rows from `revision.added.rowset`, stances from `decision.recorded` row scopes
  (last-write-wins per key, `actor` kept), frozen from `apply: true`, outcomes from
  `write.applied.row_results`, sticky `agentHoldReason`. Pure selector over the same
  event array — no new subscription.
- NEW `packages/chat-surface/src/surfaces/staged/StagedTableSurface.tsx` — per-row title
  - old→new field diffs, per-row Approve/Hold toggle, agent pre-hold warning chip
    rendered as `{reason} — agent pre-held` and **still visible after override** (FR-C7),
    live counts header ("6 will apply · 2 held"), applied / partial / result states
    ("7 updated · 1 held, untouched" — FR-C9 format), B2 provenance footer (access class
    "write · held"), ledger-id chip.
- NEW `packages/chat-surface/src/surfaces/staged/BulkApplyBar.tsx` — label exactly
  "Apply {N} changes →" (N = current will-apply count), pledge microcopy exactly
  "Writes apply only to rows you approve. Held rows stay untouched." (FR-C6,
  contract-grade), disabled at N=0, busy state while APPLY_PENDING. Kit recipes only
  (`.ui-button--primary`, `.ui-pill`, `SectionLabel`, `Badge`).
- Canvas mapping: surfaces with `kind: "table"` + a matching `write.staged` render
  `StagedTableSurface` (D1 mapped `kind: "message"` → `StagedDraftSurface`).
- Host wiring: callbacks only, via the `Transport` port to the facade routes — web
  `apps/frontend/src/features/run/RunRoute.tsx`, desktop
  `apps/desktop/renderer/destinationBinders.tsx`. On 409: refetch `GET /stages/{id}`,
  re-render, non-modal "Rows changed — review again" notice. Barrel exports in
  `packages/chat-surface/src/index.ts`.

### Error behavior summary

Typed domain errors, safe public messages: 404 unknown stage / `unknown_row_key` / flag
off; 403 foreign org/user (mirror D1's scope check); 409 `apply_set_mismatch` |
`stage_frozen` | `stale_revision`; 422 malformed body, rowset over caps, rev-scoped
`hold`, both/neither of `rev`/`row_keys`. Every 4xx emits **no ledger event**. Row-level
commit failures are data (`row_results`), never exceptions out of the handler; the
worker never retries a row after its claim exists (D2's INDETERMINATE branch, per row).

## Implementation plan

1. **Contracts.** `src/agent_runtime/surfaces_v2/rowset.py` (NEW): `RowFieldChange`,
   `StagedRow`, `AgentHold`, `RowStance`, `RowState`, `RowCounts`, `RowsetValidator`.
   New `Keys.Field`/`_Fields` constants + allow-list projections in
   `src/runtime_api/schemas/common.py` / `events.py`. Additive TS mirrors in
   `packages/api-types/src/index.ts`.
2. **Fold.** Extend `src/agent_runtime/surfaces_v2/staging.py`: `StagedWriteState.rows`
   / `row_counts`, `APPLY_PENDING` / `PARTIALLY_APPLIED`, row-scope + `apply: true`
   handling in `StagedWriteFold.fold`.
3. **Stager.** `WriteStager.stage_rowset` / `record_row_decision` / `apply_rows` +
   decision-matrix extension + allow-always branch (C1 resolver injected additively).
4. **Command + engine.** Additive `row_keys` on `RuntimeStageCommitCommand`
   (`src/runtime_api/schemas/commands.py`); additive `row_key`/`row_args` +
   row-scoped `commit_key()` on `StageCommitRequest`
   (`src/agent_runtime/surfaces_v2/commit_engine.py`); rowset branch + per-row loop +
   `row_results` accounting + terminal `applied|partial|failed` emission in
   `src/runtime_worker/handlers/stage_commit.py`.
5. **Tool.** `src/agent_runtime/capabilities/tools/builtin/stage_rowset_write.py` (NEW)
   - `Values.Tool.STAGE_ROWSET_WRITE` in `src/agent_runtime/api/constants.py` +
     flag-gated registration in `src/agent_runtime/execution/factory.py` + export in
     `src/agent_runtime/capabilities/tools/builtin/__init__.py`.
6. **Routes.** Extend `src/runtime_api/http/stages.py` + `schemas/stages.py` (row-scope
   decision body, `/apply`, row-bearing `StagedWriteView`); facade passthroughs in
   `services/backend-facade/src/backend_facade/app.py`.
7. **Client.** Extend `ledgerProjection.ts`; add `StagedTableSurface.tsx` +
   `BulkApplyBar.tsx` under `packages/chat-surface/src/surfaces/staged/`; canvas kind
   mapping; barrel exports; host callbacks (web `RunRoute.tsx`, desktop
   `destinationBinders.tsx`).
8. **Golden fixtures.** Extend the A1 golden-event fixture + expected-fold snapshots
   with a rowset scenario (stage 8 / 2 pre-held / override / apply 7 / partial variant);
   both folds (py + ts) assert against it.
9. **Tests + parity + smoke** (below).

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <file>`; fakes/mixins
per tests/CLAUDE.md — no network, no live LLM; assert typed error class + safe message):

- `tests/unit/agent_runtime/surfaces_v2/test_rowset_stager.py` —
  `test_stage_rowset_emits_table_surface_staged_and_rev_one`,
  `test_row_caps_and_duplicate_row_keys_rejected_422_no_event`,
  `test_holds_must_reference_existing_rows`,
  `test_row_decision_toggles_stance_and_emits_row_scope`,
  `test_row_stance_toggle_never_enqueues` (adversarial: spy queue stays empty),
  `test_override_pre_held_row_keeps_reason_sticky`,
  `test_rev_scoped_hold_still_422`, `test_foreign_user_cannot_decide_403_no_event`.
- `tests/unit/agent_runtime/surfaces_v2/test_rowset_fold.py` — golden rowset events →
  expected `StagedWriteState` (stances, counts, `APPLY_PENDING` freeze, outcomes,
  `PARTIALLY_APPLIED`, failed ⇒ STAGED apply-consumed); interleaved non-stage events
  tolerated; re-fold after "restart" identical; last-decision-wins per row_key.
- `tests/unit/agent_runtime/surfaces_v2/test_rowset_apply.py` (adversarial core, DoD) —
  `test_apply_emits_apply_decision_then_enqueues_exact_set`,
  `test_apply_set_mismatch_409_no_event_no_enqueue`,
  `test_duplicate_apply_idempotent_zero_additional_side_effects`,
  `test_allow_always_auto_applies_unflagged_rows_actor_policy`,
  `test_allow_always_never_dispatches_pre_held_rows` (FR-C8, DoD),
  `test_ask_first_policy_never_auto_applies`.
- `tests/unit/runtime_worker/test_stage_commit_rowset.py` —
  `test_dispatches_only_commanded_rows_held_rows_zero_connector_traffic` (spying fake
  connector; row `target_args` byte-equal), `test_per_row_claim_written_before_side_effect`,
  `test_row_failure_mid_apply_yields_partial_and_row_results`,
  `test_all_rows_failed_yields_failed_and_stage_returns_to_staged`,
  `test_duplicate_command_is_inert_per_row_claims`,
  `test_gate_mismatch_noops_without_event` (wrong set / stale seq / non-pending stage).
- `tests/unit/agent_runtime/tools/test_stage_rowset_write.py` — input validation; stage
  summary returned; no graph interrupt; flag off ⇒ tool absent from the assembled tool
  tuple (factory test); stager errors become safe tool-result errors.
- `tests/unit/runtime_api/test_stage_rowset_routes.py` — decision-body validator
  (exactly one of rev/row_keys); `/apply` happy path; 409/404/422 cells; flag off ⇒
  404; `StagedWriteView` carries rows + counts.
- Extend `tests/unit/runtime_api/test_stage_no_bypass.py` (D1/D2, never weaken) —
  random sequences of stage/row-decide/apply calls never yield `write.applied` covering
  a row without a matching apply-scoped approve covering that exact `row_key` at that
  rev; zero connector traffic for held rows across all sequences; `write.applied` still
  has exactly one producer.
- Facade: extend the D1 stage-routes proxy test (convention:
  `services/backend-facade/tests/test_*_proxy.py`, e.g. beside
  `test_approval_decision_proxy.py`).
- v1 + D1/D2 regression: D1's flag-off snapshot, D2's engine/handler suites, and the
  PRD-09 v1 suites all stay green untouched.

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`):

- `packages/chat-surface/src/surfaces/staged/StagedTableSurface.test.tsx` — per-row
  diffs render; toggle callbacks carry row_key; pre-hold chip text
  `{reason} — agent pre-held` visible before AND after override; counts header; applied
  / partial / "N updated · M held, untouched" states; 409 → refetch → notice.
- `packages/chat-surface/src/surfaces/staged/BulkApplyBar.test.tsx` — "Apply {N}
  changes →" tracks the will-apply count live; pledge microcopy exact; disabled at 0;
  apply callback sends `{rev, row_keys}` = the displayed set; busy while frozen.
- `ledgerProjection` row-fold cases + ts↔py golden-fixture parity extension (rowset
  scenario deep-equals the py snapshot).

Live-smoke script (desktop stack, real services):

1. `make dev` with `SURFACES_V2=true RUNTIME_START_IN_PROCESS_WORKER=true` in
   `services/ai-backend/.env`; `export TOKEN=$(make dev-bearer)`.
2. Connect a write-capable MCP connector (e.g. Linear or GitHub via the catalog OAuth
   flow — recipes in `docs/dev-testing.md`; facade `:8200` only); write policy ask-first.
3. Prompt a run that bulk-updates 8 items ("update the priority of these 8 issues …",
   instructing the agent to pre-hold 2 named rows with reasons).
4. Canvas shows the table: 8 rows, per-row diffs, "6 will apply · 2 held", reasons
   inline; `GET /v1/agent/runs/{run_id}/events` shows `write.staged` (rows: 8,
   agent_holds: 2) + `revision.added` rev 1 with `rowset`.
5. Override one pre-held row → warning chip stays; bar reads "Apply 7 changes →".
6. Apply → replay shows `decision.recorded {approve, scope:{row_keys:[7]}, actor: user,
apply: true}` + `write.applied {row_keys:[7], result: applied, row_results}`; verify
   in the real connector that exactly 7 items changed and the held item did not;
   surface reads "7 updated · 1 held, untouched".
7. Reload the app → canvas reconstructs identically from replay.
8. Flip the connector to allow-always, rerun a 3-row stage with 1 pre-hold → 2 rows
   auto-apply (`actor: policy` in replay), pre-held row still held; posture chip amber.
9. Flag-off rerun: tool absent from the model's tool surface, stage routes 404, event
   stream has no v2 events.

Design parity: vendor the bulk-table region of the v2 mock (`Generative Surfaces
v2.dc.html`, Claude Design project `ceb081f6`, walkthrough part 04) into
`tools/design-parity/surfaces/v2-staged-table/design/` (VERIFY AT IMPL: local mirror
location; DesignSync per `tools/design-parity/SKILL.md` if absent), then run the
pipeline → `tools/design-parity/surfaces/v2-staged-table/out/report.md`.

## Definition of done

From `../03-prds.md` PRD-D3 (binding, never weakened):

- [ ] **Live bulk flow: stage 8, override a pre-held row, apply 7, verify 7 applied +
      1 untouched in the real connector and in ledger/receipt rows.** Proof: live-smoke
      steps 3–7 executed on the desktop stack; replay JSON of the run attached to the PR.
- [ ] **Allow-always connector: unflagged rows auto-apply, pre-held rows still hold
      (test).** Proof: `test_allow_always_auto_applies_unflagged_rows_actor_policy` +
      `test_allow_always_never_dispatches_pre_held_rows` green; live-smoke step 8.
- [ ] **Parity: table surface + row decisions vs mock, 0 HIGH.** Proof:
      `tools/design-parity/surfaces/v2-staged-table/out/report.md` checked in, 0 HIGH rows.

Standard DoD (every PRD):

- [ ] Unit tests in ai-backend venv + facade venv + chat-surface/api-types workspaces
      pass; `npm run typecheck` green for both TS packages; full ai-backend suite green;
      D1/D2 + PRD-09 v1 suites untouched-green.
- [ ] Flags off ⇒ byte-identical behavior — proof: factory test (tool absent),
      stage-routes-404 test, live-smoke step 9; D1's flag-off snapshot test still green.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports;
      chat-surface eslint clean).
- [ ] No new LLM call sites (D3 has none); if any appear they go through the A2
      UsageMeter seam.
- [ ] Docs: update `../02-sdr.md` §5/§7-S4 if implementation diverges (at minimum: note
      the additive `rowset` / `apply` / `row_results` payload keys under §5).

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components — no host-app one-off
      styling, no raw font-size/letter-spacing (design-system SKILL.md rule).
- [ ] `tools/design-parity/` run vs the v2 mock bulk-table region: **0 HIGH drift**.
- [ ] Live desktop smoke of the flow on the real stack (script above), not just tests.

## Out of scope

- Per-cell/field **editing** of staged rows (FR-C4 for structured payloads): the D1
  revision seam (`revision.added`) is the extension point; a follow-up PRD adds rowset
  revisions — D3 rowsets are rev-1, agent-authored only.
- Mid-apply failure **UX** (retry affordances, error styling) — Phase-2 designer track;
  D3 ships correct `partial`/`failed` states and ledger events only.
- Receipt/Sources rendering of row decisions (E1); Approvals-queue row-set cards (E2).
- Changing single-artifact (D1/D2) behavior; v1 draft-approval flow; v1
  `result["surface"]` emission (compat window ends in E3).
- Remote-state capture for rows beyond D2's `read_remote_state` seam (connector-specific
  read-backs are per-catalog follow-ups); connector-side batch APIs (each row = one MCP
  call; batching is a later optimization).
- Settings/usage UI; classification catalog growth (C1 owns catalog data).

## Guardrails

- **Service boundaries (hard):** apps call `backend-facade:8200` `/v1/*` only — never
  `:8000`/`:8100`; facade proxies verbatim; no deployable component imports another's
  `src/`; no sibling `PYTHONPATH` additions; contracts move only via
  `packages/api-types` / `packages/service-contracts`. Policy storage stays in backend
  (C1); ai-backend consumes it via internal API only. Connector dispatch lives in the
  worker — never in the API request path, never in backend.
- **Fail-closed core (SDR §10):** `write.applied` keeps exactly one producer (the D2
  worker handler); rows execute only under a matching apply-scoped approve covering that
  exact `row_key` at that rev; per-row claims precede side effects; held rows are never
  dispatched under any policy; `agent_holds` can never be auto-approved by the policy
  actor; mismatched apply sets 409 with zero side effects. The adversarial suite is the
  gate, not review.
- **Flag-off byte-identical:** with `SURFACES_V2` unset/off, the model's tool surface,
  route table, queue content, and event stream are byte-for-byte today's.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every IO/domain
  boundary — no long-lived `dict[str, Any]`; helpers live inside classes; repeated
  keys/messages as nested `Keys`/`_Fields`/`_Messages` constants; typed domain errors
  with safe public messages; tool input (rows, reasons, target_args) is untrusted until
  validated — hold reasons are rendered UI text: length-cap, treat as plain text; the
  stager never touches an MCP client — only the CommitEngine path dispatches.
- **ai-backend tests** (`tests/CLAUDE.md`): fakes/mixins, never network or live LLMs;
  concrete test classes contain only `test_*` methods; assert typed error class + safe
  message; cover permission-denial and malformed-input paths; spying fakes assert zero
  side effects on every refusal branch.
- **chat-surface** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic — no
  `window`/`document`/`fetch`/`localStorage`/`EventSource` (eslint-enforced); all IO via
  the `Transport` port; no `apps/*` imports; row state is a pure extension of the one
  projector fold, never a second SSE subscription.
- **Event hygiene:** never derive activity types from event-name prefixes; new payload
  keys go through projector allow-lists; the ledger is append-only — a wrong stance is
  corrected by a new decision event, never by mutating history.

## Open questions

- **Inline `rowset` payload vs. offload at cap scale.** The Design section carries the
  full row data inline on `revision.added` (the ADDITIVE `rowset` key) _and_ keeps D1's
  `diff_ref`. At the caps defined here (MAX*ROWS=200 × MAX_CHANGES_PER_ROW=20) the inline
  `rowset` will very likely exceed the event pipeline's payload-offload threshold, so the
  implementer must resolve at impl (marked "VERIFY AT IMPL" in Design): does A3's
  payload-offload apply transparently to the `rowset` key so the client/server fold
  hydrates it via the payload-ref with no fold change, or must D3 add an explicit hydrate
  step (and/or carry the rowset only via `diff_ref` like D1, dropping the inline copy)?
  Decision needed before the fold is written, because it changes whether the fold reads
  `revision.added.rowset` directly or resolves a ref. Default if A3 offload is
  transparent: keep the inline key and let A3 handle it; otherwise follow D1's `diff_ref`
  offload and hydrate on fold. Does not affect the event \_vocabulary* (SDR §5), only
  payload transport.
