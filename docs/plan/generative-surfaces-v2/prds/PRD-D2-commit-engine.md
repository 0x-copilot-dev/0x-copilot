# PRD-D2 — CommitEngine: execute exactly what was approved

Give the v2 staged-write pipeline its execution stage: an approved revision — and only an
approved revision — is dispatched to the real target connector through the existing MCP
client machinery, guarded by four fail-closed invariants (approval gate, idempotency
claim written before the side effect, precondition re-check with drift abort, audit).
The outcome is ledgered as `write.applied` (`result: applied|failed`); receipt row and
surface state derive from that event alone. `write.applied` gains exactly one producer
(the worker-side CommitEngine), duplicate approve/apply attempts are provably inert, and
the v1 draft-approval flow keeps working byte-identically with the flag off.

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
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_worker/test_stage_commit_handler.py
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/
cd services/ai-backend && .venv/bin/python -m pytest            # full suite before PR
cd services/backend-facade && .venv/bin/python -m pytest
npm run test --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/api-types
```

Read these files first (paths relative to repo root):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 vocabulary + invariant paragraph
   (this PR implements it), §7 S3, §10 items 2/6, §11 compat rules.
2. `docs/plan/generative-surfaces-v2/prds/PRD-D1-staged-write-engine.md` — the stage
   engine this PR extends; D1 merges first; its "Exposed" section is your consumed
   contract.
3. `services/ai-backend/src/agent_runtime/capabilities/surfaces/commit.py` — the v1
   commit-executor island (PRD-09b): four-invariant ordering in
   `SurfaceCommitExecutor.commit()` (L450+), `RemoteState`, `ConnectorCommitResult`,
   `CommitLedgerPort` claim semantics, audit constants
   `AUDIT_COMMITTED = "surface.commit.committed"` /
   `AUDIT_ABORTED_DRIFT = "surface.commit.aborted_precondition_drift"` (L414–415) —
   your raw material. Do NOT reuse `SurfaceEdits`/`SurfaceEditMerger` (delta-merge; v2
   revisions are whole snapshots, D1 rule).
4. `services/ai-backend/tests/unit/agent_runtime/surfaces/test_commit_executor.py` —
   the adversarial suite pattern your v2 engine suite mirrors.
5. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py` —
   `CallMcpTool.ainvoke` (L58); the MCP dispatch seam is `registry.resolve_server` (L70)
   → `resolution.provider.create_client(resolution.card)` (L95) → `client.call_tool(
tool_name=, arguments=)` under `asyncio.wait_for` (L96–102) + typed-error mapping
   (L103–137). Your production
   connector replicates this seam outside the agent loop.
6. `services/ai-backend/src/runtime_worker/loop.py` — `RuntimeWorker.__init__` (L46),
   `_dispatch` (L218), `_runtime_approval_command` (L286). You add a fourth command
   type the same way.
7. `services/ai-backend/src/runtime_worker/handlers/approval.py` —
   `_resolve_draft_send_approval` (L706, the v1 "commit" v2 supersedes),
   `_next_draft_version` (L847), `_write_draft_audit` (L904).
8. `services/ai-backend/src/agent_runtime/persistence/ports.py` — `DraftStorePort`
   (`get_version` L107, `expect_status` L124), `OptimisticConflict`.
9. `services/ai-backend/src/agent_runtime/api/ports.py` — `RuntimeQueuePort` (L1049),
   the port you extend.
10. `services/ai-backend/src/runtime_worker/dependencies.py` —
    `DefaultRuntimeDependenciesFactory._mcp_registry` (L305);
    `RuntimeDependencies.mcp_registry` (`agent_runtime/execution/contracts.py` L534) is
    what your connector consumes.
11. `services/ai-backend/CLAUDE.md`, `services/ai-backend/tests/CLAUDE.md`,
    `packages/chat-surface/CLAUDE.md` — binding rules (see Guardrails).

## Context

