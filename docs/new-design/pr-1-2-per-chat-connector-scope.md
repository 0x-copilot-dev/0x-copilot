# PR 1.2 — Per-chat Connector Scope Persistence

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 1, PR 1.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (write path) · backend-facade (proxy) · frontend (toggle UI)
> **Size:** S (one column + one PATCH endpoint + tiny FE hook). Targeted at one PR.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md)
> **Sibling doc:** PR 1.1 — Citations live registry (separate file, written in parallel)

---

## 1 · PRD

### Problem

Today, "which connectors can the agent reach in this chat?" is decided **per run** from the `x-enterprise-connector-scopes` header. The scope set is recomputed on every prompt and there is no persistent "I paused Slack in this thread" state. The Atlas design doc requires three layers — workspace-installed, user-connected, **active for this chat** — with the third layer surviving page reloads, run restarts, and switching between chats.

Without persistence:

- Every new prompt the user sends in the same chat re-includes connectors they explicitly paused.
- "Summarize Q1 launch" leaks personal Calendar even after the user paused it.
- The composer + topbar `ConnectorPopover` toggles do nothing across runs (FE state is lost).

### Goals

1. A user can pause/resume any individual connector at the **conversation** scope.
2. The selection survives reloads, run completion, and other chats touching the same connector.
3. The agent harness honours the selection at run-start (snapshot semantics — never mid-run).
4. Every toggle is auditable (who, what, when).
5. No new dependency, no new event family, no breakage of the existing run-creation header path.

### Non-goals

- Per-tool scope toggles (defer to MCP catalog redesign — PR 4.4 / future PR).
- Workspace-default connector set (PR 1.6 owns this).
- Mid-run connector revocation (deliberate — see §4 "Why snapshot, not subscribe").
- Cross-conversation "global pause" (use Settings → Connectors disable instead).

### Success criteria

- ✅ `PATCH /v1/agent/conversations/{id}/connectors` returns the new scope set in <50 ms p99 against the local stack.
- ✅ Creating a run with no `connector_scopes` header on a conversation that has paused Slack results in `slack_*` tools being **invisible** to the model.
- ✅ Reloading the chat replays the toggle state correctly into the popover.
- ✅ One audit row appears in `runtime_audit_log` per toggle.
- ✅ Toggling during a running run does **not** affect that run's tool set; the next run picks up the change.

### User stories

| As…              | I want…                                    | So that…                                                        |
| ---------------- | ------------------------------------------ | --------------------------------------------------------------- |
| Sarah (end user) | to pause Calendar in my "Q1 launch" chat   | personal events don't leak into a launch summary                |
| Sarah            | the pause to stick when I reload           | I don't have to re-pause every time                             |
| Marcus (admin)   | every connector toggle to be audit-logged  | I can answer "who turned off Slack on the legal review thread?" |
| Atlas team       | the toggle to never crash a mid-flight run | scope changes never destabilize streaming                       |

---

## 2 · Spec

### 2.1 Wire — request shape

`PATCH /v1/agent/conversations/{conversation_id}/connectors`

```jsonc
{
  // RFC 7396 merge-patch semantics on a connector_id → scope-list map.
  // Send only the connectors you are changing.
  // - Array of strings = active for this chat with these scopes.
  // - null            = pause this connector (still installed/connected, just inert here).
  // - Omit a key      = no change.
  "scopes": {
    "slack": null, // pause Slack
    "salesforce": ["read"], // resume / set to read-only
  },
}
```

Response:

```jsonc
{
  "conversation_id": "conv_01HM…",
  "scopes": {
    "notion": ["read", "write_drafts"],
    "drive": ["read", "comment"],
    "slack": null, // paused
    "confluence": ["read"],
    "salesforce": ["read"],
    // disconnected / workspace-off connectors are never present here
  },
  "updated_at": "2026-05-05T14:21:08.412Z",
}
```

`GET /v1/agent/conversations/{conversation_id}` is extended to include the same `scopes` block (so a single conversation-load round-trip seeds the popover state).

### 2.2 Wire — header behaviour at run-start

