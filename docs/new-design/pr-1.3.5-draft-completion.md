# PR 1.3.5 · Draft completion — wire the harness, real send flow, auth gate, permissions

PRD + Spec + Architecture for the follow-up that closes the four critical seams left by [PR 1.3](pr-1.3-draft-artifact.md).

> Scope discipline: every gap in this PR was carved out of PR 1.3 with an explicit comment in §3.5 of that doc; nothing here is feature creep. PR 1.3 shipped the persistence + wire format; PR 1.3.5 makes them load-bearing.

---

## 1 · PRD

### 1.1 Problem

PR 1.3 landed Draft persistence, the `DraftBackend` adapter, the runtime event type, and the HTTP/proxy/FE wire — but four seams keep the system from being functional in production:

1. **Run handler doesn't construct `DraftBackend`.** The factory accepts `drafts_backend` but the worker never constructs one, so the agent's `write_file("/drafts/...")` calls fall through to deepagents' default `StateBackend` and never persist or stream `DRAFT_UPDATED`.
2. **`POST /v1/agent/drafts/{id}/send` returns a placeholder `approval_id`** that's never converted into a real `runtime_approval_requests` row + `APPROVAL_REQUESTED` SSE event. The FE gets a persisted "send pending approval" draft but no inline approval card.
3. **No connector-auth pre-check on send.** Sending to a connector the user has not authenticated should return `409 connector_auth_required` so the FE can drop a `McpDiscoveryCard`. Today any string is accepted.
4. **No `FilesystemPermission` rule for `/drafts/`.** Subagents inherit unrestricted write access by default; the spec calls for explicit opt-in.

Plus integration tests (test_draft_flow.py + test_draft_send.py + test_draft_send_unauth.py) which can only be written once 1–3 land.

### 1.2 Goals

1. The agent's `write_file("/drafts/<uuid>.md", body)` call inside any run produces a persisted `runtime_drafts` row and an SSE `DRAFT_UPDATED` event with monotonic `sequence_no` — no extra plumbing required at call sites.
2. `POST /v1/agent/drafts/{id}/send` produces a real, FE-renderable approval card on the run's SSE stream, gated by the **existing** approval primitive (`runtime_approval_requests`) and dispatched through the **existing** capability registry / approval middleware. No new approval tables, no new audit chain.
3. Send to a non-authenticated connector returns `409` with `error_code = "connector_auth_required"` and the corresponding `mcp_server_id` (when the target is an MCP server) before any draft row mutation.
4. Subagents only get `/drafts/` write access when their `SubagentDefinition` explicitly grants it. Default subagents cannot write drafts; the supervisor's permission set always grants `/drafts/` writes.
5. Three new integration tests cover the agent-write path, the approve-and-send path, and the connector-not-authenticated recovery path.

### 1.3 Non-goals