Generative Surfaces v2 renders an agent's work on real SaaS tools as live artifact
surfaces on a per-run canvas; writes stage as revisioned proposals decided on the artifact
itself, with a what-you-approve-is-what-executes guarantee; everything threads through a
typed append-only Work Ledger event-sourced on the existing per-run runtime event log.
See `../01-problem-and-requirements.md` §2C (FR-C3 is this PR's contract) and NFR-4;
`../02-sdr.md` §3 (WriteStager + CommitEngine), §5 (vocabulary + fail-closed invariant),
§7 S3, §10 item 2.

This PR is `../03-prds.md` PRD-D2, second of Wave D. PRD-D1 delivered staging —
`write.staged` / `revision.added` / `decision.recorded`, the rev-pinned approve bar, and
an adversarial proof that **nothing** emits `write.applied`. D2 adds the single
legitimate producer: on an approve decision the API enqueues a durable commit command; a
worker-side CommitEngine re-validates the approval against the folded ledger, claims an
idempotency row, re-checks preconditions, dispatches the exact approved revision through
the real MCP client, flips the draft to `SENT`, and emits `write.applied` (`applied`, or
`failed` on drift/connector error — the branch Phase-2 failure-path designs will style).
PRD-D3 generalizes to row-sets; PRD-E1 folds these events into the receipt; PRD-E3
retires the v1 island.

## Interfaces consumed / exposed

**Consumed (must already be on `main`):**

- PRD-D1 (binding, from its Exposed section): `WriteStager`, `StagedWriteFold`,
  `StagedWriteState`, `StagedWriteStatus` (incl. forward-compat `APPLIED`) in
  `services/ai-backend/src/agent_runtime/surfaces_v2/staging.py`; payloads of
  `write.staged` (`target{connector,op}`, `proposal_ref`), `revision.added`
  (`proposal_ref = "draft://<draft_id>/v<version>"`), `decision.recorded`
  (`scope:{rev}`, `actor`); routes `POST /v1/agent/stages/{stage_id}/decisions`,
  `GET /v1/agent/stages/{stage_id}`; the decision matrix (approve pins latest rev; stage
  frozen after approve). VERIFY AT IMPL: exact merged signatures — D1 carried its own
  VERIFY markers.
- PRD-A1: contracts + event-type constants + ledger-id (`r<short>·<seq>`) formatter in
  `packages/service-contracts`. VERIFY AT IMPL: symbol names as merged.
- PRD-A3: the v2 ledger emitter (wrapper over `append_api_event` stamping `v: 1`), the
  `SURFACES_V2` flag accessor, and the `payload_ref` persistence mechanics.
  VERIFY AT IMPL: emitter class name, flag accessor, and how A3 persists ref'd
  payloads — `connector_receipt_ref` must ride the same mechanism.
- PRD-A2: the UsageMeter seam exists; D2 adds **no** LLM call sites.
- Existing machinery reused, not modified: `DraftStorePort.get_version` /
  `expect_status` / `OptimisticConflict`; `DraftStatus.SENT`; the MCP dispatch seam;
  `RuntimeQueuePort` claim/retry lifecycle; `PersistenceValues.EventType`
  (`agent_runtime/persistence/constants.py` L79); duck-typed `write_audit_log`.

**Exposed (later PRDs rely on these; do not rename after merge):**

- The `write.applied` emission + payload shape — D3 adds `row_keys`/`partial`; E1
  receipt fold rows; E2 queue-card clearing.
- `CommitEngine` + `StageCommitConnector` + `StageCommitLedgerPort` — D3 injects the
  same engine row-scoped; E3 deletes the v1 island once these carry all traffic.
- Queue command `stage_commit_requested` + `RuntimeStageCommitCommand` — D3 reuses with
  row scope.
- The fold extension (`APPLIED` terminal; `failed` ⇒ held, approval consumed) — E1/E2
  render from exactly this state machine.

## Design

### Ledger event (SDR §5 verbatim; payload carries `v: 1`)

D2 emits exactly one new event type; the worker CommitEngine handler is its **only**
producer (extend D1's no-bypass grep test to pin this).

```text
write.applied  {v, stage_id, rev, result: "applied"|"failed", connector_receipt_ref?,
                failure?: {code: "precondition_drift"|"connector_error"
                                 |"attempt_indeterminate", detail},   # additive, failed only
                decided_by?: {actor: "user", decision_seq: int}}      # additive, receipt row
```

`row_keys` / `"partial"` from the SDR §5 line are D3's — they parse but are never emitted
here. Wire mechanics identical to D1's events: `RuntimeApiEventType` member
`WRITE_APPLIED = "write.applied"` in `src/runtime_api/schemas/common.py`; allow-list
payload projection + `activity_kind` + display title in `src/runtime_api/schemas/events.py`
following `_surface_spec_generated_payload` (L670). Display titles (`_Fields` constants):
`applied` ⇒ exactly **"Sent — exactly the revision you approved."** (FR-C3 requirement
microcopy); `failed` ⇒ **"Apply refused — nothing was sent."** (Phase-2 polishes wording).
`connector_receipt_ref` (NEW format, defined here): `"commit://<stage_id>/<decision_seq>"`,
resolving to the persisted raw `ConnectorCommitResult` via A3's payload-ref store. Ledger
ids shown in UI are `r<short>·<seq>` via the A1 formatter.

### State machine (extends D1's fold; fail-closed)

| Current status   | Event                            | New state                                                                                                         |
| ---------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| APPROVED (rev N) | `write.applied {rev N, applied}` | APPLIED — terminal; further decisions/revisions ⇒ 409 `stage_frozen`                                              |
| APPROVED (rev N) | `write.applied {rev N, failed}`  | STAGED, `approved_rev` cleared — **approval consumed**; surface shows held state; fresh approve required to retry |
| anything else    | `write.applied`                  | fold marks the stage `corrupt` (defensive; assert unreachable in tests)                                           |

One approve authorizes **at most one** commit attempt — that is what makes duplicate
approve/apply inert end-to-end: idempotent re-approve emits no second decision event ⇒
no second enqueue; a redelivered queue command hits the claim ledger.

### Command pipeline (API → durable queue → worker; never inline)

Mirrors the v1 approval-resolution discipline ("resume is never inline").

1. `agent_runtime/persistence/constants.py`: add
   `Values.EventType.STAGE_COMMIT_REQUESTED = "stage_commit_requested"`.
2. `src/runtime_api/schemas/commands.py`: NEW `RuntimeStageCommitCommand` beside
   `RuntimeApprovalResolvedCommand` (L48):

```python
class RuntimeStageCommitCommand(RuntimeContract):
    stage_id: str; run_id: str; org_id: str; user_id: str
    conversation_id: str
    rev: PositiveInt
    decision_seq: int          # sequence_no of the decision.recorded{approve} event
    trace_propagation: dict[str, str] = Field(default_factory=dict)  # mirrors RuntimeApprovalResolvedCommand (commands.py L70)
```

3. `src/agent_runtime/api/ports.py`: add
   `async def enqueue_stage_commit(self, command: RuntimeStageCommitCommand) -> None` to
   `RuntimeQueuePort`; implement in all three adapters
   (`src/runtime_adapters/{in_memory,postgres,file}/runtime_api_store.py`), copying
   `enqueue_approval_resolved` in each.
4. Enqueue site: D1's `WriteStager.record_decision` gains an optional ctor arg
   `commit_queue: object | None = None` (duck-typed on `enqueue_stage_commit`, same
   optional-injection style as `DraftService`'s `event_producer`). Enqueue fires **only
   when a new `decision.recorded{approve}` event was actually emitted in this call** —
   idempotent replays and reject/restore never enqueue. `commit_queue is None` ⇒
   decision records, nothing executes (fail-open to no-commit, never to execution).
   Wire the real queue in `src/runtime_api/app.py` at D1's `WriteStager` construction
   site (VERIFY AT IMPL: expected near the draft-store fallback L530–543).
5. `src/runtime_worker/loop.py`: route
   `PersistenceValues.EventType.STAGE_COMMIT_REQUESTED` →
   `self.stage_commit_handler.handle(command)` in `_dispatch`; add
   `_runtime_stage_commit_command(claim)` decode (pattern: `_runtime_approval_command`
   L286); add `stage_commit_handler` to `RuntimeWorker.__init__` with a default
   construction like `approval_handler`'s; add the `_COMMAND_NAMES` entry. Thread
   `draft_store` from `src/runtime_worker/__main__.py` (~L88, as for approvals).

### CommitEngine (worker-side domain; `src/agent_runtime/surfaces_v2/commit_engine.py`, NEW)

Contracts (RuntimeContract; reuse `RemoteState` and `ConnectorCommitResult` imported from
`agent_runtime.capabilities.surfaces.commit` — they are model-only, safe to share):

```python
class StageCommitRequest(RuntimeContract):
    org_id: str; user_id: str; run_id: str; conversation_id: str
    stage_id: str; rev: PositiveInt; decision_seq: int
    target_connector: str; target_op: str
    body: str                          # the approved rev's content_text, verbatim
    title: str = ""
    target_metadata: JsonObject = Field(default_factory=dict)  # from the DraftRecord row
    def commit_key(self) -> str:       # idempotency identity: one attempt per approve
        return f"{self.stage_id}:{self.rev}:{self.decision_seq}"
    def tool_arguments(self) -> JsonObject: ...
        # {"body", "title"?, "target_metadata"?} — mirror of
        # CommitRequest.tool_arguments(), capabilities/surfaces/commit.py L149

class StageCommitStatus(StrEnum):
    COMMITTED = "committed"; IDEMPOTENT_REPLAY = "idempotent_replay"
    DRIFT_ABORTED = "drift_aborted"; FAILED = "failed"; INDETERMINATE = "indeterminate"

class StageCommitOutcome(RuntimeContract):
    status: StageCommitStatus; commit_key: str
    result: ConnectorCommitResult | None = None
    failure_code: str | None = None    # values = the event failure codes above

class StageCommitConnector(Protocol):  # the ONLY object touching an external system
    async def read_remote_state(self, request: StageCommitRequest) -> RemoteState | None: ...
    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult: ...

class StageCommitLedgerEntry(RuntimeContract):  # NEW — v2 idempotency row, keyed by commit_key
    commit_key: str
    committed: bool = False
    result: ConnectorCommitResult | None = None
    # Do NOT reuse v1's CommitLedgerEntry (commit.py L192): it is keyed by `approval_id`,
    # a different identity from v2's `commit_key`. Only RemoteState/ConnectorCommitResult
    # are shared from the v1 island; this row is new.

class StageCommitLedgerPort(Protocol):  # claim is atomic check-then-act
    async def load(self, *, commit_key: str) -> StageCommitLedgerEntry | None: ...
    async def claim(self, *, commit_key: str) -> bool: ...
    async def complete(self, *, commit_key: str, result: ConnectorCommitResult) -> None: ...
```

`CommitEngine(connector: StageCommitConnector, ledger: StageCommitLedgerPort)` holds the
two ports and nothing else. Its single method
`CommitEngine.commit(request, captured_precondition: RemoteState | None)` is ordered,
ported one-to-one from `SurfaceCommitExecutor.commit()` (read-first item 3):

1. **Replay check** — `ledger.load(commit_key)`: committed entry ⇒ `IDEMPOTENT_REPLAY`
   (zero connector calls); claimed-but-incomplete ⇒ `INDETERMINATE` (a prior attempt
   crashed mid-send; at-most-once forbids resending — mark `complete` with a failed
   result so this branch fires exactly once).
2. **Precondition re-check** — `connector.read_remote_state(request)`; a non-None
   reading differing structurally from `captured_precondition` (when captured) ⇒
   `DRIFT_ABORTED`, no claim, no write.
3. **Claim before side effect** — `ledger.claim(commit_key)`; lost race ⇒
   `IDEMPOTENT_REPLAY`.
4. **Execute** — `connector.execute(request)`; timeout ⇒ `INDETERMINATE` (the send may
   have left the building — never resend); auth/connection/client errors ⇒
   `FAILED{connector_error}`.
5. **Complete** — `ledger.complete(commit_key, result)` ⇒ `COMMITTED`.

The engine performs **no event emission and no draft mutation** — the handler owns
those; the engine stays a pure port-driven, fully fakeable core like the v1 island.
`InMemoryStageCommitLedger` (asyncio.Lock, clone of `InMemoryCommitLedger` L339) plus
durable adapters `src/runtime_adapters/{postgres,file}/stage_commit_ledger.py` following
the draft-store adapter pattern. VERIFY AT IMPL: how `runtime_adapters/postgres` adds a
table (schema module `agent_runtime/persistence/schema/postgres.py` + `Values.MIGRATION_ID`
handling, `agent_runtime/persistence/constants.py` L72) — the claim row must survive worker restart or
at-most-once is fiction; the file adapter uses atomic temp-write→`os.replace` like
`FileSurfaceSpecStore`.

### Production connector (`src/agent_runtime/surfaces_v2/mcp_connector.py`, NEW)

`McpStageCommitConnector(dependencies_factory, timeout_seconds)` — the CallMcpTool
dispatch seam outside the agent loop: build `AgentRuntimeContext` for the run's
org/user/conversation/run (VERIFY AT IMPL: reuse the approval handler's context-building
helper); `registry = dependencies_factory(context).mcp_registry`; `resolution = await
registry.resolve_server(request.target_connector)` (an `McpLoadError` resolution ⇒ typed
`AgentRuntimeError` ⇒ `FAILED`); `client = resolution.provider.create_client(
resolution.card)`; `await asyncio.wait_for(client.call_tool(tool_name=request.target_op,
arguments=request.tool_arguments()), timeout=...)` with the typed-exception mapping of
`call_tool.py` L103–137. `read_remote_state` returns `None` in D2 (draft-send has no
remote precondition source; the local precondition below does the work; the seam exists
for D3 field-writes) — document this explicitly.

### Worker handler (`src/runtime_worker/handlers/stage_commit.py`, NEW)

`RuntimeStageCommitHandler(*, persistence, event_store, draft_store, engine=None,
connector=None, ledger=None, settings=None)` — keyword-only, mirroring
`RuntimeApprovalHandler.__init__` (`handlers/approval.py` L106, all-`*`). Default
construction (test injects its own `engine`): when `engine is None`, build
`CommitEngine(connector or McpStageCommitConnector(...), ledger or <adapter-selected
StageCommitLedgerPort>)`, where the ledger adapter is chosen off `settings`/`RUNTIME_STORE_BACKEND`
the same way `draft_store` is (in_memory / postgres / file).

Audit action strings are declared as module constants at the top of `stage_commit.py`
(not imported from the off-limits v1 island — redeclare the two shared values verbatim so a
byte diff proves equality): `_AUDIT_COMMITTED = "surface.commit.committed"`,
`_AUDIT_ABORTED_DRIFT = "surface.commit.aborted_precondition_drift"` (verbatim v1, commit.py
L414–415), and `_AUDIT_FAILED = "surface.commit.failed"` (NEW, this PR). `handle(command)`:

1. Fold `event_store.list_events_after(org_id=..., run_id=..., after_sequence=0)`
   (keyword-only, `api/ports.py` L1035) through `StagedWriteFold`;
   locate `command.stage_id`. **Approval gate (fail-closed):** status `APPROVED`,
   `approved_rev == command.rev`, approving decision's `sequence_no ==
command.decision_seq`. Any mismatch ⇒ warn-log + no-op, no event (the ledger records
   only what happened; stale commands are unreachable absent bugs — D1 freezes approved
   stages).
2. Resolve the approved rev's snapshot: parse its `proposal_ref`
   (`draft://<draft_id>/v<version>`) → `draft_store.get_version(...)` → build
   `StageCommitRequest` (`body = record.content_text`, `target_metadata =
record.target_metadata`, target from the `write.staged` payload).
3. **Local precondition:** the draft row must still be `SEND_PENDING_APPROVAL`;
   changed ⇒ emit `write.applied {rev, result: "failed", failure:
{code: "precondition_drift"}}` + audit `surface.commit.aborted_precondition_drift`
   (verbatim v1 constant) and stop.
4. `outcome = await engine.commit(request, captured_precondition=None)`:
   - `COMMITTED`: `expect_status` flip SEND_PENDING_APPROVAL → `SENT` as a new version
     (`_next_draft_version` pattern; `OptimisticConflict` here is log-and-continue — the
     send already happened); persist the raw result via A3's payload-ref mechanism; emit
     `write.applied {rev, result: "applied", connector_receipt_ref, decided_by}`; audit
     `surface.commit.committed` (verbatim constant) via duck-typed `write_audit_log`.
   - `DRIFT_ABORTED` / `FAILED` / `INDETERMINATE`: emit `write.applied {rev, result:
"failed", failure: {code}}` + drift-abort or `surface.commit.failed` (NEW action,
     defined here) audit row; draft status untouched, so a fresh approve can retry.
   - `IDEMPOTENT_REPLAY`: full no-op — the first attempt already emitted the event.
5. All emission goes through the A3 v2 emitter (stamps `v: 1`), `source=SYSTEM` like
   `SURFACE_SPEC_GENERATED`.

### Client (chat-surface — small; D2 is not a 🎨 PRD)

Extend B1's ledger projector fold with `write.applied`: `applied` ⇒ approve bar becomes
a static confirmation row ("Sent — exactly the revision you approved." + ledger-id
chip); `failed` ⇒ back to held with a non-modal warning line (failure code as text;
Phase-2 styles it). Extend the golden-event fixture + ts↔py parity test.
`StagedDraftSurface.tsx` (D1) renders both states from projector state only — kit
components, no new host UI, no new routes.

### Error behavior summary

API side unchanged from D1 (the decisions route neither waits for nor reports commit
outcomes; clients observe via SSE). Worker side: every abnormal path maps to a typed
domain error → `failed` outcome or a logged no-op; the handler must never raise out of
`handle()` in a way that retries the queue command **after** a claim exists (retry +
claim = the `INDETERMINATE` branch, asserted in tests). No ledger event is ever emitted
for a request that failed the approval gate.

## Implementation plan

1. **Contracts.** `WRITE_APPLIED` member + allow-list + display titles
   (`src/runtime_api/schemas/common.py`, `events.py`; keys as `_Fields`/`Keys.Field`
   constants); `Values.EventType.STAGE_COMMIT_REQUESTED`
   (`src/agent_runtime/persistence/constants.py`); `RuntimeStageCommitCommand`
   (`src/runtime_api/schemas/commands.py`); additive TS mirror of the `write.applied`
   payload in `packages/api-types/src/index.ts` if A1 did not already define it.
2. **Queue.** `enqueue_stage_commit` on `RuntimeQueuePort`
   (`src/agent_runtime/api/ports.py`) + the three
   `src/runtime_adapters/{in_memory,postgres,file}/runtime_api_store.py` adapters.
3. **Engine.** `src/agent_runtime/surfaces_v2/commit_engine.py`; durable ledger
   adapters `src/runtime_adapters/{postgres,file}/stage_commit_ledger.py`.
4. **Fold.** Extend `src/agent_runtime/surfaces_v2/staging.py` with the state-machine
   rows above.
5. **Connector.** `src/agent_runtime/surfaces_v2/mcp_connector.py`.
6. **Handler.** `src/runtime_worker/handlers/stage_commit.py`; wire into
   `src/runtime_worker/loop.py` (`__init__`, `_dispatch`, decode, `_COMMAND_NAMES`) and
   `src/runtime_worker/__main__.py`.
7. **Enqueue site.** `commit_queue` on `WriteStager.record_decision`; wire the queue in
   `src/runtime_api/app.py` at D1's WriteStager construction site.
8. **Client.** Extend B1's projector + golden fixtures; applied/failed states in
   `packages/chat-surface/src/surfaces/staged/StagedDraftSurface.tsx`.
9. **Tests + live smoke** (below). No facade change (D1's routes suffice) beyond a
   passthrough regression run.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <file>`; fakes only —
no network, no live LLM; assert typed error class + safe message):

- `tests/unit/agent_runtime/surfaces_v2/test_commit_engine.py` — mirror the v1 suite's
  classes:
  - `TestIdempotency` (adversarial, DoD):
    `test_replay_performs_zero_additional_side_effects` (committed entry ⇒ spy connector
    untouched); `test_claim_written_before_side_effect` (ordering via recording fake);
    `test_lost_claim_race_short_circuits`;
    `test_claimed_but_incomplete_entry_yields_indeterminate_exactly_once`.
  - `TestPrecondition` (adversarial, DoD): `test_remote_drift_aborts_no_write_no_claim`;
    `test_none_remote_state_skips_check`.
  - `TestErrorMapping`: timeout ⇒ INDETERMINATE; auth/connection ⇒ FAILED
    `connector_error`; `test_connector_never_called_without_claim`.
- `tests/unit/agent_runtime/surfaces_v2/test_staged_write_fold_applied.py` — applied ⇒
  APPLIED terminal (further decisions 409 `stage_frozen`); failed ⇒ STAGED with
  `approved_rev` cleared (held state — DoD item); golden-fixture replay parity.
- `tests/unit/runtime_worker/test_stage_commit_handler.py` —
  `test_committed_flow_dispatches_exact_approved_body` (spy asserts `arguments["body"]`
  == approved rev's `content_text`, byte-equal — FR-C3);
  `test_duplicate_command_is_inert` (DoD: same command twice ⇒ one connector call, one
  `write.applied`); `test_draft_status_changed_since_staging_refuses_ledgered_failed`
  (DoD drift item: draft off SEND_PENDING_APPROVAL ⇒ no connector call,
  `write.applied{failed, precondition_drift}`, audit row);
  `test_stale_command_stage_not_approved_noops_without_event`;
  `test_committed_flips_draft_to_sent_and_writes_audit`;
  `test_failed_leaves_draft_pending_so_fresh_approve_can_retry`;
  `test_write_applied_carries_rev_decided_by_and_receipt_ref`.
- `tests/unit/runtime_api/test_stage_commit_enqueue.py` — approve ⇒ exactly one enqueue
  with `{stage_id, rev, decision_seq}`; idempotent re-approve enqueues nothing (DoD);
  reject/restore/revision never enqueue; `commit_queue=None` ⇒ decision records, no
  enqueue; flag off ⇒ routes 404 (D1 test still green).
- Extend `tests/unit/runtime_api/test_stage_no_bypass.py` (never weaken): the producer
  test now asserts `write.applied` has exactly one producer (the stage-commit handler);
  random API sequences still yield zero `write.applied` without an approve and zero
  connector calls on the spying fake (the API process has no connector).
- Adapters (`tests/unit/runtime_adapters/{in_memory,postgres,file}/`): claim atomicity under
  concurrent `claim` calls (postgres + file); enqueue/claim round-trip for the new
  command type in all three `runtime_api_store` adapters.
- v1 regression: `test_approval_with_edits.py`, `test_draft_send_approve_with_edits.py`,
  `test_draft_send_resolution.py`, `test_commit_executor.py` stay green untouched.

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`): projector fold
`write.applied` cases (applied/failed) + fixture parity extension;
`StagedDraftSurface.test.tsx` applied confirmation row + failed held-with-warning state.

Live-smoke script (real stack; DoD "real MCP connector"):

1. `make dev` with `SURFACES_V2=true RUNTIME_START_IN_PROCESS_WORKER=true` in
   `services/ai-backend/.env`; `export TOKEN=$(make dev-bearer)`.
2. Register + authenticate a write-capable MCP server (recipes in
   `docs/dev-testing.md`; facade `:8200` only). VERIFY AT IMPL: which send-capable
   connector the dev stack offers; a locally-run MCP server exposing a
   `send_message`-style tool, registered through the real registry and dispatched
   through the real client, satisfies "real MCP connector" — record the choice in the PR.
3. Run the D1 smoke through approve: draft → edit → rev 2 →
   `POST /v1/agent/stages/{stage_id}/decisions`.
4. Verify: tool server received exactly the rev-2 body; replay
   `GET /v1/agent/runs/{run_id}/events` shows `write.applied {rev: 2, result:
"applied", decided_by, connector_receipt_ref}` (rev + actor + envelope time = DoD);
   canvas shows "Sent — exactly the revision you approved."; draft status `sent`.
5. Duplicate-approve probe: re-POST the same approve ⇒ 200 idempotent; replay shows one
   `write.applied`; tool server received exactly one call.
6. Drift probe: stage a second draft, mutate its status out-of-band (dev store
   manipulation), approve ⇒ `write.applied {failed, precondition_drift}`, surface back
   to held, zero tool calls.
7. Flag-off rerun: v1 approval flow works as today; no v2 events; no enqueues.

## Definition of done

From `../03-prds.md` PRD-D2 (binding, never weakened):

- [ ] **Live: approved draft actually sends via a real MCP connector; ledger row carries
      rev + actor + time.** Proof: live-smoke steps 2–4 with replay JSON attached to the
      PR; `test_committed_flow_dispatches_exact_approved_body` +
      `test_write_applied_carries_rev_decided_by_and_receipt_ref` green.
- [ ] **Precondition drift (target changed since staging) ⇒ apply refuses, ledgered as
      failed, surface shows held state (UX polish may be Phase-2, state must be correct).**
      Proof: `test_draft_status_changed_since_staging_refuses_ledgered_failed` +
      `TestPrecondition` + the fold `failed ⇒ held/approval-consumed` test + live-smoke
      step 6.
- [ ] **Duplicate approve/apply calls are inert (idempotency test).** Proof:
      idempotent-re-approve case + `test_duplicate_command_is_inert` + `TestIdempotency` +
      live-smoke step 5.

Standard DoD (every PRD):

- [ ] Unit tests in ai-backend + facade venvs and chat-surface/api-types workspaces
      pass; typechecks green; full ai-backend suite green; v1 suites untouched-green.
- [ ] Flags off ⇒ byte-identical behavior — D1's snapshot test still green; no enqueue
      and no v2 events with flag off (asserted); new port methods dormant.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports; no
      sibling PYTHONPATH).
- [ ] No new LLM call sites (D2 has none); any that appear go through the A2 UsageMeter
      seam.
- [ ] Docs: update `../02-sdr.md` §5 (additive `failure`/`decided_by` keys,
      `connector_receipt_ref` format) and §7 S3 if implementation diverges.

## Out of scope

- Row-set commits, `row_keys`, `result: "partial"`, per-row idempotency, allow-always
  auto-apply (PRD-D3).
- Receipt/Sources folds and rendering (E1); Approvals-queue cards (E2).
- Remote-state capture at stage time (`read_remote_state` is `None` in D2; the seam
  ships for D3 field-writes).
- Touching the v1 island (`agent_runtime/capabilities/surfaces/commit.py`) or the v1
  draft-approval flow — both retire in E3. This PRD supersedes the tracked follow-up
  "wire SurfaceCommitExecutor to a real send-connector" (`docs/plan/generative-ui/
STATUS.md`): the v2 engine + `McpStageCommitConnector` are that wiring.
- Failure-path visual polish (Phase-2 lands here — states/events must be correct now).
- Undo/rollback of applied writes; usage UI; any Settings work.

## Guardrails

- **Service boundaries (hard):** apps call `backend-facade:8200` `/v1/*` only; facade
  proxies verbatim (no commit logic in it); no deployable component imports another's
  `src/`; contracts move only via `packages/api-types` / `packages/service-contracts`.
  Connector dispatch lives in ai-backend's worker — never in the API request path, never
  in backend.
- **Flag-off byte-identical:** with `SURFACES_V2` unset/off, every wire payload, event
  stream, route table, and queue content is byte-for-byte today's; port-protocol
  additions must be behaviorally dormant.
- **The no-bypass invariant is the PR:** `write.applied` has exactly one producer; the
  producer runs only off a durable command that exists only because a
  `decision.recorded{approve}` event was emitted; the claim precedes the side effect.
  Anything that emits `write.applied` elsewhere, executes inline in the API, or retries
  a send after a claim is a rejected design.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every IO/domain
  boundary; no long-lived `dict[str, Any]` state; helpers inside classes; repeated
  keys/messages as nested `Keys`/message classes; broad exceptions → typed domain errors
  with safe public messages; connector responses are untrusted — allow-list projection
  only, never echoed raw into events; orchestration (handler) stays separate from
  connector side effects (connector class).
- **ai-backend tests** (`tests/CLAUDE.md`): fakes/mixins, never network or live LLMs;
  concrete test classes contain only `test_*` methods; cover denial and malformed-input
  paths; spying fakes assert zero side effects on every refusal branch.
- **chat-surface** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic (no
  `window`/`fetch`/`localStorage`); all IO via the `Transport` port; one event projector
  — applied/failed states are pure selectors over the same event array; no `apps/*`
  imports.
- **Event hygiene:** never derive activity types from event-name prefixes; the ledger is
  append-only — a failed commit is a new `write.applied{failed}` event, never a mutation
  of a prior one.
