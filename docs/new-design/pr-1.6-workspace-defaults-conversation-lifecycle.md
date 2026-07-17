# PR 1.6 — Workspace Defaults + Conversation Lifecycle

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 1, PR 1.6 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (write path) · backend-facade (proxy) · frontend (Settings → Workspace + sidebar)
> **Size:** S (one new table, one ALTER, two endpoints, sidebar grouping is FE-only). Targeted at one PR.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md), [`docs/decomp/persistence/_index.md`](../decomp/persistence/_index.md)
> **Sibling docs:**
> – PR 1.2 — Per-chat connector scope persistence (already landed: `enabled_connectors` JSONB on conversation row)
> – PR 1.3 — Draft artifact (in flight)
> – PR 1.4 — Two-stage approvals (in flight)
> – PR 1.5 — Subagent + workspace pane data feeds (in flight)

---

## 1 · PRD

### Problem

The Atlas design doc (Settings → Workspace) requires a workspace owner to set:

- **Default model** new chats start on (today: deployment-wide via `agent_runtime/settings.py:default_model`),
- **Default connectors** active for new chats (today: every connector the user has authenticated with shows up; nothing is "off by default"),
- **Retention policy** (today: per-deployment 365-day floor in `backend_facade/deployment_profile.py`; the per-tenant `retention_policies` table from migration 0012 supports it but is not surfaced),
- **Folders** to organise long chat lists (today: flat list; the design's sidebar shows `Workspace › Folder` crumbs and `Today / Yesterday / Earlier` groupings),
- **Soft-delete** so deleting a chat is reversible inside the retention window and gets reaped by the sweeper later (today: `archived_at` exists but no `deleted_at`; "delete" is destructive at the API).

The same conversation row will, in Wave 6 (sharing), need a **fork lineage** pointer (`parent_conversation_id`) so a recipient can fork a shared thread into their own workspace. We forward-declare that column here so Wave 6 doesn't need another migration.

Without this PR:

- Sarah's "Q1 launch" chat starts every new conversation with all connectors live, even ones the workspace owner wanted off by default for new chats.
- An admin setting workspace retention can only do it through `POST /v1/retention/policies` directly — the Settings UI has nothing to call.
- Deleting a chat is destructive — there's no "undo" window and no soft-tombstone for the retention sweeper to reap.
- The sidebar can't render folders or `Today / Yesterday / Earlier` groups because the data doesn't carry a folder and the FE has no contract telling it to group locally.

### Goals

1. A workspace admin can set **default model** + **default connectors** for new chats; the runtime honours them at conversation-create time.
2. A workspace admin can set **per-org retention TTL** through Settings — reusing the existing `retention_policies` table (no new retention storage).
3. A user can **soft-delete** a chat; it disappears from the sidebar, the retention sweeper reaps it on TTL.
4. A user can put a chat in a **folder** (string label); the sidebar groups by folder and `Today / Yesterday / Earlier`.
5. The conversation row carries a nullable `parent_conversation_id` so Wave 6 fork lineage is one column behind the existing schema, not a follow-up migration.
6. Zero new event types. Zero changes to the streaming handshake. Zero new third-party dependencies.

### Non-goals

- Per-folder access control or moving folders between orgs (folders are flat string labels in this PR — we deliberately do not introduce a `folders` table).
- Workspace name / slug / logo editing (that surface lives in `services/backend` `organizations` and is out of this PR's scope; tracked separately in PR 4.2).
- Multi-select bulk delete / archive (P1 follow-up; this PR ships the row-level soft-delete only).
- Per-user retention overrides through the UI (the table supports `scope='user'`; Settings UI for it can land later — see §3.9).
- Recipient-fork creation logic (the column is added; the **endpoint** + recipient-view UI ships in PR 6.2).
- Drag-reorder / pin chats (design's "later" pills).

### Success criteria

- ✅ `POST /v1/agent/workspace/defaults` (admin) writes `{default_model, default_connectors, retention_days}` and returns the full effective view in <80 ms p99 against the local stack.
- ✅ Creating a new conversation with no model/connectors in the request inherits both from `workspace_defaults` (and only then falls back to `agent_runtime/settings.py` defaults if the row is absent).
- ✅ `DELETE /v1/agent/conversations/{id}` sets `deleted_at` (does not destroy rows); `GET /v1/agent/conversations` filters them by default; setting `?include_deleted=true` returns them; the retention sweeper reaps them after the org's `messages` TTL.
- ✅ `PATCH /v1/agent/conversations/{id}` accepts `{folder, title}` updates; sidebar renders folder groups.
- ✅ One audit row per write (defaults update, soft-delete, folder rename) in `runtime_audit_log`.
- ✅ The streaming handshake is byte-for-byte unchanged. PR 1.1 (citations), PR 1.3 (drafts), PR 1.4 (approvals), PR 1.5 (workspace pane) merge in any order around this PR with no conflict in event schemas, projections, or replay.

### User stories

| As…              | I want…                                                              | So that…                                                              |
| ---------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Marcus (admin)   | new chats to default to "Atlas Reasoning" with Slack + Drive on      | members don't have to manually configure each new chat                |
| Marcus           | to set workspace retention to 90 days from one Settings toggle       | I'm not making people memorise scope/kind in `POST /v1/retention/...` |
| Sarah (end user) | to delete a chat and undo within 30 days                             | "delete" doesn't mean "forever"                                       |
| Sarah            | to drop a chat into "Launches" and see it grouped in the sidebar     | I can find Q1-launch threads three weeks later                        |
| Future-Wave-6    | the conversation row to already carry `parent_conversation_id`       | sharing/fork ships as a logic-only PR, not a schema PR                |
| Atlas team       | to never need a new event family or stream change for these features | streaming work in PRs 1.1/1.3/1.4/1.5 doesn't conflict                |

---

## 2 · Spec

### 2.1 Wire — workspace defaults

**Read** `GET /v1/agent/workspace/defaults`

```jsonc
{
  "default_model": {
    "provider": "openai",
    "model_name": "gpt-5.4-mini",
    "reasoning": null,
  },
  "default_connectors": {
    "notion": ["read", "write_drafts"],
    "drive": ["read"],
    "slack": null, // installed but off by default for new chats
  },
  "retention_days": 90, // resolved from retention_policies.scope='org'
  "updated_at": "2026-05-05T16:01:14.220Z",
  "updated_by_user_id": "user_…",
}
```

When no row exists for the org, the response materialises deployment defaults
(`agent_runtime/settings.py:default_model` and `deployment_profile.default_retention_days`) so the FE always sees a real shape.

**Write** `PUT /v1/agent/workspace/defaults` (admin-only) — full-document replace, _not_ merge-patch. This is intentional: defaults are short, the admin is editing a Settings panel where partial intent isn't a thing.

```jsonc
{
  "default_model": {
    "provider": "openai",
    "model_name": "gpt-5.4-mini",
    "reasoning": null,
  },
  "default_connectors": {
    "notion": ["read", "write_drafts"],
    "drive": ["read"],
    "slack": null,
  },
  "retention_days": 90,
}
```

The request body is validated by the same `ConnectorScopeValidator` already used in PR 1.2 (we don't duplicate the shape check). `retention_days` is delegated to the existing retention pipeline (see §2.3 below) — _we do not introduce a new retention column_.

### 2.2 Wire — conversation lifecycle (extends existing routes)

| Verb     | Path                                       | Effect                                                                                                 |
| -------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `PATCH`  | `/v1/agent/conversations/{id}`             | Update `title`, `folder`, `archived_at` (set/null) — no breaking-change to the existing PATCH surface. |
| `DELETE` | `/v1/agent/conversations/{id}`             | **Soft-delete**: sets `deleted_at = now()`, status stays `'archived'`. Returns 204.                    |
| `POST`   | `/v1/agent/conversations/{id}/restore`     | Clears `deleted_at`. 404 if the row was retention-reaped.                                              |
| `GET`    | `/v1/agent/conversations?include_deleted=` | Default `false`. When `true`, returns soft-deleted rows still inside the retention window.             |

Existing `GET /v1/agent/conversations` response gains two fields per item — `folder: string \| null` and `deleted_at: string \| null`. No new top-level pagination shape.

`PATCH /v1/agent/conversations/{id}` extends to accept:

```jsonc
{
  "title": "Q1 launch — review",
  "folder": "Launches",
  "archived": true,
}
```

Each field is optional. RFC 7396 merge-patch semantics — omit a field to leave it untouched, send `null` to clear (`folder: null` removes the folder; `archived: false` un-archives). Title rewrites use `null`-to-clear semantics too (FE renders "Untitled chat" when `null`).

### 2.3 Persistence

**Two migrations.** Both numbered after the in-flight ones (PR 1.3 lands `0014_runtime_drafts.sql`, PR 1.1 lands `0015_runtime_citations.sql`, PR 1.2 already merged `0016_conversation_connector_scope.sql`).

#### 2.3.1 ai-backend `0017_workspace_defaults.sql`

```sql
-- One row per org. Workspace-scoped runtime defaults that the conversation
-- creator + run service consult when the request omits a field.
--
-- Retention deliberately lives in `retention_policies` (migration 0012):
-- this table does NOT carry a retention column. The Settings UI writes
-- one row to retention_policies with scope='org', kind='messages' (and
-- siblings for events/checkpoints) when the admin moves the retention
-- slider — that's the same path an operator using `POST /v1/retention/
-- policies` would take.

CREATE TABLE IF NOT EXISTS workspace_defaults (
    org_id              TEXT PRIMARY KEY,
    default_model       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- { provider, model_name, reasoning? }
    default_connectors  JSONB NOT NULL DEFAULT '{}'::jsonb,   -- same shape as agent_conversations.enabled_connectors
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id  TEXT
);

-- RLS (mirrors existing migration 0008 pattern; the `set_config('app.current_org', …)`
-- guard already runs on every connection from the runtime API.)
ALTER TABLE workspace_defaults ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workspace_defaults
    USING (org_id = current_setting('app.current_org', true));
```

Why a dedicated table over `organizations.metadata`:

- ai-backend cannot read `organizations` (lives in the **backend** service; service boundary). A row in ai-backend is the right home.
- One row per org, one writer (admin), one reader (RunService + ConversationService). Trivially cacheable in the worker if it ever shows up in profiling.
- Schema evolution lives next to the runtime code that consumes it.

#### 2.3.2 ai-backend `0018_conversation_lifecycle.sql`

```sql
ALTER TABLE agent_conversations
    ADD COLUMN IF NOT EXISTS deleted_at              TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS folder                  TEXT,
    ADD COLUMN IF NOT EXISTS parent_conversation_id  TEXT;

-- Hot-path index: the sidebar query is "active, undeleted, this user's
-- conversations newest-first" — extend the existing one to skip deleted rows.
CREATE INDEX IF NOT EXISTS idx_agent_conversations_org_user_active_updated
    ON agent_conversations (org_id, user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

-- Folder filter (sparse — most rows have NULL folder; partial index keeps it small).
CREATE INDEX IF NOT EXISTS idx_agent_conversations_folder
    ON agent_conversations (org_id, user_id, folder, updated_at DESC)
    WHERE folder IS NOT NULL AND deleted_at IS NULL;

-- parent_conversation_id is forward-declared for Wave 6 (sharing fork).
-- No FK self-reference yet — Wave 6 adds it together with the share schema.
```

We deliberately keep the existing `idx_agent_conversations_org_user_updated` index untouched (other code paths may rely on its full coverage); the new partial index is what `list_conversations` will use after this PR.

#### 2.3.3 What we are _not_ adding

| Thing                                    | Why not                                                                                            |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `retention_days` column on conversation  | `retention_policies(scope='conversation')` already does this. Per-row override = one row inserted. |
| `retention_days` on `workspace_defaults` | `retention_policies(scope='org')` already does this. The admin slider writes one row.              |
| `folders` table + folder ACL             | Folders are personal organisational labels in v1. A table costs migrations + ACLs for no win.      |
| `status` enum widening                   | `deleted_at` timestamp captures _both_ state and _when_. Enums are stickier; timestamps compose.   |
| New event family for soft-delete         | The conversation row is the source of truth; clients refetch on `visibilitychange` or 304.         |

### 2.4 Audit

One row per privileged write into `runtime_audit_log` (existing chain; same hashing as PR 1.2):

| Action                      | Metadata                                                               |
| --------------------------- | ---------------------------------------------------------------------- |
| `workspace.defaults.update` | `{ before, after, diff_keys, retention_policy_ids: [...] }`            |
| `conversation.delete`       | `{ conversation_id, folder, retention_until }`                         |
| `conversation.restore`      | `{ conversation_id }`                                                  |
| `conversation.update`       | `{ conversation_id, before, after, diff_keys }` (folder/title/archive) |

The `retention_policy_ids` array in `workspace.defaults.update` cross-references rows the call inserted/updated in `retention_policies`, so a forensic reader can chase a single audit event back to all the storage rows it affected.

### 2.5 Permissions

| Caller                 | `workspace_defaults`                                            | Conversation lifecycle                                               |
| ---------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------- |
| Conversation owner     | ❌ (read public defaults via `GET`; cannot write)               | ✅ — own rows                                                        |
| Workspace admin        | ✅ read + write                                                 | ✅ on any row in their org (matches PR 1.2.1 admin-override pattern) |
| Other workspace member | ❌ write; ✅ read public `default_model` + `default_connectors` | ❌                                                                   |
| Service-to-service     | Read-only via the existing `RuntimeServiceAuthenticator`        | Per-call as today                                                    |

The admin check reuses [`runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py)'s `ADMIN_USERS` permission scope — the exact same constant PR 1.2.1 introduced for connector admin override. No new role, no new RBAC primitive.

### 2.6 Error semantics

| Condition                                        | Status | Code                     |
| ------------------------------------------------ | ------ | ------------------------ |
| Caller not admin → `PUT /workspace/defaults`     | 403    | `forbidden`              |
| `default_model.provider` not in catalog          | 422    | `unknown_model_provider` |
| `default_model.model_name` not in catalog        | 422    | `unknown_model_name`     |
| `default_connectors` shape invalid               | 422    | `invalid_request`        |
| `retention_days < 1` or `> 3650`                 | 422    | `invalid_retention_days` |
| `DELETE` / `PATCH` on foreign-org conversation   | 404    | `conversation_not_found` |
| `restore` after retention sweeper reaped the row | 404    | `conversation_not_found` |
| `folder` longer than 64 chars (UI limit)         | 422    | `invalid_folder`         |

Model-catalog validation reuses the same `ModelCatalog` already loaded by [`runtime_api/services/runs.py`](../../services/ai-backend/src/runtime_api/services/runs.py); we do not duplicate the model registry. Connector-scope validation reuses `ConnectorScopeValidator` from PR 1.2.

### 2.7 Frontend contract (`@0x-copilot/api-types`)

Three additions:

```ts
// packages/api-types/src/index.ts
export interface WorkspaceDefaults {
  default_model: {
    provider: string;
    model_name: string;
    reasoning?: Record<string, unknown> | null;
  };
  default_connectors: ConversationConnectorScopes; // re-uses PR 1.2 type
  retention_days: number;
  updated_at: string;
  updated_by_user_id: string | null;
}

export interface UpdateConversationRequest {
  title?: string | null;
  folder?: string | null;
  archived?: boolean;
}
```

`Conversation` (existing type) gets two new optional fields:

```ts
export interface Conversation {
  // ... existing fields
  folder: string | null;
  deleted_at: string | null;
  parent_conversation_id: string | null; // populated only after Wave 6
}
```

`@0x-copilot/api-types` is an additive change — no consumer breaks. Once the types ship, the FE Settings page (PR 4.2) and sidebar (PR 2.2) can render against them.

### 2.8 What `RunService.create_run` and `ConversationService.create_conversation` change

Today's `create_conversation`:

```python
record = ConversationRecord(
    org_id=request.org_id,
    user_id=request.user_id,
    assistant_id=request.assistant_id or settings.default_assistant_id,
    title=request.title,
    metadata=request.metadata or {},
    enabled_connectors={},
)
```

After this PR (single new line, gated by an absent client value):

```python
defaults = await self.persistence.get_workspace_defaults(org_id=request.org_id)
record = ConversationRecord(
    org_id=request.org_id,
    user_id=request.user_id,
    assistant_id=request.assistant_id or settings.default_assistant_id,
    title=request.title,
    metadata=request.metadata or {},
    enabled_connectors=request.enabled_connectors or defaults.default_connectors,
)
```

Today's `create_run` model resolution chain (in [`agent_runtime/execution/models.py`](../../services/ai-backend/src/agent_runtime/execution/models.py)) is:

```
request.model  →  conversation.assistant.model  →  settings.default_model
```

After this PR:

```
request.model  →  conversation.assistant.model  →  workspace_defaults.default_model  →  settings.default_model
```

The chain is one slot longer; nothing else moves. The worker's `RuntimeContext` still sees a single resolved `ModelConfig`, so capabilities/middleware/token-budgeting continue working unchanged.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────┐       PUT /v1/agent/workspace/defaults  (admin)
   │   apps/        │ ────────────────────────┐
   │   frontend     │                         │
   │  Settings →    │ ◄───────────────────────┤  (200 with full defaults view)
   │  Workspace     │                         │
   │  Sidebar group │   GET/PATCH/DELETE/POST conversations + .../restore
   └────────────────┘ ────────────────────────┐
                                              │
                                              ▼
                                    ┌────────────────────┐
                                    │  backend-facade    │  thin proxy, headers preserved
                                    │  /v1/agent/...     │  (no business logic here)
                                    └─────────┬──────────┘
                                              │ /internal/v1/agent/...
                                              ▼
                                    ┌────────────────────┐
                                    │  ai-backend        │  WorkspaceDefaultsService
                                    │  runtime_api       │  ConversationsService
                                    └────┬──────────┬────┘
                                         │          │
                              writes     │          │ writes
                                         ▼          ▼
                          ┌─────────────────┐    ┌──────────────────────────┐
                          │workspace_       │    │agent_conversations       │
                          │defaults         │    │  + deleted_at, folder,   │
                          │  (one row /org) │    │    parent_conversation_id│
                          └─────────────────┘    └──────────────────────────┘
                                ▲                     ▲
                                │ READ at create-conv │ READ on every list
                                │ (defaults fallback) │ (filters deleted_at)
                                │                     │
                          ┌─────┴──────┐        ┌─────┴──────────────────┐
                          │ Run/       │        │ list_conversations     │
                          │ Conversa-  │        │   service              │
                          │ tion       │        │   sidebar query path   │
                          │ Service    │        │                        │
                          └────────────┘        └────────────────────────┘

                     retention slider in Settings panel ─────► POST /v1/retention/policies
                     (existing C8 admin CRUD, no change)         (already shipped)

                     retention sweeper loop (existing)  ─────► reaps soft-deleted convs
                     when conversation's resolved TTL elapses    via existing kind=messages path
```

The diagram emphasises the rule: **no new path, no new service, no new event**. Two existing services gain endpoints; one existing pipeline (retention) gains a UI surface.

### 3.2 Streaming impact — explicitly **none**

This is the question the user flagged ("how the entire system will come along — agent harness, streaming events, FE, DB schema").

| Subsystem                                  | Touched by this PR?                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events` schema                    | **No.** No new `event_type`, no projection change, no `sequence_no` semantic change.                                                                                                                                                                                                                                                                                         |
| `RuntimeEventEnvelope` Pydantic            | **No.**                                                                                                                                                                                                                                                                                                                                                                      |
| SSE handshake (`?after_sequence=N`)        | **No.** Reconnect is byte-identical.                                                                                                                                                                                                                                                                                                                                         |
| Worker `runtime_worker/` job loop          | **No.** Worker reads `runtime_context_json` from the run row, which is already populated correctly by the slightly extended `create_run` model-resolution chain.                                                                                                                                                                                                             |
| Capabilities middleware, tools, MCP loader | **No.** They consume `enabled_connectors` from the run snapshot — the new column on the row only changes _which value gets snapshotted_, not how snapshotting works.                                                                                                                                                                                                         |
| Citation registry (PR 1.1)                 | **No.** Independent.                                                                                                                                                                                                                                                                                                                                                         |
| Drafts (PR 1.3)                            | **No.** Drafts attach to a conversation; soft-deleting the conversation cascades naturally because every dependent migration uses `ON DELETE CASCADE` already. We never _physically_ delete the conversation row in this PR — the sweeper does that on TTL — so the cascade only fires after TTL.                                                                            |
| Approvals (PR 1.4)                         | **No.**                                                                                                                                                                                                                                                                                                                                                                      |
| Subagents (PR 1.5)                         | **No.**                                                                                                                                                                                                                                                                                                                                                                      |
| Audit chain                                | **Yes (additive).** Three new `action` constants. No chain semantic change.                                                                                                                                                                                                                                                                                                  |
| Retention sweeper                          | **Yes (additive).** Already reaps `agent_messages` etc. by org-scope TTL; soft-deleted conversations now also disappear once their `messages` TTL expires because their dependent rows are reaped first and a follow-up cleanup pass removes the parent row when no children remain. (If we want eager reaping inside a shorter "trash" window we add it later — out of v1.) |

**There is exactly one runtime-state question in this PR**: _what happens if a user soft-deletes a conversation while a run is streaming?_

The answer: **`DELETE` on a conversation with an active run is allowed; it cancels the run.** The existing `cancel_run` path (already wired to emit `run_cancelled` and stop the SSE stream cleanly) is invoked from `delete_conversation` before the `deleted_at` is written. The FE's stream handler already handles `run_cancelled` (per PR 1.2's chat surface) — no FE change needed beyond surfacing a toast. This makes the lifecycle decision deterministic: a deleted conversation cannot continue producing events.

```
   user clicks "delete chat"
        │
        ▼
   FE: optimistic remove from sidebar
        │
        │  DELETE /v1/agent/conversations/{id}
        ▼
   ConversationsService.delete()
        │
        ├── if active run on this conv → cancel_run(run_id, reason="conversation_deleted")
        │                                  └── emits run_cancelled (existing)
        │
        ├── UPDATE agent_conversations SET deleted_at = NOW()
        │
        └── INSERT runtime_audit_log (conversation.delete)

   FE: SSE stream closes via run_cancelled (existing path), no toast surprise
```

### 3.3 Why workspace defaults live in **ai-backend**, not backend

Per the service-boundaries doc, "tenants, IdP integration, permissions, product persistence, admin workflows" belong in **backend**. So why does `workspace_defaults` live in **ai-backend**?

Because the _consumers_ of the row are runtime services (`RunService.create_run`, `ConversationService.create_conversation`) that cannot import backend and need a sub-millisecond local read at conversation-create. The alternative — backend owns the row, ai-backend fetches via HTTP at every conversation-create — adds a hop for a value that changes once a quarter, costs an in-flight network round-trip on every chat creation, and forces backend to know the runtime model catalog (it doesn't and shouldn't).

The same pragmatic call was made for `retention_policies` (lives in ai-backend, keyed by `org_id`, written by an admin Settings UI through the ai-backend HTTP surface). This PR follows that precedent.

The boundary that **does** matter — `workspace_defaults` is keyed by `org_id` and the **definition** of an org (creation, deletion, slug, billing status) lives in backend's `organizations` table. ai-backend does not insert into `workspace_defaults` until something happens for an org that already exists in backend; the only writer is the admin path which proves org membership through the existing identity headers. There's no cross-service consistency requirement (a deleted org's stale `workspace_defaults` row just sits there until reaped — harmless).

### 3.4 Why retention reuses `retention_policies` and adds _no_ new column

The retention story is fully built — see [`migrations/0012_retention_policies.sql`](../../services/ai-backend/migrations/0012_retention_policies.sql) and [`runtime_worker/jobs/retention_sweeper.py`](../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py). It supports:

- per-org defaults (`scope='org'`, `resource_id IS NULL`),
- per-user override (`scope='user'`, `resource_id=user_id`),
- per-conversation override (`scope='conversation'`, `resource_id=conversation_id`),
- per-assistant override (`scope='assistant'`, `resource_id=assistant_slug`),
- multiple kinds (`messages | events | context_payloads | checkpoints | memory_items`).

The Settings UI's "Retention" slider's only job is to write **one row per kind** at scope `org`:

```python
for kind in (RetentionKind.MESSAGES, RetentionKind.EVENTS, RetentionKind.CHECKPOINTS):
    await persistence.upsert_retention_policy(
        RetentionPolicyRecord(
            org_id=org_id,
            scope=RetentionScope.ORG,
            kind=kind,
            ttl_seconds=retention_days * 86_400,
            created_by_user_id=actor_user_id,
        )
    )
```

That's it. No new sweeper, no new resolver, no new column. The `WorkspaceDefaultsService.update()` method orchestrates the call (single transaction with the workspace_defaults upsert + the three retention upserts + one audit row) so the admin sees one atomic write.

This composition is the hard rule: **DRY across PRs in the same wave is non-negotiable** — PR 1.2 added connector scope shape validation, PR 1.6 reuses it; C8 added retention storage + sweeper, PR 1.6 reuses both. No second source of truth.

### 3.5 Sidebar grouping is a frontend concern

The Atlas sidebar groups chats by `Today / Yesterday / Earlier`. The plan's first draft proposed an `X-Enterprise-Timezone` header so the backend could group server-side. We do not need it.

```ts
// apps/frontend/src/features/chat/components/sidebar/groupConversations.ts
const groupByDay = (conversations: Conversation[], now: Date): Group[] => {
  const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "short" });
  const todayKey = fmt.format(now);
  const yesterdayKey = fmt.format(new Date(now.getTime() - 86_400_000));
  return groupBy(conversations, (c) => {
    const k = fmt.format(new Date(c.updated_at));
    if (k === todayKey) return "Today";
    if (k === yesterdayKey) return "Yesterday";
    return "Earlier";
  });
};
```

The browser's `Intl.DateTimeFormat` is the user's local timezone by definition — exactly what the design wants. Folder grouping nests under the day groups in the same one-pass reducer.

**Counter-arguments considered:**

- _"What if mobile clients can't compute this?"_ — they all have an `Intl` runtime; this is a 12-line reducer.
- _"What if we want server-side cursor pagination by group?"_ — the existing `(updated_at DESC)` index is the natural cursor; groupings are presentation, not query semantics.
- _"What about time-of-day in the row label?"_ — same `Intl.DateTimeFormat({ timeStyle: 'short' })` call.

We pay zero protocol surface, zero header parsing, and zero server timezone bugs.

### 3.6 DRY — what we reuse vs. what we add

| Concern                             | Reuse                                                                                                                      | Add                                                                                                                                              |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Identity / RBAC                     | `RuntimeServiceAuthenticator`, `runtime_api/rbac.py`, `ADMIN_USERS` scope                                                  | —                                                                                                                                                |
| Connector ID/scope validation       | `ConnectorScopeValidator` (PR 1.2)                                                                                         | —                                                                                                                                                |
| Model catalog validation            | `ModelCatalog.list_models()`                                                                                               | —                                                                                                                                                |
| Retention storage                   | `retention_policies` (migration 0012) + `RetentionPolicyResolver` + `RetentionSweeperLoop`                                 | one new audit `action` constant                                                                                                                  |
| Retention HTTP surface              | `POST/GET/DELETE /v1/retention/policies` (already exists)                                                                  | a server-side composition (`upsert_retention_policy × kinds`) inside `update_workspace_defaults`                                                 |
| Persistence pool / migration runner | `agent_runtime/persistence/schema/migrate.py`                                                                              | one new table + one ALTER + two indexes                                                                                                          |
| Audit chain                         | `runtime_audit_log` writer                                                                                                 | three new `action` constants                                                                                                                     |
| PATCH partial-update parsing        | Pydantic v2 `model_dump(exclude_unset=True)` ([FastAPI body-updates](https://fastapi.tiangolo.com/tutorial/body-updates/)) | —                                                                                                                                                |
| Soft-delete primitive               | `users.deleted_at` + partial unique index pattern from `0004_identity_foundation.sql`                                      | one column + partial index, identical pattern                                                                                                    |
| Cancellation on conversation delete | existing `cancel_run` path (used by `POST /v1/agent/runs/{id}/cancel`)                                                     | one call site                                                                                                                                    |
| Streaming                           | `runtime_stream_handshake.md` contract is unchanged                                                                        | —                                                                                                                                                |
| Facade routing                      | `backend-facade/app.py` proxy pattern + `forward_json_to_ai`                                                               | five proxy routes (4 lifecycle + 1 defaults; the `PUT /defaults` is one route, the `GET /defaults` is the other half — total **2** for defaults) |
| FE state                            | existing `useConversations()` + `useSettings()`                                                                            | one `useWorkspaceDefaults()` hook returning `(defaults, save)`                                                                                   |
| Day grouping                        | `Intl.DateTimeFormat` (browser built-in)                                                                                   | a 12-line `groupConversations.ts` reducer                                                                                                        |
| Folder UX                           | existing `ContextMenu` from `@0x-copilot/design-system`                                                                    | one menu item ("Move to folder…") + a tiny modal                                                                                                 |

**Net new code** is intentionally small:

- 2 SQL migrations (~25 lines combined).
- 2 Pydantic shapes (`WorkspaceDefaultsRecord`, `WorkspaceDefaultsResponse`); existing shapes extended with optional fields.
- 1 service file `workspace_defaults_service.py` (~80 LOC, includes retention orchestration).
- 1 service method extension (`ConversationsService.update / delete / restore`) reusing existing query helpers (~60 LOC).
- 7 FastAPI routes (2 defaults + 4 lifecycle + 1 restore).
- 5 facade proxy routes (one mirror per public route).
- 4 audit `action` constants.
- 1 Settings panel (PR 4.2) — _separate FE PR; unblocked by this contract_.

Total target: **~520 net LOC, ~160 of which is test fixtures + table-driven validators.**

### 3.7 No third-party middleware needed

Web-survey of likely candidates and why we skip them:

- **`sqlalchemy-easy-soft-delete` / `sqlalchemy-soft-delete`** — the codebase uses raw SQL adapters (`runtime_adapters/postgres/runtime_api_store.py`), not SQLAlchemy ORM. The existing `users.deleted_at` pattern is a 2-line idiom; a library would be net-negative.
- **`apscheduler` / `pg_cron` / `pg_partman`** — the retention sweeper already uses an `asyncio.create_task` loop with `RETENTION_SWEEP_INTERVAL_SECONDS` (env-driven, restartable, dry-run flag). It's the established pattern (see [`runtime_worker/jobs/retention_sweeper.py`](../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py); the same shape was used by `UsageRollupLoop` from migration 0007). No scheduler dep.
- **`python-jsonpatch` / `jsonpatch`** — RFC 7396 merge-patch is half a page of code, already implemented inline by PR 1.2. Pulling a dependency for `~`200 LOC of trivial map merge is overkill.
- **`structlog`-style audit logger libs** — `runtime_audit_log` already has a full append-only chain (HMAC, prev_hash, key_version) per migration 0003. Replacing it would lose the chain verifier. Reuse.
- **OPA / Casbin** — no new policy. The admin check is "does the caller have `ADMIN_USERS` in their permission_scopes" — a single tuple membership.
- **`tzlocal` / `pytz` (server-side)** — not needed: grouping is FE; server stores `updated_at` UTC.

The only library decision worth validating against the broader Python community is **PATCH semantics**, which we lift verbatim from FastAPI's documented pattern ([Body — Updates](https://fastapi.tiangolo.com/tutorial/body-updates/) using `model_dump(exclude_unset=True)`) — the same pattern PR 1.2 already uses. RFC 7396 (JSON Merge Patch) gives us the field-level `null = clear, omit = no-op` rule.

### 3.8 Sequence — admin sets workspace defaults, member creates a chat

```
admin                  FE                        facade               ai-backend                   Postgres                       worker
 │                      │                          │                     │                            │                              │
 │  open Settings →     │                          │                     │                            │                              │
 │   Workspace          │                          │                     │                            │                              │
 │ ──────────────────► │  GET /workspace/defaults │                     │                            │                              │
 │                      │ ───────────────────────►│ ──────────────────► │  WorkspaceDefaultsService  │                              │
 │                      │                          │                     │ ◄──────────────────────── │  SELECT defaults row + ORG TTL│
 │                      │ ◄─────────────────────── │ ◄────────────────── │  hydrate w/ deployment fb │                              │
 │                      │  panel populates         │                     │                            │                              │
 │                      │                          │                     │                            │                              │
 │  drag retention slider│                         │                     │                            │                              │
 │  to 90 days, save    │                          │                     │                            │                              │
 │                      │  PUT /workspace/defaults │                     │                            │                              │
 │                      │ ───────────────────────►│ ──────────────────► │  authorize ADMIN_USERS    │                              │
 │                      │                          │                     │  validate model+conn+ttl  │                              │
 │                      │                          │                     │  BEGIN TX                 │                              │
 │                      │                          │                     │ ─────────────────────────►│  UPSERT workspace_defaults  │
 │                      │                          │                     │ ─────────────────────────►│  UPSERT retention_policies x3│
 │                      │                          │                     │ ─────────────────────────►│  INSERT runtime_audit_log    │
 │                      │                          │                     │  COMMIT                   │                              │
 │                      │ ◄─────────────────────── │ ◄────────────────── │  return effective view    │                              │
 │                      │  toast "saved"           │                     │                            │                              │
 │                      │                          │                     │                            │                              │
 │  …later, a member presses ⌘N to start a chat                                                                                       │
 │                      │  POST /conversations     │                     │                            │                              │
 │                      │ ───────────────────────►│ ──────────────────► │  ConversationsService     │                              │
 │                      │                          │                     │  defaults = SELECT WS_DEF │                              │
 │                      │                          │                     │  if !req.connectors:       │                              │
 │                      │                          │                     │     enabled = defaults.dc │                              │
 │                      │                          │                     │ ─────────────────────────►│  INSERT agent_conversations │
 │                      │ ◄─────────────────────── │ ◄────────────────── │  return record            │                              │
 │                      │                          │                     │                            │                              │
 │                      │  member sends prompt → POST /runs                                                                           │
 │                      │ ───────────────────────►│ ──────────────────► │  RunService.create_run    │                              │
 │                      │                          │                     │  model = req.model        │                              │
 │                      │                          │                     │       ?? assistant.model  │                              │
 │                      │                          │                     │       ?? defaults.model   │   ◄── new fallback           │
 │                      │                          │                     │       ?? settings.default │                              │
 │                      │                          │                     │  freeze in runtime_context│                              │
 │                      │                          │                     │ ─────────────────────────►│  INSERT agent_runs           │
 │                      │                          │                     │                            │ ──────────────────────────►│ claim
 │                      │                          │                     │                            │                              │ build deep agent
 │                      │  SSE event stream (unchanged)                                                                              │
 │                      │ ◄────────────────────────────────────────────────────────────────────────────────────────────────────── │
```

### 3.9 Edge cases

| Case                                                                                        | Behaviour                                                                                                                                                               |
| ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Admin sets `default_connectors: { slack: ["read"] }` but the user has not OAuth'd Slack yet | Conversation is created with the override; `ToolPermissionChecker` filters Slack out at run-start (the connector card resolves `connected=false`). No error.            |
| Admin removes a model that's the default                                                    | `default_model.model_name` becomes invalid; `RunService.create_run` falls through to the next layer (`settings.default_model`). A warning is logged + audit-tagged.     |
| Member creates a conversation, admin then changes defaults                                  | Existing conversation keeps its row; only _new_ conversations inherit. ✅ design intent.                                                                                |
| Conversation is deleted while a run is streaming                                            | Run is cancelled (`run_cancelled` event), `deleted_at` is written. FE closes stream on `run_cancelled` (existing handler). One audit row each.                          |
| User restores a conversation whose retention TTL passed                                     | 404 — the sweeper already reaped dependent rows (or will imminently). FE shows "this chat could not be restored." Document in copy.                                     |
| Folder is renamed by editing one chat                                                       | Local-only — folders are user-personal labels, not entities. Other chats with the same string keep that string. We sort lexicographically; renames are purely cosmetic. |
| Two admins write defaults simultaneously                                                    | Last-write-wins; the second writer sees the first's effect on read-back. Audit captures both writes with `before/after`. No row-version column needed.                  |
| Cross-org PUT (header says different org than URL)                                          | Existing identity guard rejects with 403 before reaching `WorkspaceDefaultsService`.                                                                                    |
| `parent_conversation_id` populated by Wave 6 fork while user-PATCH happens                  | Independent fields; no conflict.                                                                                                                                        |

### 3.10 Test plan

Lives in the same PR. Minimum bar before merge.

**ai-backend (`services/ai-backend/tests/`)**

- `unit/runtime_api/workspace/test_get_defaults.py`
  - empty defaults → deployment-fallback shape
  - row present → that shape
  - foreign-org caller → 404 (does not leak existence)
- `unit/runtime_api/workspace/test_update_defaults.py`
  - happy path — defaults row + 3 retention rows + 1 audit row in single TX
  - non-admin → 403, no rows written
  - invalid model name → 422, no rows written
  - retention < 1 / > 3650 → 422
  - rollback on partial failure (forced via persistence-port stub)
- `unit/runtime_api/conversations/test_lifecycle.py`
  - PATCH folder/title/archive — independent, idempotent
  - DELETE — soft-deletes + cancels active run + audit
  - DELETE on conversation with no active run — soft-deletes, no cancel call
  - RESTORE — un-deletes; 404 after sweeper reap (simulated)
  - LIST `include_deleted=true` only returns soft-deleted
- `unit/runtime_api/services/test_create_conversation_defaults_fallback.py`
  - request omits connectors → conversation row uses defaults
  - request provides connectors → request wins (defaults ignored)
- `unit/runtime_api/services/test_create_run_model_fallback.py`
  - request → assistant → workspace_defaults → settings.default — one test per slot
- `integration/test_audit_emission_for_workspace_defaults.py` — verifies one row per call with `before/after/diff_keys` and the cross-reference `retention_policy_ids`.
- `integration/test_soft_delete_then_retention_sweep.py` — soft-delete, run sweeper with TTL=1s, assert row gone.

**Frontend (`apps/frontend/src/features/`)**

- `settings/sections/WorkspaceSettings.test.tsx` — admin can save defaults; non-admin sees read-only view.
- `chat/components/sidebar/groupConversations.test.ts` — Today/Yesterday/Earlier reducer with timezone-shifted fixtures.
- `chat/components/sidebar/AssistantThreadList.test.tsx` — soft-deleted rows hidden by default; `Show deleted` filter brings them back.
- `chat/hooks/useWorkspaceDefaults.test.tsx` — optimistic save, rollback on 4xx.

**Cross-service smoke (`make test`)**: one happy path through facade → ai-backend → DB for defaults + lifecycle.

### 3.11 Rollout

- **Flag-free.** New columns default to `NULL`; new table starts empty. Old runs continue to use the existing fallback chain (deployment defaults).
- **Zero-downtime migrations.** `ALTER TABLE … ADD COLUMN IF NOT EXISTS … TIMESTAMPTZ` (no default → no rewrite). New table is `CREATE TABLE IF NOT EXISTS`. New indexes are `CREATE INDEX IF NOT EXISTS`; production runbook addendum: run them via `CREATE INDEX CONCURRENTLY` (operator note, not in the SQL file).
- **Backout.** Drop the new table + new columns + new indexes; the API returns deployment fallback again. Audit rows tied to the dropped actions remain (chain stays intact).
- **Forward compatibility for Wave 6.** `parent_conversation_id` is nullable + unindexed today; Wave 6 adds the FK self-reference + a `(parent_conversation_id)` index in its own migration.

### 3.12 Open questions

1. **Eager trash window.** Do we want soft-deleted conversations to live in a "Trash" view for a fixed N days _separate_ from retention? v1 says no — soft-delete is just "retention-eligible from now"; the user gets to restore as long as the retention TTL hasn't elapsed. Revisit if churn complaints surface.
2. **Per-user retention overrides through the UI.** The table supports `scope='user'`; we surface `scope='org'` only in this PR's Settings panel. Per-user overrides land when a workspace asks (no design surface yet).
3. **Folder ACL / project model.** Out of scope per Design Doc § "Future explorations · Workspaces ↔ projects" (v0.6 bookmark).

---

## 4 · Acceptance checklist

- [ ] Migration `0017_workspace_defaults.sql` applies cleanly forward and rolls back via the matching `.rollback.sql`.
- [ ] Migration `0018_conversation_lifecycle.sql` applies cleanly forward and rolls back; existing `idx_agent_conversations_org_user_updated` is preserved.
- [ ] `WorkspaceDefaultsService.get()` materialises deployment fallback when no row exists.
- [ ] `WorkspaceDefaultsService.update()` writes the defaults row + three retention policies + one audit row in a single transaction; rollback is exercised by a port-level fault test.
- [ ] `ConversationsService.create_conversation()` consumes `default_connectors` only when the request omits them.
- [ ] `RunService.create_run()` model-resolution chain extended to consult `workspace_defaults` between assistant and deployment defaults; existing model-resolution tests stay green.
- [ ] `ConversationsService.delete_conversation()` cancels the active run via existing `cancel_run` before writing `deleted_at`.
- [ ] `GET /v1/agent/conversations` filters out `deleted_at IS NOT NULL` by default; `?include_deleted=true` returns them.
- [ ] One audit row per `workspace.defaults.update`, `conversation.delete`, `conversation.restore`, `conversation.update`. Chain verifier passes.
- [ ] No new event types in `runtime_api/schemas/events.py`. `RuntimeEventEnvelope` Pydantic schema is byte-identical pre/post merge.
- [ ] `backend-facade` exposes `GET/PUT /v1/agent/workspace/defaults` and `PATCH/DELETE /v1/agent/conversations/{id}` and `POST /v1/agent/conversations/{id}/restore`. None reach `/internal/v1/*`.
- [ ] `@0x-copilot/api-types` exports `WorkspaceDefaults` + `UpdateConversationRequest`; existing `Conversation` gains optional `folder`, `deleted_at`, `parent_conversation_id`.
- [ ] `useWorkspaceDefaults()` hook + `groupConversations.ts` reducer ship with tests.
- [ ] `make test` green; targeted ai-backend pytest suite green; frontend typecheck + build green.

---

## 5 · References

- [FastAPI · Body — Updates (PATCH semantics)](https://fastapi.tiangolo.com/tutorial/body-updates/) — the `model_dump(exclude_unset=True)` pattern reused from PR 1.2.
- RFC 7396 — JSON Merge Patch (semantics adopted, no library).
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — stays unchanged; this PR is a non-event.
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md) — facade-only ingress; ai-backend owns runtime defaults.
- [`docs/decomp/persistence/_index.md`](../decomp/persistence/_index.md) — persistence inventory.
- [`services/ai-backend/migrations/0012_retention_policies.sql`](../../services/ai-backend/migrations/0012_retention_policies.sql) — retention storage we reuse.
- [`services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`](../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py) — sweeper we reuse.
- [`services/ai-backend/src/runtime_api/http/retention_routes.py`](../../services/ai-backend/src/runtime_api/http/retention_routes.py) — admin CRUD we compose into `update_workspace_defaults`.
- [`services/ai-backend/src/runtime_api/schemas/conversations.py`](../../services/ai-backend/src/runtime_api/schemas/conversations.py) `ConnectorScopeValidator` — shape validator reused for `default_connectors`.
- [`services/ai-backend/src/agent_runtime/execution/models.py`](../../services/ai-backend/src/agent_runtime/execution/models.py) — model-resolution chain we extend by one slot.
- [`services/backend/migrations/0004_identity_foundation.sql`](../../services/backend/migrations/0004_identity_foundation.sql) — `users.deleted_at` partial-index pattern reused for conversations.
- [`services/backend-facade/src/backend_facade/deployment_profile.py`](../../services/backend-facade/src/backend_facade/deployment_profile.py) `default_retention_days` — deployment-level fallback consulted when a tenant has no policy row.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — sibling PR; provides `ConnectorScopeValidator` and `enabled_connectors` pattern this PR composes.
- [`docs/new-design/pr-1.3-draft-artifact.md`](pr-1.3-draft-artifact.md) · [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) · [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — sibling PRs landing in parallel; this PR's "no new event family" guarantee keeps merges conflict-free.