- Adding a new database table. The existing `runtime_drafts`, `agent_runs`, `runtime_events`, `runtime_approval_requests`, `runtime_audit_log`, and `runtime_outbox_events` carry every fact this PR needs.
- Changing the `DRAFT_UPDATED` event payload shape. The send-resolution path emits a fresh `DRAFT_UPDATED` with the new version + status; FE reducer is already idempotent on `(draft_id, version)`.
- Connector-auth refresh / OAuth flows for the new gate. We surface the `mcp_server_id` and let the existing OAuth start endpoint do the work; PR 1.3.5 only adds the _check_.
- Multi-recipient send (Slack DM list, ticket fanout). Single-target only.
- Draft templates / library, reusable connector configs.
- Worker concurrency tuning specific to draft-sends. They use the existing `RUNTIME_MAX_PARALLEL_RUNS` budget like any other run.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Verified by                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| AC‑1  | A run handler constructs a `DraftBackend(store=…, org_id=…, conversation_id=…, run_id=…, user_id=…, emit_event=…)` per run and passes it through `RuntimeDependencies.drafts_backend`.                                                                                                                                                                                                                                                                                                                                | Run-handler unit test          |
| AC‑2  | An agent calling `write_file("/drafts/{uuid}.md", body)` persists a `runtime_drafts` row and emits a `DRAFT_UPDATED` event whose `sequence_no` is strictly greater than the immediately preceding `tool_call` event.                                                                                                                                                                                                                                                                                                  | E2E `test_draft_flow.py`       |
| AC‑3  | `POST /send` validates `target_connector` against the user's authenticated capability set; non-authenticated returns `409 {error_code:"connector_auth_required", mcp_server_id?:string}` _before_ any `runtime_drafts` write.                                                                                                                                                                                                                                                                                         | Route + service unit test      |
| AC‑4  | A successful `POST /send` creates a `runtime_drafts` row at v+1 with `status=send_pending_approval`, creates one `agent_run` (`runtime_context.kind="draft_send"`) and enqueues a `RuntimeRunCommand{kind:"draft_send"}`. The response carries the real `approval_id` once the worker has emitted it (clients can poll the run's `/events` or open `/stream`).                                                                                                                                                        | E2E `test_draft_send.py`       |
| AC‑5  | The worker's `draft_send` handler emits an `APPROVAL_REQUESTED` event with `approval_kind="action"` (not a new kind), payload describing the connector + summary; on approve, dispatches the connector tool through the existing capability registry; on success transitions the draft to `status=sent` (v+2) and audits `draft.send.completed`; on failure transitions to `status=send_failed` (v+2) and audits `draft.send.failed`; on reject transitions to `status=draft` (v+2) and audits `draft.send.rejected`. | E2E + worker handler unit test |
| AC‑6  | Subagents cannot write `/drafts/` unless their `SubagentDefinition` has `fs_permissions` granting write to `/drafts/`. The supervisor agent always retains write access.                                                                                                                                                                                                                                                                                                                                              | Permissions unit test          |
| AC‑7  | Cancel mid-send: cancelling the send run before the approval resolves transitions the draft to `status=draft` (v+2, audit `draft.send.cancelled`) and emits the standard `run_cancelled` event. No half-sent state.                                                                                                                                                                                                                                                                                                   | Cancel-race integration test   |
| AC‑8  | Cross-org isolation holds end-to-end: org B's POST /send to org A's draft returns `404` (RLS blocks the row read before any auth check).                                                                                                                                                                                                                                                                                                                                                                              | Isolation integration test     |
| AC‑9  | Send-flow performance: API send endpoint p95 ≤ 50ms in-memory / ≤ 80ms postgres (one tx, no connector I/O); worker pickup ≤ 1× `RUNTIME_WORKER_POLL_INTERVAL_SECONDS`; queue→approval-card-on-stream ≤ 1s end-to-end.                                                                                                                                                                                                                                                                                                 | Bench step in verification     |
| AC‑10 | Audit chain integrity: every state transition (`proposed → approved/rejected → completed/failed/cancelled`) writes one append-only chain entry with the existing HMAC + `prev_hash` linkage, exportable through the existing `/internal/v1/audit/export` endpoint.                                                                                                                                                                                                                                                    | Audit chain test               |

### 1.5 Risks

| Risk                                                                                                                       | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Worker handler dispatches the wrong connector tool (model output trusted as tool name).                                    | The worker resolves `target_connector` against the capability registry **at command time**, not from the model. The agent's `target_connector` write goes through the standard middleware that already validates the tool name; invalid names get rejected at the registry layer with a typed error and audit entry.                                                                                                    |
| Connector-auth state changes between API pre-check and worker dispatch.                                                    | The worker re-checks at dispatch time. If the user revoked the connector between send-request and approval-decision, the dispatch returns the same `connector_auth_required` error; the run completes with `send_failed` and the FE renders the `McpDiscoveryCard` for retry.                                                                                                                                           |
| Approval-fanout: a draft has multiple in-flight `send_pending_approval` versions.                                          | The send endpoint guards on `expected_version` (already in PR 1.3). Two concurrent sends → second gets `409 optimistic_conflict`. Only one outstanding send per draft at a time.                                                                                                                                                                                                                                        |
| Subagent regresses to default permissive `/drafts/` access on a missed permission rule.                                    | Permission rule is constructed in `_filesystem_permissions_for(subagent_definition)` and tested with both grant + deny cases. Default in `SubagentDefinition` is `fs_permissions=()` which excludes `/drafts/`.                                                                                                                                                                                                         |
| Cancel mid-tool-dispatch leaves a half-applied connector side effect (e.g., Slack message posted but draft still pending). | The runtime worker already handles cancel-during-tool through the existing tool-call ledger (`runtime_worker/tool_call_ledger.py`): the tool result event lands first, then the run terminal. Our handler observes the tool result before persisting `status=sent`, so cancel-after-tool still records `sent`. Cancel-before-tool records `draft.send.cancelled`. No new code needed for this property — we inherit it. |
| Stale `runtime_drafts` rows after many edits/sends pile up.                                                                | Standard retention (`runtime_retention_policies` from migration 0012) covers `runtime_drafts` once we register the table — one extra row in `RetentionScope`, no new mechanism.                                                                                                                                                                                                                                         |
| Performance regression from connector-auth pre-check on every send.                                                        | The capability registry is already in-memory per-process and called on every run start; one extra lookup per `POST /send` is sub-µs. Negligible.                                                                                                                                                                                                                                                                        |

### 1.6 Unit testing requirements

Tests must follow [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md):

- `tests/unit/runtime_worker/handlers/test_run_handler_drafts_backend.py`: run handler constructs a `DraftBackend` with bound tenant identity; missing draft store falls back gracefully (no DraftBackend, just default StateBackend) for legacy/unconfigured deployments.
- `tests/unit/agent_runtime/api/test_draft_service_send_auth_gate.py`: send to authenticated connector → succeeds; send to non-authenticated MCP server → `409` with `mcp_server_id`; send to unknown capability → `400 invalid_target_connector`; send while another send is pending → `409 optimistic_conflict`.
- `tests/unit/runtime_worker/handlers/test_draft_send_handler.py`: queue command → run lifecycle (queued → running → waiting_for_approval → completed); approve → tool dispatch + status=sent + audit; reject → status=draft + audit; tool failure → status=send_failed + audit.
- `tests/unit/agent_runtime/delegation/subagents/test_subagent_drafts_permission.py`: subagent without `/drafts/` grant cannot write; subagent with grant can.
- `tests/unit/agent_runtime/api/test_draft_service_resolution.py`: post-resolution `_finalize_send` is idempotent on duplicate worker delivery (cache-of-one + INSERT … ON CONFLICT DO NOTHING semantics).

Plus three integration tests in `tests/integration/`:

- `test_draft_flow.py` — full agent run that writes `/drafts/<uuid>.md`, asserts `DRAFT_UPDATED` on SSE, then `GET /v1/agent/conversations/{cid}/drafts` returns the latest.
- `test_draft_send.py` — POST send → approve → tool dispatch (against a stub connector) → status=sent → audit chain has all four entries.
- `test_draft_send_unauth.py` — pre-check 409 happy path → user authenticates → retry succeeds.

---

## 2 · Spec

### 2.1 Architecture

```
                                                        ┌─ EXISTING ─────────────────┐
                                                        │                            │
                                                        │  agent_runs                │
                                                        │  runtime_events            │
                                                        │  runtime_approval_requests │
                                                        │  runtime_outbox_events     │
                                                        │  runtime_audit_log         │
                                                        │  runtime_drafts (PR 1.3)    │
                                                        └────────────┬───────────────┘
                                                                     │
   ┌── POST /v1/agent/drafts/{id}/send ──────────────────────────────┴───────┐
   │ 1. DraftService.send                                                    │
   │    a. expect_status(draft, expected_version)                            │
   │    b. CapabilityAuthGate.check(target_connector, runtime_context)       │
   │       → 409 connector_auth_required {mcp_server_id?}  if not authed     │
   │       → 400 invalid_target_connector                  if not registered │
   │    c. INSERT runtime_drafts v+1 status=send_pending_approval            │
   │    d. INSERT agent_runs (kind=draft_send in runtime_context_json)        │
   │    e. INSERT runtime_outbox_events (RuntimeRunCommand{kind=draft_send}) │
   │    f. write_audit_log("draft.send.proposed")                            │
   │    g. return DraftSendResponse{draft, run_id, approval_id=null}         │
   │       FE opens /v1/agent/runs/{run_id}/stream to wait for the           │
   │       approval card                                                     │
   └─────────────────────────────────────────────────────────────────────────┘
                                  │ outbox claim
                                  ▼
   ┌── runtime_worker/handlers/draft_send.py ────────────────────────────────┐
   │ 1. Load latest draft (RLS-enforced; expect status=send_pending_approval)│
   │ 2. CapabilityAuthGate.check(...) — re-check (state may have changed)    │
   │ 3. Build a DraftSendToolInvocation(target, payload from draft body)     │
   │ 4. Hand off to ApprovalMiddleware which:                                │
   │    a. INSERTs runtime_approval_requests                                 │
   │    b. emits APPROVAL_REQUESTED on this run's SSE stream                 │
   │    c. transitions run.status=waiting_for_approval                       │
   │ 5. Suspends until the existing /v1/agent/approvals/{id}/decision lands  │
   │    a RuntimeApprovalResolvedCommand                                     │
   │ 6. On resolution:                                                       │
   │    - approve → dispatch tool via capability registry (existing path)    │
   │              → on success: INSERT draft v+2 status=sent +                │
   │                            audit draft.send.completed +                  │
   │                            emit DRAFT_UPDATED + run_completed           │
   │              → on failure: INSERT draft v+2 status=send_failed +         │
   │                            audit draft.send.failed +                     │
   │                            emit DRAFT_UPDATED + run_failed              │
   │    - reject  → INSERT draft v+2 status=draft +                          │
   │                audit draft.send.rejected +                               │
   │                emit DRAFT_UPDATED + run_completed                       │
   │ 7. On cancel (existing cancel path) →                                   │
   │    INSERT draft v+2 status=draft +                                      │
   │    audit draft.send.cancelled +                                          │
   │    emit DRAFT_UPDATED + run_cancelled (existing)                        │
   └─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                       | Module                                                                                                                                                                              | Owns                                                                                                                                                                                      |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_adapters/factory.py` (modify)                      | `RuntimePorts` / `AsyncRuntimePorts` extended with `draft_store: DraftStorePort`                                                                                                    | Single source of the draft store across API + worker.                                                                                                                                     |
| `runtime_worker/handlers/run.py` (modify)                   | run handler constructs `DraftBackend` per claimed run from `ports.draft_store` + `event_producer` + `runtime_context`                                                               | One backend per run; bound tenant identity.                                                                                                                                               |
| `runtime_worker/handlers/draft_send.py` (new)               | `DraftSendCommandHandler`                                                                                                                                                           | Translates a queued `RuntimeRunCommand{kind:"draft_send"}` into a tool-call + approval-gate flow against the existing capability registry. Handles approve/reject/cancel/tool-fail paths. |
| `runtime_worker/loop.py` / dispatcher (modify)              | Routes `RuntimeRunCommand.kind` to the right handler                                                                                                                                | Dispatch by `kind` field (currently single `"agent"` kind; add `"draft_send"`).                                                                                                           |
| `agent_runtime/capabilities/permissions/auth_gate.py` (new) | `CapabilityAuthGate.check(target_connector, runtime_context) -> AuthGateOutcome`                                                                                                    | Single source of truth for "is this connector reachable for this user." Used by both API pre-check and worker re-check.                                                                   |
| `agent_runtime/api/draft_service.py` (modify)               | `DraftService.send` calls `CapabilityAuthGate` before any DB write; constructs the synthetic send-run + outbox command instead of the placeholder `approval_id`                     | Atomic: one tx for run + draft + outbox.                                                                                                                                                  |
| `agent_runtime/delegation/subagents/contracts.py` (modify)  | `SubagentDefinition.fs_permissions: tuple[FilesystemPermission, ...] = ()`                                                                                                          | Permission rule shape; default empty (denies `/drafts/`).                                                                                                                                 |
| `agent_runtime/execution/factory.py` (modify)               | Builds `FilesystemPermission` list for the deepagents builder from `SubagentDefinition.fs_permissions` for subagents and a constant `_SUPERVISOR_FS_PERMISSIONS` for the main agent | Translates spec into deepagents middleware config.                                                                                                                                        |
| `agent_runtime/persistence/records/retention.py` (modify)   | Add `RetentionScope.RUNTIME_DRAFTS` enum + `runtime_drafts` to retention sweep registry                                                                                             | Folds drafts into the existing retention machinery.                                                                                                                                       |
| `runtime_api/schemas/runs.py` (no change)                   | `RuntimeContext.kind` is already free-form text inside `runtime_context_json`; we set `kind="draft_send"` to discriminate                                                           | No new column.                                                                                                                                                                            |

### 2.3 Pydantic contracts

```python
class CapabilityAuthOutcome(StrEnum):
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN_CAPABILITY = "unknown_capability"
    WORKSPACE_DISABLED = "workspace_disabled"


class CapabilityAuthCheck(RuntimeContract):
    outcome: CapabilityAuthOutcome
    mcp_server_id: str | None = None
    safe_message: str | None = None


class DraftSendCommandPayload(RuntimeContract):
    """Body of a ``RuntimeRunCommand{kind:"draft_send"}`` enqueued by the API.

    All values are server-derived from the persisted draft + the user's
    capability registry — never copied from the request body verbatim.
    """

    draft_id: str = Field(min_length=32, max_length=36)
    draft_version: PositiveInt
    target_connector: str = Field(min_length=1, max_length=64)
    target_metadata: JsonObject = Field(default_factory=dict)
    requested_by_user_id: str
    requested_at: datetime


# Extended runtime command kind discriminator (already a free-form string)
class RuntimeRunCommandKind(StrEnum):
    AGENT = "agent"        # existing
    DRAFT_SEND = "draft_send"  # new (PR 1.3.5)


class FilesystemPermission(RuntimeContract):
    """Mirror of deepagents' FilesystemPermission for our spec contract."""

    path_prefix: str       # e.g. "/drafts/"
    actions: frozenset[Literal["read", "write", "delete"]]
```

`DraftSendResponse` (already in PR 1.3) **changes meaning**: `approval_id` becomes `None` initially because the approval row is created by the worker, not the API. The FE reads `run_id` and opens `/v1/agent/runs/{run_id}/stream?after_sequence=0` — the existing flow — to receive the approval card via `APPROVAL_REQUESTED`. No FE change is required (the FE already opens streams keyed off run_id).

### 2.4 Storage — explicitly **no new tables**

Every fact this PR needs is in an existing table:

| Fact                                                                                       | Existing home                                                                                | Notes                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The send "run" (a one-tool synthetic run scoped to one connector dispatch)                 | `agent_runs`                                                                                 | `runtime_context_json -> 'kind'` discriminator; existing RLS + indexes apply. No model is invoked, but the row still tracks status, audit linkage, cancel handle, and the SSE stream URL.                                                                                                                                   |
| The approval card                                                                          | `runtime_approval_requests` (existing schema, `approval_kind="action"`)                      | We deliberately reuse `action`, not a new `draft_send` enum. The FE renders any `action`-kind approval through the same `ApprovalTool` component. The `metadata` JSONB carries `target_connector`, `draft_id`, `draft_version`, `summary`, `body_preview`.                                                                  |
| State transitions on the draft (proposed → approved/rejected → completed/failed/cancelled) | `runtime_drafts` (PR 1.3)                                                                    | One new row per transition, append-only. Latest version wins. Status enum already covers all five terminal states.                                                                                                                                                                                                          |
| The outbox command that wakes the worker                                                   | `runtime_outbox_events`                                                                      | Existing kind discriminator field; we add `"draft_send"` as a value. No DDL.                                                                                                                                                                                                                                                |
| Audit trail                                                                                | `runtime_audit_log`                                                                          | Five action strings: `draft.send.proposed`, `draft.send.approved`, `draft.send.rejected`, `draft.send.completed`, `draft.send.failed`, `draft.send.cancelled`. Existing HMAC chain.                                                                                                                                         |
| Connector-auth state                                                                       | `mcp_servers` (backend) + `mcp_auth_connections` (backend) + per-process capability registry | Read via existing `/internal/v1/mcp/servers?status=...` endpoint when the target is an MCP server; via `tool_registry.list_available_tools(runtime_context)` when the target is a built-in capability. **Cached on the worker process for the lifetime of the run** so a slow MCP backend doesn't re-amplify per-tool-call. |

#### Why no new column or index either

- We don't add `agent_runs.kind`. Discriminating sends from agent runs is done by `runtime_context_json -> 'kind' = 'draft_send'`. That column is JSONB and indexable via expression index _if_ we ever need it. Today the only reader is the worker dispatcher which has the kind in-hand.
- We don't add `runtime_approval_requests.draft_id`. The metadata JSONB already carries it.
- The `runtime_drafts (org_id, conversation_id, draft_id, version DESC)` composite index from PR 1.3 covers every read path the new send flow introduces — including the worker's "load latest before dispatch."
- Retention: register `runtime_drafts` with the existing retention sweep (one constant + one cascade; no new sweep code).

### 2.5 The send flow, in detail

#### 2.5.1 API path (`DraftService.send`)

```python
async def send(self, *, org_id, user_id, draft_id, request: DraftSendRequest) -> DraftSendResponse:
    latest = await self._expect(org_id=org_id, draft_id=draft_id, expected_version=request.expected_version)
    if latest.status in {DraftStatus.SENT, DraftStatus.DISCARDED}:
        raise self._immutable_status_error(latest.status)

    # 1. Pre-check: is this connector reachable for this user?
    auth = self._auth_gate.check(
        target_connector=request.target_connector,
        runtime_context=runtime_context_for(org_id, user_id, latest.conversation_id),
    )
    if auth.outcome is not CapabilityAuthOutcome.AUTHENTICATED:
        raise self._auth_error(auth)  # 409 connector_auth_required / 400 invalid_target_connector

    # 2. One transaction: run + draft v+1 + outbox cmd + audit
    async with self._persistence.transaction():
        run = await self._persistence.create_synthetic_run(
            org_id=org_id, user_id=user_id, conversation_id=latest.conversation_id,
            kind=RuntimeRunCommandKind.DRAFT_SEND,
        )
        next_record = self._next_version(
            previous=latest, run_id=run.run_id, user_id=user_id,
            content_text=latest.content_text,
            target_connector=request.target_connector,
            target_metadata=dict(request.target_metadata or {}),
            status=DraftStatus.SEND_PENDING_APPROVAL,
        )
        persisted = await self._store.insert_version(next_record)

        await self._queue.enqueue_run(
            RuntimeRunCommand(
                run_id=run.run_id, kind=RuntimeRunCommandKind.DRAFT_SEND,
                payload=DraftSendCommandPayload(
                    draft_id=draft_id, draft_version=persisted.version,
                    target_connector=request.target_connector,
                    target_metadata=dict(request.target_metadata or {}),
                    requested_by_user_id=user_id,
                    requested_at=datetime.now(timezone.utc),
                ).model_dump(),
            )
        )
        await self._persistence.write_audit_log(
            event_type="draft.send.proposed",
            record={"draft_id": draft_id, "version": persisted.version, "run_id": run.run_id, ...},
        )
    return DraftSendResponse(draft=_to_draft(persisted), run_id=run.run_id, approval_id=None)
```

The transaction boundary is critical: either all four rows commit together or none do. No half-states, no orphan outbox commands.

#### 2.5.2 Worker path (`runtime_worker/handlers/draft_send.py`)

```python
class DraftSendCommandHandler:
    async def handle(self, command: RuntimeRunCommand) -> None:
        payload = DraftSendCommandPayload.model_validate(command.payload)

        # 1. Load latest under RLS — confirm send_pending_approval (idempotent reclaim).
        run = await self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
        if run.status in TERMINAL_STATUSES:
            return  # idempotent: another worker/replay handled it
        latest = await self.draft_store.latest(org_id=command.org_id, draft_id=payload.draft_id)
        if latest is None or latest.status is not DraftStatus.SEND_PENDING_APPROVAL:
            await self._fail(command, run, reason="draft_state_changed")
            return

        # 2. Re-check capability auth (state may have changed since API).
        auth = self.auth_gate.check(target_connector=payload.target_connector, runtime_context=run.runtime_context)
        if auth.outcome is not CapabilityAuthOutcome.AUTHENTICATED:
            await self._fail(command, run, reason="connector_auth_required",
                             extra={"mcp_server_id": auth.mcp_server_id})
            return

        # 3. Mark run started + emit RUN_STARTED.
        await self.event_producer.append_lifecycle(run, RuntimeApiEventType.RUN_STARTED)

        # 4. Build the synthetic ToolInvocation and hand to the approval middleware.
        invocation = ToolInvocation(
            org_id=command.org_id, run_id=command.run_id,
            tool_name=payload.target_connector,
            args={**payload.target_metadata, "body": latest.content_text},
            side_effect_class=ToolSideEffectClass.EXTERNAL_SIDE_EFFECT,
            requires_approval=True,
            approval_metadata={
                "draft_id": payload.draft_id, "draft_version": latest.version,
                "summary": _approval_summary(latest, payload),
                "body_preview": latest.content_text[:400],
            },
        )
        try:
            tool_result = await self.approval_gated_invoke(invocation, run)
        except ApprovalRejected:
            await self._record_terminal(latest, DraftStatus.DRAFT, "draft.send.rejected", run, ok=True)
            return
        except RunCancelled:
            await self._record_terminal(latest, DraftStatus.DRAFT, "draft.send.cancelled", run, ok=True)
            return
        except ToolDispatchError as exc:
            await self._record_terminal(latest, DraftStatus.SEND_FAILED, "draft.send.failed", run,
                                        ok=False, error=exc.safe_message)
            return

        await self._record_terminal(latest, DraftStatus.SENT, "draft.send.completed", run,
                                    ok=True, tool_result=tool_result)
```

`approval_gated_invoke` is the existing primitive used by every other tool that requires approval — we don't fork it. It writes `runtime_approval_requests`, emits `APPROVAL_REQUESTED`, parks the task on a future, and resumes when an `RuntimeApprovalResolvedCommand` lands via the existing `/v1/agent/approvals/{id}/decision` endpoint.

`_record_terminal` is one method that:

1. INSERTs `runtime_drafts` v+2 with the new status (and `target_connector`/`metadata` carried forward).
2. Emits `DRAFT_UPDATED` via the existing event producer.
3. Writes one append-only audit chain entry.
4. Marks the run terminal via the existing `update_run_status`.

All in **one** transaction so the FE never sees an intermediate state where the draft is `sent` but the run is still `running`, or vice versa.

### 2.6 Connector-auth pre-check (`CapabilityAuthGate`)

A small, dependency-free class that wraps the existing capability registry:

```python
class CapabilityAuthGate:
    def __init__(self, *, tool_registry: ToolRegistry, mcp_registry: McpRegistry) -> None:
        self._tools = tool_registry
        self._mcp = mcp_registry

    def check(self, *, target_connector: str, runtime_context: AgentRuntimeContext) -> CapabilityAuthCheck:
        # 1. Built-in tool present + workspace-enabled?
        for tool in self._tools.list_available_tools(runtime_context):
            if tool.name == target_connector:
                return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)

        # 2. MCP server known to the user?
        for server in self._mcp.list_available_servers(runtime_context):
            if server.exposes_tool(target_connector):
                if server.auth_state == "authenticated":
                    return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)
                return CapabilityAuthCheck(
                    outcome=CapabilityAuthOutcome.NOT_AUTHENTICATED,
                    mcp_server_id=server.server_id,
                    safe_message="Connector not authenticated for this user.",
                )

        return CapabilityAuthCheck(
            outcome=CapabilityAuthOutcome.UNKNOWN_CAPABILITY,
            safe_message="Unknown connector for this workspace.",
        )
```

Both registries are already in-memory caches refreshed on a TTL by the existing run handler — the gate is sub-µs in the hot path. The API and the worker share one instance per process; no I/O, no rate-limit risk.

### 2.7 Subagent `/drafts/` permission

`SubagentDefinition` (existing) gains:

```python
fs_permissions: tuple[FilesystemPermission, ...] = ()  # default: deny /drafts/
```

In `agent_runtime/execution/factory.py`, we already pass `subagents=subagents` to the deepagents builder. We add:

```python
_SUPERVISOR_FS_PERMISSIONS = (
    FilesystemPermission(path_prefix="/drafts/", actions=frozenset({"read", "write"})),
    # /memories/, /skills/, /subagents/ already covered by their own backends.
)

def _filesystem_permissions_for(subagent: SubagentDefinition) -> tuple[FilesystemPermission, ...]:
    return tuple(subagent.fs_permissions or ())
```

The deepagents `FilesystemMiddleware` accepts a per-agent `permissions=...` arg; we plumb the list. Subagents whose definition doesn't grant `/drafts/` get `EditResult(error="permission_denied")` straight from the middleware before reaching `DraftBackend`. The supervisor's permissions always include `/drafts/` so the headline use case (Sarah asks Atlas to draft an announcement) just works.

### 2.8 Edge cases (drop none)

| Edge case                                                                             | Behavior                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two concurrent POST /send for the same draft                                          | The second one's `expect_status(expected_version=N)` finds latest=N+1 → `409 optimistic_conflict`.                                                                                                                                                                                  |
| User revokes connector auth between API pre-check and worker dispatch                 | Worker re-checks; `_fail(reason="connector_auth_required")` → draft status=send_failed + audit; FE sees DRAFT_UPDATED + run_failed and renders the McpDiscoveryCard.                                                                                                                |
| Connector tool returns a partial-success (e.g., Slack 207 multi-status)               | Goes through the existing tool-result projection; we treat any non-error tool result as success → status=sent. The tool-result event carries the connector's response so downstream observability can flag if needed.                                                               |
| Cancel after approval but before tool dispatch                                        | Existing cancel path triggers `RunCancelled` inside `approval_gated_invoke`. `_record_terminal(DRAFT, "draft.send.cancelled")`. No connector side effect.                                                                                                                           |
| Cancel after tool dispatch (Slack message already posted)                             | Tool result lands first (existing tool-call ledger settles in-flight tools before run terminal). `_record_terminal(SENT, "draft.send.completed")`. Cancel changes nothing — the side effect already happened and we record it truthfully.                                           |
| Send to a draft that's already `sent` or `discarded`                                  | `_immutable_status_error` (existing PR 1.3 logic).                                                                                                                                                                                                                                  |
| Send arrives, worker hasn't picked up yet, user opens stream                          | Existing replay path returns the persisted events so far (just `RUN_QUEUED`). FE shows "Queued..." — no special UI.                                                                                                                                                                 |
| Worker crashes mid-handler after the run row is `running` but before any approval row | Lock expires → outbox redelivers. `handle` checks `run.status in TERMINAL_STATUSES` (no) and `latest.status is SEND_PENDING_APPROVAL` (yes) → re-runs. Idempotent because no DB rows have been mutated since claim.                                                                 |
| Worker crashes between `_fail` and outbox `mark_complete`                             | Outbox redelivers. `handle` sees `latest.status` is now `send_failed` (already recorded) → returns early. Audit chain retains exactly one `draft.send.failed` (UNIQUE constraint via the chain seq).                                                                                |
| Subagent attempts `/drafts/` write without permission                                 | Deepagents `FilesystemMiddleware` rejects at the tool-call level, returns `EditResult(error="permission_denied")` to the subagent's harness. Subagent's tool message records the failure; supervisor sees a `tool_result` event with the error. No `runtime_drafts` row is written. |
| Send during a memory-compression event                                                | Compression runs on its own queue; doesn't block the send run. Dispatch unaffected.                                                                                                                                                                                                 |
| Retention sweep deletes a draft mid-send                                              | Sweep skips `send_pending_approval` rows (existing pattern: retention checks are status-aware on `runtime_runs` already; we extend the same skip to `runtime_drafts.status`).                                                                                                       |

### 2.9 Security

- **Tenant identity at construction**: the `DraftBackend` constructor is called by the run handler with values from the validated `AgentRuntimeContext`. The model can never inject org_id via path strings.
- **Path validation**: `DraftPath.parse_draft_id` returns `None` for non-UUID-hex paths; the backend rejects with `invalid_path`. Already covered by PR 1.3 + tested.
- **Connector tool name as untrusted**: the worker resolves the name through the registry, never executes a string verbatim. Unknown names return `unknown_capability`.
- **Audit chain integrity**: every state transition uses the existing `AuditChainSigner` (HMAC + prev_hash). The send flow contributes 3–4 entries per send, all with the existing `seq` / `signature` invariants.
- **RLS on read paths**: the worker uses `_tenant_connection(org_id=...)` for every store call; cross-org reads return zero rows.
- **No new connector executes from the API process**: tool dispatch lives only in the worker's existing capability registry path. The API never calls a connector synchronously; that decoupling is what makes the request-latency invariant in AC-9 hold.
- **PII handling**: the approval card's `body_preview` is truncated to 400 chars and goes through the existing `ObservabilityRedactor` before persistence.

### 2.10 Observability

- **Metrics** (existing observability stack):
  - Counter `runtime.draft_send.outcome_total{outcome=approved|rejected|completed|failed|cancelled,connector_class}` — counts per-terminal-state.
  - Histogram `runtime.draft_send.api_to_approval_card_seconds` — wall time from API send response to APPROVAL_REQUESTED on stream.
  - Histogram `runtime.draft_send.api_send_endpoint_seconds` — server timing for the API hot path (target ≤50ms p95 in_memory).
  - Counter `runtime.draft_send.auth_gate_outcome_total{outcome}` — to track how often the pre-check rejects.
- **Logs**: structured fields `draft_id`, `draft_version`, `target_connector`, `mcp_server_id` (when applicable), `outcome`, `safe_error_message`. No PII / model output.
- **`pg_stat_statements`**: the new SQL is one new query (synthetic-run insert) + reuse of existing draft inserts + outbox inserts. Visible under the existing extension.
- **Audit chain**: entries land in `runtime_audit_log` and ship to SIEM via the existing exporter (`/internal/v1/audit/export`). No new exporter.

### 2.11 Performance

| Path                                         | Steady-state cost                                                                                               | Worst case                                             | Mitigation                                                                                          |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| API `POST /send`                             | 1 SELECT (`expect_status`) + 4 INSERTs (run, draft, outbox, audit) inside one tx + 1 in-memory auth-gate lookup | ~25ms in_memory / ~60ms postgres p95                   | All inserts hit existing indexes; no FK round-trip beyond what already exists.                      |
| Worker queue pickup                          | Polling cadence                                                                                                 | `RUNTIME_WORKER_POLL_INTERVAL_SECONDS` (default 250ms) | Existing knob; not draft-specific.                                                                  |
| Worker dispatch hot path                     | 1 SELECT (run) + 1 SELECT (latest draft) + 1 in-memory auth-gate lookup + N tool dispatches (1 today)           | Bounded by connector latency                           | Tool dispatch is async; worker handles other claims concurrently up to `RUNTIME_MAX_PARALLEL_RUNS`. |
| Approval middleware                          | 1 INSERT (`runtime_approval_requests`) + 1 emit                                                                 | sub-millisecond                                        | Existing path.                                                                                      |
| Resolution finalization (`_record_terminal`) | 1 INSERT (draft v+2) + 1 emit + 1 audit + 1 UPDATE (run status) inside one tx                                   | ~15ms                                                  | Single tx.                                                                                          |
| Cancel-during-tool race                      | Existing tool-call ledger settles in-flight tools before run terminal                                           | Bounded by tool timeout                                | No new code.                                                                                        |

End-to-end queue→approval-card-on-stream is dominated by the worker poll interval. With the default 250ms poll and SSE flush, we expect <500ms p50 and <1s p95.

### 2.12 Anti-patterns explicitly avoided

- ❌ **A new approval kind for draft sends.** We use the existing `action` kind. The FE renders any `action`-kind approval through the existing `ApprovalTool` component; bolting a new kind would force a parallel UI component for no semantic gain.
- ❌ **A new `runtime_draft_sends` table.** Every fact lives in an existing append-only table. A new table would split the audit story across two storage rooms and double the retention surface.
- ❌ **Synchronous connector dispatch inside the API request.** Couples request latency to connector latency, breaks AC-9, and makes graceful degradation harder when a connector is slow.
- ❌ **A `draft_id` foreign key on `runtime_approval_requests`.** Approvals are polymorphic by design. The metadata JSONB carries the link; a structured FK would reduce cross-domain reuse.
- ❌ **Storing the connector tool name on the draft.** Couples drafts to specific tool identifiers and breaks if a connector is renamed. The send command carries the target; the draft carries an opaque `target_connector` string that the registry resolves at dispatch time.
- ❌ **A "fake" run with `model_provider=null`.** All runs go through the same lifecycle; the synthetic send-run has model_provider/model_name set to `"system"`/`"draft_send"` so existing observability and budget tooling treat it uniformly. The worker handler short-circuits the model-invocation step.
- ❌ **A bespoke cancel handler for send runs.** The existing cancel path (writing to `runtime_outbox_events` + `runtime_run.status=cancelling`) covers our case. The handler observes `RunCancelled` from the same future the rest of the runtime does.
- ❌ **Returning a placeholder `approval_id` from the API and pretending it's real.** PR 1.3 had to do this because the worker didn't exist; PR 1.3.5 returns `null` and lets the FE pick up the real id from the SSE stream — same pattern as every other run-driven approval today.
- ❌ **Looping the FE's POST /send to retry on connector-auth failure.** The 409 carries `mcp_server_id` and the FE renders `McpDiscoveryCard`; on connector auth, the user retries the send manually. No silent retries that could double-post.

### 2.13 Tests

Unit (mandatory):

- `test_run_handler_drafts_backend.py` — handler builds DraftBackend with bound identity; missing draft_store → no DraftBackend wired; emit_event closure carries the per-run `RuntimeEventProducer`.
- `test_capability_auth_gate.py` — built-in connector authenticated; MCP server not authenticated → mcp_server_id surfaced; unknown capability → unknown_capability; workspace-disabled → workspace_disabled.
- `test_draft_service_send_auth_gate.py` — 409 connector_auth_required; 400 invalid_target_connector; 409 optimistic_conflict on stale expected_version; immutable_status on already-sent.
- `test_draft_send_handler.py` — happy path (approve → tool dispatch → status=sent + audit completed); reject (status=draft + audit rejected); cancel before tool (status=draft + audit cancelled); tool dispatch failure (status=send_failed + audit failed); idempotent re-delivery; auth-state-changed-since-api guard.
- `test_subagent_drafts_permission.py` — supervisor grants /drafts/ writes; subagent without grant denied; subagent with grant succeeds.
- `test_draft_send_runtime_context_kind.py` — synthetic run carries `runtime_context.kind="draft_send"`; observability + budget tooling can query it.

Integration (in `tests/integration/`):

- `test_draft_flow.py` — end-to-end: agent run writes /drafts/<uuid>.md → SSE emits DRAFT_UPDATED v=1 → GET drafts returns v=1 → agent's edit_file → DRAFT_UPDATED v=2.
- `test_draft_send.py` — POST /send → API returns run_id + null approval_id → SSE emits APPROVAL_REQUESTED → POST /v1/agent/approvals/{id}/decision approve → SSE emits tool_call_started/tool_result/DRAFT_UPDATED v=2 status=sent + run_completed → audit chain has 4 entries with linked seqs.
- `test_draft_send_unauth.py` — POST /send to a non-authed MCP target → 409 with mcp_server_id → no draft mutation → mock OAuth completes → POST /send retry succeeds.
- `test_draft_send_cancel.py` — POST /send → POST /v1/agent/runs/{run_id}/cancel before approval → DRAFT_UPDATED v=2 status=draft + audit cancelled.

---

## 3 · Architecture decisions worth calling out

### 3.1 Why a "send run" instead of an approval-without-a-run

`runtime_approval_requests` requires a `run_id`. We could relax that to make approvals first-class conversation objects, but every existing approval primitive (cancel, replay, audit linkage) keys off `run_id`. Inventing approvals-without-runs would fork half the runtime. A synthetic single-tool run is one row; the worker handler short-circuits the model invocation, so there's no token cost.

### 3.2 Why a single transaction for API send

Without it, four failure modes are observable: orphan run (no draft), orphan draft (no outbox cmd → permanent send_pending_approval), orphan outbox (worker hits a missing draft), missing audit. With the transaction, the only observable failure is "the send didn't happen" — same shape as a 5xx response. The cost is one explicit `BEGIN`/`COMMIT` block; sub-ms overhead.

### 3.3 Why the worker re-checks connector auth

Time can pass between API send (state captured) and worker dispatch (state-of-the-world). If the user revoked the connector in between, dispatching anyway would be a quietly wrong behavior. The re-check is cheap (in-memory) and fail-closed.

### 3.4 Why no new table, no new column, no new index

The pattern from CLAUDE.md is "share only stable contracts and truly cross-cutting primitives." A `runtime_draft_sends` table would be neither — it would just split the existing draft lifecycle across two storage rooms and double the retention/compliance surface. The composite index on `runtime_drafts (org_id, conversation_id, draft_id, version DESC)` from PR 1.3 covers every read this PR introduces. The `runtime_outbox_events` schema already has a free-form `kind` field. JSONB fields on `agent_runs` and `runtime_approval_requests` already carry the per-kind metadata.

### 3.5 Why sub-1s queue→approval is acceptable

The Atlas design's success criterion for approvals is "the user sees the card in the same gestalt as the message that requested it" — not strict realtime. The existing tool-approval flow has the same characteristic: a `tool_call` lands, then `APPROVAL_REQUESTED` lands ~250ms later. We match that property.

---

## 4 · Verification plan

After this PR lands, on `make dev`:

1. Open a chat. Prompt "Draft the FY26 Q1 launch announcement using the approved positioning."
2. Watch `/runs/{run_id}/stream` in devtools. Expect, in order: `tool_call(write_file)` → `tool_result` → `DRAFT_UPDATED v=1`.
3. Open Workspace pane → Drafts. Verify v=1 visible.
4. Click "Send to Slack", channel `#announcements`. Network: `POST /send` returns `{run_id, approval_id: null}`. The chat thread's stream emits `APPROVAL_REQUESTED` within 1s; the FE renders the existing `ApprovalTool` card.
5. Click Approve. Stream emits `tool_call_started(slack_post_message)` → `tool_result` → `DRAFT_UPDATED v=2 status=sent` → `run_completed`.
6. `psql`: `SELECT action FROM runtime_audit_log WHERE action LIKE 'draft.%' ORDER BY id DESC LIMIT 5;` — expect `proposed`, `approved`, `completed` linked by `prev_hash`.
7. Cross-tenant: change `x-enterprise-org-id` and POST send → 404.
8. Auth-gate negative: revoke the Slack connector. POST /send → 409 with `mcp_server_id`. FE renders McpDiscoveryCard. Re-authenticate. Retry → succeeds.
9. Cancel race: send → cancel before approving → DRAFT_UPDATED v=2 status=draft + audit cancelled.
10. Subagent permission: spawn a subagent without /drafts/ grant; it tries `write_file("/drafts/<uuid>.md", ...)` → tool result `permission_denied`; no `runtime_drafts` row.
11. Bench: run 100 sends in a loop, measure API p95 ≤ 50ms (in_memory) and queue→approval-card p95 ≤ 1s.

If all eleven pass, AC-1 through AC-10 are satisfied.

## 5 · Sequencing summary

```
PR 1.3.5
├── ports/factory plumbing (RuntimeAdapterFactory.draft_store)
├── runtime_worker run-handler wires DraftBackend per run        ← AC-1, AC-2
├── CapabilityAuthGate + DraftService.send pre-check             ← AC-3
├── DraftService.send transaction + synthetic run + outbox       ← AC-4
├── runtime_worker/handlers/draft_send.py                        ← AC-5, AC-7
├── SubagentDefinition.fs_permissions + factory plumbing         ← AC-6
├── runtime_drafts in retention sweep registry                    (operational)
├── 6 unit tests + 4 integration tests                           ← AC-8, AC-9, AC-10
└── Observability: 4 metrics + structured fields                  (operational)
```

Estimated PR size: **~1,200 LOC** new code + **~800 LOC** tests. Two of the diffs are 5-line changes (factory port wiring, run-handler), three are mid-size (auth gate, draft_send handler, send transaction rewrite). Single PR, single merge.
