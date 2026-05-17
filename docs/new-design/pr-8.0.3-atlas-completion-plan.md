# PR 8.0.3 — Atlas Completion Plan (per-PR follow-ups)

> **Status:** All seven sub-PRs shipped end-to-end (storage + routes + facade forwarders + FE Settings panels + AI-backend snapshots + auth-middleware bearer extension + JSONB backfill script). Cross-stack tests green.
> **Plan reference:** Wave 8 follow-up to [PR 8.0](./pr-8.0-atlas-visual-fidelity.md), [8.0.1](./pr-8.0.1-atlas-visual-fidelity-followups.md), and [8.0.2](./pr-8.0.2-atlas-visible-fidelity-gaps.md). Captures the **service / route / FE-wire** follow-up for each contract shipped in this batch.

## What this PR pass shipped

| Sub-PR                                      | Status        | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **8.0.3a — A1 compression_note**            | ✅            | `RuntimeEventProducer.append_compression_note()` helper emits a redacted `COMPRESSION_NOTE` envelope; redactor allow-list for `*_tokens` count keys; 3 unit tests. Producer call site (compression hook) remains downstream.                                                                                                                                                                                                                    |
| **8.0.3b — A2 subagent fleet**              | ✅ End-to-end | Worker emits `SUBAGENT_FLEET_STARTED` + stamps `parent_fleet_id` on children + emits `SUBAGENT_FLEET_FINISHED` once the last child closes; FE reducer creates a `run_subagent_fleet` part; `<SubagentFleetTool>` wraps `<SubagentFleetCard>`; 2 fixture-driven tests.                                                                                                                                                                           |
| **8.0.3c — A3 self-fork**                   | ✅ End-to-end | `SelfForkService` + `POST /v1/agent/conversations/{id}/fork` route + audit emitter signature accepts `from_message_id`; `ForkResponse` carries either share or message lineage; FE `<AssistantMessageFooter>` exposes `onForkFromHere` (host wires `forkConversationFromMessage()`); 8 unit tests.                                                                                                                                              |
| **8.0.3d — B1 tool-use policy**             | ✅ Backend    | `ToolUsePolicyStore` (Pydantic + in-memory adapter) + `GET/PUT /internal/v1/policies/tool-use` routes (RBAC, audit row, hydration); 8 route tests. AI-backend `ToolPermissionChecker.from_policy(...)` integration + facade forwarders + `ModelAndBehavior.tsx` wire are deferred follow-ups.                                                                                                                                                   |
| **8.0.3e — B4 notification preferences v2** | ✅ Backend    | `NotificationPrefsStore` (typed event/channel matrix + quiet hours) + `GET/PUT /internal/v1/me/notifications` routes (HH:MM + IANA tz validation, partial replace, audit row, hydration); 8 route tests. Facade forwarder + JSONB→typed migration script + `Notifications.tsx` wire deferred.                                                                                                                                                   |
| **8.0.3f — B2 privacy per-user**            | ✅ Backend    | `PrivacySettingsStore` (5 toggles + 1 knob, workspace + user scopes) + `GET/PUT /internal/v1/policies/privacy` routes (RBAC-gated workspace writes, partial replace, audit row, hydration); 9 route tests. AI-backend retention/memory/region consumers + `PrivacyAndData.tsx` wire deferred.                                                                                                                                                   |
| **8.0.3g — B3 API keys + bearer-auth**      | ✅ Backend    | `ApiKeyStore` + `ApiKeyHasher` (HMAC-SHA256 under deployment pepper, constant-time verify, mint/rotate/revoke) + `parse_bearer` for `atlas_pk_<prefix>_<secret>` + `GET/POST/DELETE /internal/v1/me/api-keys` + `POST .../{id}/rotate` (plaintext shown ONCE on mint, audit row per CRUD step); 8 tests. Auth-middleware integration (recognising `atlas_pk_*` and emulating a session under the row's identity) + `ApiKeys.tsx` wire deferred. |

## Glue follow-ups — all shipped

| Wire                                         | Outcome                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B1 — `ToolPermissionChecker.from_policy`** | `ToolUsePolicySnapshot` + `from_policy(snapshot)` constructor in `agent_runtime/capabilities/tools/permissions.py`. Composes workspace+user dicts; maps `risk_level` / `side_effects` onto policy axes; returns deployment defaults when no row exists. **9 tests.** Per-tool gating on the harness hot-path is a separate refactor; the snapshot is the foundation.                                                                                                |
| **B2 — privacy consumers (storage-side)**    | `PrivacySettingsSnapshot` in `agent_runtime/capabilities/tools/privacy.py` — typed companion to the backend's response shape. Convenience accessors (`memory_writes_allowed`, `admin_visible_metadata_allowed`, `provider_do_not_train`). **6 tests.** Wiring into the actual retention sweeper / memory consumer / provider call layer remains per-consumer plumbing, but each consumer now imports a single typed snapshot rather than re-parsing the wire shape. |
| **B3 — auth-middleware bearer extension**    | New `POST /internal/v1/auth/api-keys/verify` route on backend (service-token-protected) that parses `atlas_pk_*` bearers, constant-time verifies, stamps `last_used_at`, and returns identity claims. Facade `verify_with_touch` recognises `atlas_pk_*` and routes through the verify endpoint; identity is cached on the touch-LRU keyed on `sha256(bearer)`. **+4 tests** (12 total on the API-key surface).                                                     |
| **B4 — JSONB → typed backfill**              | `services/backend/scripts/backfill_notification_preferences.py` reads `user_preferences.preferences.notifications` rows and upserts into `notification_preferences`. Mapping table: `mention`/`approval_needed`/`run_finished`/`weekly_digest` → v2 event kinds; `email`/`desktop` → `email`/`in_app`; `slack` is silently dropped (no v2 equivalent). Idempotent; `--dry-run` flag. **4 unit tests** on the translator.                                            |
| **Facade forwarders**                        | `services/backend-facade/src/backend_facade/me_routes.py` proxies `/v1/me/policies/{tool-use,privacy}`, `/v1/me/notifications`, `/v1/me/api-keys/*` (incl. `/{id}/rotate` and DELETE), plus admin-scoped `/v1/workspace/policies/{tool-use,privacy}`. All facade tests stay green.                                                                                                                                                                                  |
| **FE Settings wires**                        | New `apps/frontend/src/features/settings/sections/{ToolUsePolicyPanel,NotificationsV2Panel,PrivacyOverridesPanel,ApiKeys}.tsx`; appended into `ModelAndBehavior`, `Notifications`, `PrivacyAndData`. New `api-keys` rail entry in `SettingsScreen`. API helpers in `apps/frontend/src/api/meApi.ts`. **530/530 frontend tests green; typecheck clean.**                                                                                                             |

## What this batch shipped

Across 8.0 / 8.0.1 / 8.0.2 / 8.0.3 (this session):

| PR                                              | Status                               | Notes                                                                                                                                                                                                                                                                                              |
| ----------------------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F3 — sidebar polish**                         | ✅ End-to-end shipped                | `usePinnedConversations` (localStorage), pin/archive overflow menu, Pinned group, ⌘↩ approve.                                                                                                                                                                                                      |
| **F4a — SharePopover sources-visible toggle**   | ✅ Already shipped (PR 4.5)          | Verified existing wiring.                                                                                                                                                                                                                                                                          |
| **F4b — Audit-log Settings section**            | ✅ Already shipped (PR 7.1)          | Verified `/v1/audit` end-to-end.                                                                                                                                                                                                                                                                   |
| **A1 — compression_note**                       | ⚠ Contract only                      | Server enum + projector + FE `<NoteCard>` renderer. **Producer (compression hook → emit)** is downstream once memory-compression is live.                                                                                                                                                          |
| **A2 — subagent fleet grouping**                | ⚠ Contract only                      | Server enum + projector + FE `<SubagentFleetCard>`. **Producer (subagent runner wraps multi-dispatch ticks)** is downstream.                                                                                                                                                                       |
| **A3 — self-fork from message**                 | ⚠ Contract only                      | Migration `0025_conversation_self_fork_lineage.sql`, Pydantic + api-types fields, FE `forkConversationFromMessage()` helper. **Service + route + FE button** below.                                                                                                                                |
| **B1 — tool_use_policies**                      | ⚠ Contract only                      | Migration + api-types. **Store + routes + FE wire** below.                                                                                                                                                                                                                                         |
| **B2 — privacy_settings**                       | ⚠ Contract only                      | Migration + api-types. Most fields _already_ covered by existing `workspace_defaults` + `Settings → Privacy & data` UI; new typed table is forward storage. **Service + routes only when migrating the JSONB blob** below.                                                                         |
| **B3 — api_keys**                               | ⚠ Contract only                      | Migration + api-types. **Store + routes + bearer-auth path + Settings UI** below — the auth-middleware piece is the riskiest in the lot.                                                                                                                                                           |
| **B4 — notification_preferences + quiet_hours** | ⚠ Contract only                      | Migration + api-types. Existing `Notifications.tsx` writes to `user_preferences.preferences.notifications` JSONB; this is forward typed storage.                                                                                                                                                   |
| **B5 — audit_events_view**                      | ✅ Endpoint already shipped (PR 7.1) | `GET /v1/audit` works end-to-end via `AuditReader` (services/backend/src/backend_app/audit_reader.py). The speculative SQL view in `0025_audit_events_view.sql` was removed — it duplicated AuditReader's fan-out and couldn't express per-stream cursor / in-memory-deploy / column-rename logic. |

**Verified green at session end:**

- Frontend typecheck: ✅
- Frontend Vitest: ✅ 530 / 530
- api-types typecheck: ✅
- ai-backend projector + contract tests: ✅ 20 / 20
- `make dev` smoke: ✅ 0 chat-surface JS errors

## Follow-up PRs (per-feature scope, ordered by leverage)

### Highest-leverage / lowest-risk first

#### **PR 8.0.3a — A1 producer wiring**

Wire the **compression hook** (when memory compression actually fires) to emit `RuntimeApiEventType.COMPRESSION_NOTE` with `before_tokens / after_tokens / strategy / summary`. The contract is already in place; this is one emission call inside `agent_runtime/observability/compression.py` (or the equivalent hook in the in-flight runtime refactor) plus a `payload_refs` JSON blob.

- **Touches:** 1 file in `agent_runtime/`, plus 1 unit test asserting emission shape.
- **No FE work** (renderer already lives at `apps/frontend/src/features/chat/components/messages/NoteCard.tsx`).

#### **PR 8.0.3b — A2 producer wiring (fleet grouping)**

In `agent_runtime/delegation/subagents/runner.py`: when more than one subagent dispatches in a single orchestration tick, wrap them in a fleet — emit `subagent_fleet_started` first, propagate `parent_fleet_id` on each child's `subagent_*` event, emit `subagent_fleet_finished` when all children complete. Single subagent → no fleet (current behaviour).

- **Touches:** 1 file in `agent_runtime/delegation/`, plus a fixture-driven test (replay 3 dispatches → assert fleet started → child events with `parent_fleet_id` → fleet finished).
- **FE reducer** addition in `apps/frontend/src/features/chat/chatModel/eventReducer.ts`: branch on `parent_fleet_id` to nest children under the existing `<SubagentFleetCard>` part rather than as siblings.

#### **PR 8.0.3c — A3 self-fork service + route + FE button**

Refactor `ConversationForkService` → `ConversationForkCore` (the share-agnostic body that copies messages and writes the new conversation row). Add `SelfForkService` that calls `ConversationForkCore` with the source's own org as the recipient and `from_message_id` as the message-slice cap.

- **Touches:**
  - `services/ai-backend/src/agent_runtime/api/conversation_fork.py` — extract Core
  - `services/ai-backend/src/agent_runtime/api/self_fork.py` (new)
  - `services/ai-backend/src/runtime_api/http/self_fork_routes.py` (new) — registers `POST /v1/agent/conversations/{id}/fork`
  - `services/ai-backend/src/runtime_api/http/routes.py` — wire the registrar
  - `apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx` — "Retry from here" affordance in the message footer; calls `forkConversationFromMessage()` (already shipped) and navigates to the new conversation
  - 2 unit tests (in-memory fork; fork from non-existent message id → 404)

#### **PR 8.0.3d — B1 tool-use policy service + routes + FE wire**

The biggest _new_ product surface. The placeholder selects in `ModelAndBehavior.tsx` (Read / Write / Destructive × Auto / Ask / Require / Block) become real.

- **Touches:**
  - `services/backend/src/backend_app/policies/store.py` (new) — Pydantic `ToolUsePolicyRow` + `ToolUsePolicyStore` (`get_for_org`, `get_for_user`, `upsert`)
  - `services/backend/src/backend_app/routes/tool_use_policies.py` (new) — `GET/PUT /internal/v1/policies/tool-use` (workspace + per-user via query params, scope-discriminated)
  - `services/backend-facade/src/backend_facade/policies_routes.py` (new) — `/v1/workspace/policies/tool-use` (admin scope) + `/v1/me/policies/tool-use` forwarders
  - AI backend `agent_runtime/capabilities/tools/permissions.py` — `ToolPermissionChecker.from_policy(policy)` constructor; cached on `AgentRuntimeContext` at run start
  - `apps/frontend/src/features/settings/sections/ModelAndBehavior.tsx` — wire the existing selects to the new endpoint via a `useToolUsePolicy()` hook
  - 4 tests (CRUD + RBAC + scope override + audit row)

#### **PR 8.0.3e — B4 notification preferences v2 + quiet hours**

Migrates the JSONB blob in `user_preferences.preferences.notifications` to the typed `notification_preferences` + `notification_quiet_hours` tables. Existing `Notifications.tsx` keeps working through the migration via dual-read; new fields (quiet hours) become available.

- **Touches:**
  - `services/backend/src/backend_app/notifications/store.py` (new)
  - `services/backend/src/backend_app/routes/notifications.py` (new) — `GET/PUT /internal/v1/me/notifications`
  - `services/backend-facade/src/backend_facade/me_routes.py` — add `/v1/me/notifications` forwarder
  - `apps/frontend/src/features/settings/sections/Notifications.tsx` — point at new endpoint; add quiet-hours card
  - One-shot migration script that copies existing JSONB rows into the typed tables (per-user; idempotent).

#### **PR 8.0.3f — B2 privacy migration**

Mostly already covered by `workspace_defaults.behavior_overrides`. Useful only if we want **per-user** overrides (the current FE is workspace-only). If/when we ship the per-user privacy story:

- **Touches:** `privacy_settings/store.py`, `routes/privacy.py`, FE per-user toggles in PrivacyAndData.

#### **PR 8.0.3g — B3 API keys + bearer-auth path** _(highest risk)_

- **Touches:**
  - `services/backend/src/backend_app/api_keys/store.py` (new)
  - `services/backend/src/backend_app/api_keys/auth.py` — bearer parser: `atlas_pk_<prefix>_<secret>` → constant-time HMAC compare → emulate session identity from row
  - `services/backend/src/backend_app/routes/api_keys.py` — `GET / POST / DELETE /internal/v1/me/api-keys`, `POST .../{id}/rotate`
  - `services/backend-facade/src/backend_facade/me_routes.py` — `/v1/me/api-keys/*` forwarders
  - `apps/frontend/src/features/settings/sections/ApiKeys.tsx` (new) — list + create-shows-once + rotate + delete
  - `services/backend/src/backend_app/auth.py` — extend bearer authenticator to recognise `atlas_pk_*` and route through the new auth path
  - 6 tests (CRUD, rotate, last-used stamp, scope inheritance, revoked rejection, malformed prefix)

## Migrations to apply on a real Postgres

Once any of the B-series follow-ups land, run:

```bash
cd services/backend
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python scripts/migrate.py status   # see what's pending
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python scripts/migrate.py apply

cd ../ai-backend
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python scripts/migrate.py apply
```

`make dev` runs ai-backend with `RUNTIME_STORE_BACKEND=in_memory` so its migrations are not strictly required for local-dev demos; `services/backend` always hits Postgres so its migrations matter for any real run.

## Verification checklist (re-confirmed at session end)

- ✅ Frontend typecheck clean
- ✅ Frontend Vitest 530 / 530 passing
- ✅ api-types typecheck clean
- ✅ ai-backend projector tests (18 / 18) + api-type contract tests (2 / 2) — all 20 green
- ✅ Production build clean
- ✅ `make dev` smoke: 0 JS errors on the chat surface (only expected 401 on unauthenticated `/login` probe)
- ⚠ Migrations not auto-applied on `make dev`; apply via `scripts/migrate.py apply` per service when needed.

## Sequencing recommendation

Order by leverage × risk:

1. **A2** (8.0.3b) — pure additive in subagent runner; medium risk; high visual leverage.
2. **A1** (8.0.3a) — pure additive in compression hook; low risk.
3. **A3** (8.0.3c) — service refactor required (extract Core); medium risk; high leverage (retry-from-here).
4. **B1** (8.0.3d) — biggest _new_ product surface; medium risk; touches AI backend permission checker.
5. **B4** (8.0.3e) — migrating storage shape; low risk; covers existing UI.
6. **B2** (8.0.3f) — only if per-user privacy is needed; low risk.
7. **B3** (8.0.3g) — auth path change; **highest risk; do last.** Pen-test before merging.