The existing `x-enterprise-connector-scopes` header (parsed by [`runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py) `_connector_scopes`) **takes precedence when present**. This is intentional:

- **Source of truth: the conversation row** when the FE just sends the run prompt.
- **Override: the header** for service-to-service callers that already computed scopes (e.g. backend-facade preview links, share-link recipients in PR 6.1).

The fallback merge is the only new logic in `RunService.create_run`:

```python
# Pseudocode in services/ai-backend/src/runtime_api/services/runs.py (existing class)
effective_scopes = (
    request_context.connector_scopes
    if request_context.connector_scopes  # explicit, even if empty {} stays explicit
    else await conversations.get_scope_snapshot(conversation_id)
)
```

The downstream `AgentRuntimeContext.connector_scopes` reaching [`ToolPermissionChecker.has_scopes_for_connector`](../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py#L39) and the MCP loader middleware is unchanged. **No tool/middleware code is modified.**

### 2.3 Persistence

Single migration `services/ai-backend/migrations/0014_conversation_connector_scope.sql`:

```sql
ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS enabled_connectors JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS connectors_updated_at TIMESTAMPTZ;

-- Cheap GIN index — supports "which chats use this connector" admin queries later
-- without a table scan. Keep optional behind a feature flag if storage budget is tight.
CREATE INDEX IF NOT EXISTS idx_agent_conversations_enabled_connectors
    ON agent_conversations USING gin (enabled_connectors jsonb_path_ops);
```

Storage shape inside the column:

```json
{
  "notion": ["read", "write_drafts"],
  "slack": null,
  "salesforce": ["read"]
}
```

Why a dedicated column over `metadata_json`:

- Indexable (`gin (jsonb_path_ops)`) without polluting metadata.
- Distinct mutation path — no risk of a different feature stomping the metadata blob.
- Backwards-compatible default (`{}` = "no override; capability resolver falls back to all connected"); zero migration code beyond the `ALTER`.
- One column, no row-version column added: scope toggles are user-driven and serial per chat — last-write-wins is fine. Audit row carries the diff for forensic reconstruction.

### 2.4 Audit

One row per successful PATCH, written through the existing chain in `runtime_audit_log`:

```jsonc
{
  "action": "conversation.connectors.update",
  "actor_user_id": "<user>",
  "org_id": "<org>",
  "conversation_id": "conv_01HM…",
  "metadata_json_redacted": {
    "before": { "slack": ["read"] },
    "after": { "slack": null },
    "diff_keys": ["slack"],
  },
}
```

Connector IDs and scope strings are non-sensitive and stored unredacted; the column already has `encryption_version` available if a customer ever wants envelope encryption.

### 2.5 Permissions

| Caller                 | Allowed                                                                  |
| ---------------------- | ------------------------------------------------------------------------ |
| Conversation owner     | ✅                                                                       |
| Workspace admin        | ✅ (covers "support paused this for a member after a security incident") |
| Other workspace member | ❌ 403 — no read, no write                                               |

This reuses the existing role check in `runtime_api/rbac.py`; nothing new.

### 2.6 Error semantics

| Condition                                         | Status | Code                     |
| ------------------------------------------------- | ------ | ------------------------ |
| Conversation not found / not in caller's org      | 404    | `conversation_not_found` |
| Caller lacks permission                           | 403    | `forbidden`              |
| Body fails schema (e.g. scope not a list-or-null) | 422    | `invalid_request`        |
| Connector ID not workspace-installed              | 422    | `unknown_connector`      |
| Scope string not declared by the connector card   | 422    | `unknown_scope`          |

Validation reuses the workspace's authoritative connector card registry already loaded by [`agent_runtime/capabilities/tools/cards.py`](../../services/ai-backend/src/agent_runtime/capabilities/tools/cards.py) — DRY, no second source of truth.

### 2.7 Frontend contract (`@0x-copilot/api-types`)

Two additions only:

```ts
// packages/api-types/src/index.ts
export type ConversationConnectorScopes = Record<
  string,
  readonly string[] | null
>;

export interface UpdateConversationConnectorScopesRequest {
  scopes: ConversationConnectorScopes; // RFC 7396 merge-patch semantics
}

export interface ConversationConnectorScopesResponse {
  conversation_id: string;
  scopes: ConversationConnectorScopes;
  updated_at: string;
}
```

`Conversation` already exists; extend with `scopes?: ConversationConnectorScopes` so `GET /conversations/{id}` and `GET /conversations` return the snapshot inline (saves one round-trip on chat open).

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────┐    PATCH /v1/agent/conversations/{id}/connectors
   │   apps/        │ ────────────────┐
   │   frontend     │                 │
   │  ConnectorPop. │ <───────────────┤  (200 with new scope map)
   └────────────────┘                 │
                                      ▼
                          ┌────────────────────┐
                          │ backend-facade     │   thin proxy, headers preserved
                          │ /v1/agent/...      │   (no business logic here)
                          └─────────┬──────────┘
                                    │ /internal/v1/agent/conversations/{id}/connectors
                                    ▼
                          ┌────────────────────┐
                          │ ai-backend         │   ConversationsService
                          │ runtime_api        │   .update_scopes()  ──┐
                          └─────────┬──────────┘                       │
                                    │                                  │
                                    │ writes                           │ writes
                                    ▼                                  ▼
                          ┌────────────────────┐         ┌────────────────────┐
                          │ agent_conversations│         │ runtime_audit_log  │
                          │ .enabled_connectors│         │ (append-only chain)│
                          └────────────────────┘         └────────────────────┘
                                    ▲
                                    │ READ at run-start (only when header absent)
                                    │
                          ┌─────────┴──────────┐
                          │ ai-backend         │
                          │ RunService         │
                          │ .create_run()      │
                          └────────────────────┘
                                    │
                                    ▼
                          ┌────────────────────┐
                          │ agent_runs         │
                          │ .runtime_context   │  ← scope snapshot frozen here
                          │   _json            │
                          └─────────┬──────────┘
                                    │
                                    ▼
                          ┌────────────────────┐
                          │ runtime_worker     │  reads runtime_context → builds
                          │ + capabilities/    │  AgentRuntimeContext → tools filtered
                          │   tools/permissions│  by ToolPermissionChecker (unchanged)
                          └────────────────────┘
```

### 3.2 Why snapshot, not subscribe (the streaming question)

The agent harness is built around `runtime_events` ordered by `sequence_no` per run, and `agent_runs.runtime_context_json` is **frozen at run start**. Tool resolution happens once when the LangGraph deep agent is constructed in the worker — it does not re-query during streaming.

Doing scope-mutation mid-run is the wrong shape for three reasons:

1. **Reproducibility / auditability.** A run's events are a faithful record of what tools the agent could see. If scope changes mid-stream you'd need a new event family (`runtime_capabilities_changed`) and either tear-down/rebuild the agent (loses checkpoints) or cope with tools the model already has token-cached. Either path is a major surface and a worse UX (the model "forgets" how to do something it just saw).
2. **Determinism for replay.** `GET /v1/agent/runs/{id}/events?after_sequence=N` resumes by replaying. If capabilities mutate mid-run, replay needs capability deltas too. Avoidable.
3. **Product fit.** The Atlas spec describes a chat-level pause as a setup step before sending a prompt, not a kill-switch on a flying agent. Cancel exists for kill-switch (`POST /v1/agent/runs/{id}/cancel`).

**Therefore: scope changes affect the next run only.** No new event type. No subscribe path. The FE PATCH response is the single source of state for the popover.

This also keeps the SSE wire and stream-handshake doc unchanged — important for not destabilising PR 1.1's citation work landing in parallel.

### 3.3 DRY — what we reuse vs. what we add

| Concern                              | Reuse                                                                                                                      | Add                                                                 |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Identity / RBAC                      | `RuntimeServiceAuthenticator`, `runtime_api/rbac.py`                                                                       | —                                                                   |
| Connector ID/scope validation        | `capabilities/tools/cards.py` registry                                                                                     | —                                                                   |
| Permission semantics                 | `ToolPermissionChecker` (untouched)                                                                                        | —                                                                   |
| Persistence pool / migrations runner | `agent_runtime/persistence/schema/migrate.py`                                                                              | one ALTER + one GIN index                                           |
| Audit chain                          | `runtime_audit_log` writer                                                                                                 | one new `action` constant                                           |
| PATCH partial-update parsing         | Pydantic v2 `model_dump(exclude_unset=True)` ([FastAPI body-updates](https://fastapi.tiangolo.com/tutorial/body-updates/)) | —                                                                   |
| Concurrency                          | None needed (serial by user; last-write-wins; audit captures diff)                                                         | —                                                                   |
| Streaming                            | `runtime_stream_handshake.md` contract is unchanged                                                                        | —                                                                   |
| Facade routing                       | `backend-facade/app.py` proxy pattern                                                                                      | one PATCH route                                                     |
| FE state                             | existing `useConnectors()` hook + `ConnectorPopover` from Wave 3                                                           | tiny `useConversationConnectors()` hook returning `(scopes, patch)` |

**Net new code is intentionally small:**

- 1 SQL migration (~10 lines)
- 1 record-type Pydantic model (`ConversationConnectorScopes` typed-dict-ish)
- 1 service method + 1 store method (`get_scope_snapshot`, `update_scopes`) — both ~20 LOC
- 1 FastAPI route in `runtime_api`
- 1 proxy route in `backend-facade`
- 1 audit action constant
- 2 TypeScript types + 1 React hook

Total target: **~250 net LOC, ~80 of which is test fixtures.**

### 3.4 No third-party middleware needed

Web check for prior art: FastAPI's documented PATCH pattern uses Pydantic v2's `model_dump(exclude_unset=True)` with manual merge into the stored row — that's the de-facto best practice and what we'll use ([Body Updates · FastAPI](https://fastapi.tiangolo.com/tutorial/body-updates/), [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)). Heavier authorization frameworks (OPA, Casbin) are unwarranted — `ToolPermissionChecker` already enforces the policy at the read path; this PR only persists the input. `with_optimistic_retry` from `agent_runtime/persistence/optimistic.py` exists but is overkill here (it's keyed on `row_version` which `agent_conversations` doesn't carry, and the toggle is single-user-serial). RFC 7396 (JSON Merge Patch) is the prevailing convention for "send only what changes" — we follow its semantics without adopting a library.

### 3.5 Sequence — toggle Slack off, then send a new prompt

```
User                      FE                      facade                  ai-backend                  Postgres                     worker
 │                         │                        │                         │                            │                          │
 │  click "pause Slack"    │                        │                         │                            │                          │
 │ ──────────────────────► │                        │                         │                            │                          │
 │                         │  PATCH .../connectors  │                         │                            │                          │
 │                         │  { scopes:{slack:null} }│                        │                            │                          │
 │                         │ ─────────────────────► │  proxy + headers        │                            │                          │
 │                         │                        │ ──────────────────────► │  validate connector id     │                          │
 │                         │                        │                         │  merge-patch jsonb         │                          │
 │                         │                        │                         │ ─────────────────────────► │  UPDATE                  │
 │                         │                        │                         │ ─────────────────────────► │  INSERT runtime_audit_log│
 │                         │                        │ ◄────────────────────── │  return new scope map      │                          │
 │                         │ ◄───────────────────── │                         │                            │                          │
 │                         │  popover updates       │                         │                            │                          │
 │                         │                        │                         │                            │                          │
 │  type prompt + Send     │                        │                         │                            │                          │
 │ ──────────────────────► │                        │                         │                            │                          │
 │                         │  POST /runs            │                         │                            │                          │
 │                         │   (no scope header)    │                         │                            │                          │
 │                         │ ─────────────────────► │ ──────────────────────► │  RunService.create_run     │                          │
 │                         │                        │                         │  scopes ← row.snapshot     │                          │
 │                         │                        │                         │  freeze in runtime_context │                          │
 │                         │                        │                         │ ─────────────────────────► │  INSERT agent_runs       │
 │                         │                        │                         │                            │ ────────────────────────►│ claim
 │                         │                        │                         │                            │                          │ build deep agent
 │                         │                        │                         │                            │                          │ ToolPermissionChecker
 │                         │                        │                         │                            │                          │  filters slack_* OUT
 │                         │  SSE: tool_call(notion, drive, …)                                                                        │
 │                         │ ◄────────────────────────────────────────────────────────────────────────────────────────────────────── │
```

### 3.6 Edge cases

| Case                                                                 | Behaviour                                                                                                                                                    |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| User toggles during an active run on the same chat                   | Toggle persists; current run is unaffected (snapshot was taken at create-run); banner in the popover footer reads "Active for the next message".             |
| User toggles a connector that was disconnected since the last reload | `unknown_connector` 422; FE refreshes the connector list and re-renders the popover.                                                                         |
| Conversation is archived                                             | PATCH allowed, but FE hides the popover for archived chats — non-destructive on backend so an admin can still adjust before re-opening.                      |
| Workspace admin disables a connector globally                        | A run reads the scope snapshot, but `ToolPermissionChecker` short-circuits because the card itself becomes `enabled = false`. No reconciliation job needed.  |
| Cross-chat: user paused Slack in chat A                              | Chat B's row is independent — paused only in A. ✅ design intent.                                                                                            |
| Multi-tab race                                                       | Last write wins; both tabs see the post-PATCH `updated_at`; FE refetches on `visibilitychange` if `updated_at` skew is detected (cheap, no protocol change). |

### 3.7 Test plan

Lives in the same PR; minimum bar before merge.

**ai-backend (`services/ai-backend/tests/`)**

- `unit/runtime_api/conversations/test_update_connectors.py`
  - happy path merge-patch with `null` and `[…]` values
  - unknown connector / unknown scope → 422
  - foreign-org conversation → 404
  - non-owner non-admin → 403
- `unit/runtime_api/services/test_run_scope_fallback.py`
  - header present + scopes set → header wins
  - header empty `{}` → still wins (explicit override of "no connectors")
  - header omitted → conversation snapshot is materialised into runtime_context
- `unit/agent_runtime/capabilities/test_permissions_with_paused.py`
  - paused connector → cards filtered out of the model's tool list
- `integration/test_audit_emission_for_scope_update.py` — verifies one row per PATCH with diff metadata.

**Frontend (`apps/frontend/src/features/connectors/`)**

- `useConversationConnectors.test.tsx` — optimistic UI flips, rollback on 4xx.
- `ConnectorPopover.test.tsx` — paused state badge renders; toggle calls PATCH.

**Cross-service smoke** (`make test`): one happy path through facade → ai-backend → DB.

### 3.8 Rollout

- Flag-free. The new column defaults to `'{}'::jsonb`; old runs continue to use the header path.
- Zero-downtime: the migration is `ADD COLUMN ... DEFAULT … NOT NULL` with PG 11+ semantics (non-rewriting). Index creation is `CREATE INDEX IF NOT EXISTS`; in production, run via `CREATE INDEX CONCURRENTLY` (operator runbook addendum, not in the SQL file).
- Backout: drop the column (no consumers in any older binary). The header fallback restores prior behaviour.

### 3.9 Open questions

None blocking. A v2 enhancement could add **per-tool toggles inside a connector** (`{"slack": {"send_message": false, "search": true}}`); the column shape can absorb this without another migration by switching scope arrays to objects, gated by `schema_version` on the conversation row.

---

## 4 · Acceptance checklist

- [ ] Migration `0014_conversation_connector_scope.sql` applies cleanly forward and rolls back via the matching `.rollback.sql`.
- [ ] `ConversationsService.update_scopes()` returns `ConversationConnectorScopesResponse`; raises typed errors mapped to 4xx.
- [ ] `RunService.create_run` derives scopes from the conversation when the header is absent; existing tests stay green.
- [ ] One audit row per successful PATCH; chain verifier passes.
- [ ] `backend-facade` exposes `PATCH /v1/agent/conversations/{id}/connectors`, preserves identity headers, never reaches `/internal/v1/*`.
- [ ] `@0x-copilot/api-types` exports the two new types; `npm run typecheck` is green across `apps/frontend`, `packages/api-types`.
- [ ] `useConversationConnectors()` exposes `(scopes, patchScopes)`; `ConnectorPopover` calls it.
- [ ] No new event family in `runtime_api/schemas/events.py`; the streaming handshake doc is untouched.
- [ ] `make test` green; targeted ai-backend pytest suite green; frontend typecheck + build green.

---

## 5 · References

- [FastAPI · Body — Updates (PATCH semantics)](https://fastapi.tiangolo.com/tutorial/body-updates/)
- [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) — partial update + `model_dump(exclude_unset=True)` pattern
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — why we don't add a `runtime_capabilities_changed` event
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md) — facade-only ingress rule
- [`services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py`](../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py) — read-side enforcement (unchanged by this PR)
- [`services/ai-backend/src/runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py) — header parser (unchanged; still wins when present)
- RFC 7396 — JSON Merge Patch (semantics adopted, no library)
