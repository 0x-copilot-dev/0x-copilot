# PR 1.3 · Draft artifact

PRD + Spec + Architecture for the Atlas "Draft" workspace‑pane artifact and the agent‑harness wiring behind it.

> Sibling docs (in this folder): `pr-1.1-citations.md` (live citation registry), `pr-1.2-per-chat-connectors.md` (`agent_conversations.enabled_connectors`). This doc references those contracts but does not duplicate them.

> Style follows [`services/ai-backend/docs/CLAUDE.md`](../../services/ai-backend/docs/CLAUDE.md): PRD must state Problem / Goals / Non‑goals / Acceptance criteria / Risks / Unit testing requirements. Spec must include Architecture / Module boundaries / Pydantic contracts / Edge cases / Security / Observability / Tests.

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc ([Main app → Workspace pane → "Draft"](../../enterprise-search-design-doc.html), [Flow — Launch step 4](#)) requires a first‑class **draft artifact**: when the agent produces a writable output (announcement, doc, message body, ticket fields), it must appear in the right‑rail Draft tab as an editable, citation‑bearing artifact, with a `Send to {connector}` button that routes through the existing approval gate. The artifact must update _live_ during streaming, persist across run boundaries, and follow the same RLS / encryption / audit invariants as other tenant content.

We have nothing today: no `/drafts` table, no `DRAFT_UPDATED` event, no FE Draft tab, no `produce_draft` semantics. But we _do_ have the right primitives — deepagents already ships `FilesystemMiddleware` with `write_file` / `edit_file` / `read_file` / `ls` / `glob` / `grep`, the runtime worker already projects tool calls into runtime events, and `CompositeBackend` already routes path prefixes to per‑prefix backends (we use it today for `/subagents/`).

The right architecture is therefore **not a new tool**. A draft is a **file at `/drafts/{draft_id}.md`** routed by `CompositeBackend` to a thin `DraftBackend` adapter that persists to Postgres and emits a `DRAFT_UPDATED` runtime event on every successful write. The agent already knows how to use `write_file` and `edit_file`; we get streaming, undo via re‑edit, multi‑subagent collaboration, and `read_file`‑based reflection for free.

### 1.2 Goals

1. The agent can produce and revise a draft using the **existing deepagents file tools**, no new tool surface.
2. The Workspace pane's Draft tab populates **live** as the agent writes, via the existing SSE pipeline and `RuntimeEventEnvelope` contract.
3. A draft is **persisted** durably (encrypted at rest), versioned (every `awrite`/`aedit` produces a new version row), and **scoped** to `(org_id, conversation_id, user_id)` with RLS.
4. The user can **edit‑in‑place** in the Draft tab and trigger `Send to {connector}` — a server‑side action that funnels through the **existing approval primitive** (`runtime_approval_requests`) and emits identical audit events to other approval‑gated writes.
5. Drafts can carry **citation references** that share storage with PR 1.1's `runtime_citations`, so chips render identically inline in the chat thread and inside the Draft body.
6. Other agents (subagents) writing into `/drafts/` produce the same persisted artifact; the supervisor sees the same file.

### 1.3 Non‑goals

- A WYSIWYG rich‑text editor. Drafts are Markdown text; the FE renders via the existing Streamdown pipeline plus a `contenteditable="plaintext-only"` for edit mode.
- A general "files" UI surface. We only expose drafts in `/drafts/`. Memories, skills, subagent artifacts already have their own surfaces.
- Multi‑user collaborative editing (CRDT). Last‑writer‑wins with optimistic version locking; conflicts surface as a toast and the FE re‑fetches.
- Draft templating / library (a "Drafts I've used" workspace‑level catalog). v1 drafts are conversation‑scoped only.
- Per‑section approval. The whole draft is a single approval target. Section‑level edits are tracked as version history but are not separately approvable.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                              | Verified by                                     |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| AC‑1  | Agent calling `write_file("/drafts/{uuid}.md", body)` produces a persisted `runtime_drafts` row with `version=1`, encrypted `content_text` and `title`, and a `DRAFT_UPDATED` event with `sequence_no` strictly greater than the immediately preceding `tool_call` event.                                                              | Unit + integration test against `RuntimeWorker` |
| AC‑2  | `edit_file("/drafts/{uuid}.md", old, new)` produces a `version=N+1` row and a second `DRAFT_UPDATED` event. The previous row remains readable for history.                                                                                                                                                                             | Unit test on `DraftBackend`                     |
| AC‑3  | `GET /v1/agent/conversations/{cid}/drafts` returns the **latest version per `draft_id`** for that conversation.                                                                                                                                                                                                                        | API contract test                               |
| AC‑4  | `POST /v1/agent/drafts/{id}/send` enqueues an outbound connector tool call (`slack_post_message`, `linear_create_issue`, etc.), gated by the existing approval primitive, and on resolved approval the draft transitions to `status=sent` with audit chain entries `draft.send.proposed → draft.send.approved → draft.send.completed`. | E2E test with stubbed connector                 |
| AC‑5  | A subagent writing to `/drafts/{uuid}.md` (in its own LangGraph namespace) produces the same artifact rows; the supervisor's `read_file` returns identical content.                                                                                                                                                                    | Multi‑subagent integration test                 |
| AC‑6  | Cross‑org access (a request with `x‑enterprise‑org‑id` not equal to the draft's `org_id`) returns `404` (not `403` — same shape as other tenant data). RLS policy on `runtime_drafts` blocks at SQL level.                                                                                                                             | Persistence + facade test                       |
| AC‑7  | All draft fields that can hold user content (`title`, `content_text`, `target_metadata`) are stored encrypted v1 (`FieldCodec`); strict‑reads gate refuses any `encryption_version=0` row.                                                                                                                                             | Encryption round‑trip test                      |
| AC‑8  | The frontend's `DraftTab` updates within ≤ 250ms of a `DRAFT_UPDATED` event arriving on the run's SSE stream, using the existing `chatModel/eventReducer.ts` projection (no extra fetch).                                                                                                                                              | FE component test with mocked SSE               |
| AC‑9  | If the `target_connector` is not authenticated, `POST /send` returns `409` with `error_code="connector_auth_required"` and a `mcp_server_id` hint; the FE renders the existing `McpDiscoveryCard`.                                                                                                                                     | API + FE test                                   |
| AC‑10 | Cancelling a run (`POST /v1/agent/runs/{id}/cancel`) does not corrupt drafts: writes that completed before the cancel signal are durably persisted; an in‑flight write that loses its run is rolled back at the SQL transaction boundary.                                                                                              | Cancel‑race test                                |

### 1.5 Risks

| Risk                                                                           | Mitigation                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agents abuse `/drafts/` to stash arbitrary scratch content, polluting the tab. | Path validation in `DraftBackend.awrite` requires `/drafts/[a-z0-9]{32}\.md` (i.e. UUID‑hex). Non‑UUID paths return `invalid_path` and the file write fails — the model gets feedback through the deepagents tool result.                                                           |
| Two subagents racing on the same draft drop each other's edits.                | Optimistic locking via `version` column (existing `persistence/optimistic.py` pattern). Conflicting `aedit` returns a typed `OptimisticConflict` from `DraftBackend`; the harness reports it back to the model as a recoverable file error.                                         |
| Inflated event volume from per‑keystroke edits during streaming.               | The agent does not stream per‑token into files; `write_file` / `edit_file` are atomic. Each call emits exactly one `DRAFT_UPDATED`.                                                                                                                                                 |
| `Send to {connector}` becomes a back‑door around the approval system.          | The send endpoint **does not** call connector adapters directly. It posts a `RuntimeRunCommand` (kind=`draft_send`) on the existing queue port; the worker translates it into a tool call that goes through `capabilities/permissions` middleware exactly like an in‑run tool call. |
| Concurrent reader during a write returns torn content.                         | All `runtime_drafts` writes are single‑row inserts (append‑only), readers always select `MAX(version)`; readers see only fully‑committed rows.                                                                                                                                      |
| Encryption key rotation breaks history.                                        | Inherit existing `FieldCodec` rotation contract — old versions stay decryptable via `key_version`; new versions use current key.                                                                                                                                                    |
| Frontend drift between live event and post‑run replay.                         | `DraftTab` is a pure projection of the per‑conversation draft map maintained by `eventReducer.ts`; on conversation switch, the map is seeded from `GET /drafts`, then SSE events update it incrementally — same pattern used today for messages.                                    |

### 1.6 Unit testing requirements

Tests must live with the producing module under `services/ai-backend/tests/unit/...` and follow the test‑style rules in [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md):

- `tests/unit/agent_runtime/persistence/records/test_drafts.py`: `DraftRecord` validation (path, version, status enum), encryption round‑trip, optimistic conflict detection.
- `tests/unit/agent_runtime/capabilities/backends/test_draft_backend.py`: `DraftBackend.awrite` / `aedit` / `aread` happy paths, path validation, cross‑org refusal, event emission contract (event sequence + payload shape), composite routing (delegation to default backend for non‑`/drafts/` paths).
- `tests/unit/runtime_api/http/test_drafts_routes.py`: list / get / send / discard route shape, identity header propagation, 404/409 responses.
- `tests/unit/runtime_worker/handlers/test_draft_send.py`: queue‑command → approval‑gated tool call translation, status transitions, audit emission.
- `tests/unit/runtime_api/schemas/test_draft_event.py`: `DRAFT_UPDATED` envelope projection (presentation fields, redaction).

---

## 2 · Spec

### 2.1 Architecture

```
                                ┌────────────────────────────────────────┐
                                │  AGENT (deepagents harness, in worker) │
                                │   tools: write_file / edit_file /...    │
                                └────────────────┬───────────────────────┘
                                                 │ ToolRuntime call
                                                 ▼
                              ┌─────────────────────────────────────┐
                              │  FilesystemMiddleware (deepagents)  │
                              │   permission check → backend op     │
                              └────────────────┬────────────────────┘
                                               │ awrite/aedit/aread
                                               ▼
                              ┌─────────────────────────────────────┐
                              │   CompositeBackend                  │
                              │   routes:                           │
                              │     /subagents/  → SubagentArtifacts │
                              │     /drafts/    → DraftBackend ★    │
                              │     default     → StateBackend       │
                              └────────────────┬────────────────────┘
                                               │
                              ┌────────────────▼────────────────┐
                              │  DraftBackend (★ this PR)       │
                              │   persist → emit DRAFT_UPDATED  │
                              └────────────────┬────────────────┘
                          persist │             │ event append
                                  ▼             ▼
                  ┌──────────────────┐   ┌──────────────────────┐
                  │ runtime_drafts   │   │ runtime_events       │
                  │ (versioned, RLS) │   │ (existing, RLS)      │
                  └──────────────────┘   └──────────┬───────────┘
                                                    │ SSE / replay
                                                    ▼
                                          ┌──────────────────┐
                                          │  Frontend        │
                                          │  DraftTab        │
                                          └──────────────────┘
```

The lever: **one new `BackendProtocol` impl + one new event type + four HTTP routes + one FE tab**. Everything else is reused.

### 2.2 Module boundaries

| Layer                                                                                | New module                                                                                                    | Owns                                                                                                |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `agent_runtime/capabilities/backends/draft_backend.py` (new)                         | `DraftBackend(BackendProtocol)`                                                                               | Path validation, persistence dispatch, event emission. _No_ business rules about target connectors. |
| `agent_runtime/persistence/records/drafts.py` (new)                                  | `DraftRecord`, `DraftStatus` enum, `DraftWriteCommand`                                                        | Pydantic shape; never touches HTTP.                                                                 |
| `agent_runtime/persistence/ports.py` (extend)                                        | `DraftStorePort` (read/list/insert/upsert‑version)                                                            | Storage contract; in‑memory + Postgres adapters.                                                    |
| `runtime_adapters/in_memory/draft_store.py` (new)                                    | dev/test impl                                                                                                 | Deterministic ordering + version conflicts.                                                         |
| `runtime_adapters/postgres/draft_store.py` (new)                                     | prod impl                                                                                                     | Uses existing pool, RLS via session GUC `set_config('app.org_id',…)`.                               |
| `agent_runtime/execution/factory.py` (modify)                                        | `_composed_deep_backend`                                                                                      | Add `/drafts/` route.                                                                               |
| `runtime_api/schemas/events.py` (extend)                                             | `DraftUpdatedEvent` payload                                                                                   | API event projection (visibility=user).                                                             |
| `runtime_api/schemas/drafts.py` (new)                                                | `Draft`, `DraftSection`, `DraftSendRequest`, `DraftListResponse`                                              | HTTP IO contracts.                                                                                  |
| `runtime_api/http/drafts.py` (new)                                                   | FastAPI router for `/v1/agent/...drafts...`                                                                   | Authn header validation; delegate to `RuntimeApiService`.                                           |
| `runtime_api/services.py` (extend)                                                   | `RuntimeApiService.list_drafts / get_draft / send_draft / discard_draft`                                      | Call store + enqueue command.                                                                       |
| `runtime_worker/handlers/draft_send.py` (new)                                        | Translates `RuntimeRunCommand{kind=draft_send}` into a tool call invocation under the existing approval gate. | One small handler.                                                                                  |
| `runtime_worker/audit.py` (extend)                                                   | `draft.send.proposed / approved / completed / failed` audit emission.                                         |                                                                                                     |
| `services/backend-facade/src/...` (extend)                                           | proxy `/v1/agent/...drafts...` routes                                                                         | Identity header pass‑through.                                                                       |
| `packages/api-types/src/index.ts` (extend)                                           | `Draft`, `DraftSection`, `DraftSendRequest`, `DRAFT_UPDATED` event variant                                    | Single source of truth for FE/BE contract.                                                          |
| `apps/frontend/src/features/chat/components/workspace/DraftTab.tsx` (new, in PR 3.2) | UI tab                                                                                                        | Reducer projection + edit‑in‑place + send button.                                                   |
| `apps/frontend/src/features/chat/chatModel/eventReducer.ts` (extend)                 | Handle `DRAFT_UPDATED`                                                                                        | Pure function, deterministic.                                                                       |
| `apps/frontend/src/api/agentApi.ts` (extend)                                         | `listDrafts / sendDraft / discardDraft`                                                                       | HTTP clients via facade only.                                                                       |

### 2.3 Pydantic contracts

#### 2.3.1 `DraftStatus` enum

```python
class DraftStatus(str, Enum):
    DRAFT = "draft"
    SEND_PENDING_APPROVAL = "send_pending_approval"
    SENT = "sent"
    DISCARDED = "discarded"
    SEND_FAILED = "send_failed"
```

#### 2.3.2 `DraftRecord`

```python
class DraftRecord(RuntimeContract):
    """Persisted draft version. Append-only: each write inserts one row."""

    draft_id: str = Field(min_length=32, max_length=32)         # uuid hex
    version: PositiveInt                                         # monotonic per draft_id
    org_id: str
    conversation_id: str
    run_id: str | None                                           # null for user-edited versions
    user_id: str                                                 # creator (or last editor for edit-in-place)
    title: str = Field(max_length=240)                           # encrypted v1
    content_text: str                                            # encrypted v1; raw markdown
    target_connector: str | None = None                          # e.g. "slack" | "linear" | None
    target_metadata: JsonObject = Field(default_factory=dict)    # encrypted v1; e.g. {"channel":"#announcements"}
    citation_ids: tuple[str, ...] = ()                           # references runtime_citations.citation_id (PR 1.1)
    status: DraftStatus = DraftStatus.DRAFT
    encryption_version: NonNegativeInt = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("draft_id")
    @classmethod
    def _hex_uuid(cls, value: str) -> str:
        UUID(value)  # raises if not valid 32-hex
        return value
```

#### 2.3.3 `DraftStorePort`

```python
class DraftStorePort(Protocol):
    async def insert_version(self, record: DraftRecord) -> None: ...
    async def latest(self, *, org_id: str, draft_id: str) -> DraftRecord | None: ...
    async def latest_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> tuple[DraftRecord, ...]: ...
    async def get_version(self, *, org_id: str, draft_id: str, version: int) -> DraftRecord | None: ...
    async def update_status(
        self, *, org_id: str, draft_id: str, version: int,
        status: DraftStatus, expected_status: DraftStatus,
    ) -> DraftRecord: ...   # raises OptimisticConflict on mismatch
```

#### 2.3.4 HTTP IO

```python
class Draft(BaseModel):
    draft_id: str
    version: int
    conversation_id: str
    title: str
    content_text: str
    sections: tuple[DraftSection, ...]                  # parsed from content_text on read
    target_connector: str | None
    target_metadata: dict[str, Any] | None
    citation_ids: tuple[str, ...]
    status: DraftStatus
    updated_at: datetime

class DraftSection(BaseModel):
    heading: str
    body: str

class DraftListResponse(BaseModel):
    drafts: tuple[Draft, ...]

class DraftSendRequest(BaseModel):
    target_connector: str = Field(min_length=1, max_length=64)
    target_metadata: dict[str, Any] = Field(default_factory=dict)
    expected_version: int                                # optimistic guard

class DraftDiscardRequest(BaseModel):
    expected_version: int
```

#### 2.3.5 Runtime event payload (`DRAFT_UPDATED`)

Re‑use the existing `RuntimeEventEnvelope`. New `event_type = "draft_updated"`, `source = "draft_backend"`, visibility=`user`. Payload:

```python
class DraftUpdatedPayload(BaseModel):
    draft_id: str
    version: int
    status: DraftStatus
    title: str                          # plaintext only over the wire (post-decrypt at the boundary)
    sections: tuple[DraftSection, ...]
    target_connector: str | None
    target_metadata: dict[str, Any] | None
    citation_ids: tuple[str, ...]
    summary: str                        # display_title, e.g. "Draft v3: Aurora 4.0 announcement"
```

The projector populates `display_title=summary`, `activity_kind="draft"`, `status="ready"` (or `"running"` if mid‑edit_file streaming — see §2.5).

### 2.4 Storage

#### 2.4.1 Migration `0014_runtime_drafts.sql`

> Numbering placeholder. PR 1.1 may also use `0014_runtime_citations.sql`; we sequence at land time so the citation table lands first (drafts reference its `citation_id`).

```sql
CREATE TABLE runtime_drafts (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id        TEXT            NOT NULL,
    version         INTEGER         NOT NULL CHECK (version > 0),
    org_id          TEXT            NOT NULL,
    conversation_id TEXT            NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    run_id          TEXT            REFERENCES agent_runs(id) ON DELETE SET NULL,
    user_id         TEXT            NOT NULL,
    title           BYTEA           NOT NULL,                -- encrypted_v1(text)
    content_text    BYTEA           NOT NULL,                -- encrypted_v1(text)
    target_connector TEXT,
    target_metadata BYTEA,                                    -- encrypted_v1(jsonb) NULLable
    citation_ids    TEXT[]          NOT NULL DEFAULT '{}',
    status          TEXT            NOT NULL DEFAULT 'draft',
    encryption_version SMALLINT     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (org_id, draft_id, version)
);

CREATE INDEX runtime_drafts_conversation_idx
    ON runtime_drafts (org_id, conversation_id, draft_id, version DESC);

ALTER TABLE runtime_drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY runtime_drafts_tenant_isolation ON runtime_drafts
    USING (org_id = current_setting('app.org_id', true));
```

Append‑only is enforced by code (no UPDATE path); status transitions are modeled as new versions with the same `(draft_id)` and incremented `version`. This keeps the drift‑free, audit‑replayable invariant we already use for events. Discards are a final version with `status='discarded'`. Sent drafts are a final version with `status='sent'`.

### 2.5 Streaming behavior — how it flows through the harness

**Producer side (worker → events):**

1. The agent (or a subagent) emits a `write_file("/drafts/{uuid}.md", body)` tool call.
2. `FilesystemMiddleware` checks per‑agent permissions (we register `/drafts/` as writable). If the call passes, deepagents calls `CompositeBackend.awrite("/drafts/...")`.
3. `CompositeBackend` matches the `/drafts/` prefix and calls `DraftBackend.awrite(file_path, content)`.
4. `DraftBackend.awrite`:
   - validates the path (`/drafts/<32 hex>.md`) — else returns `WriteResult(error="invalid_path")`;
   - parses the body (Markdown) into a `tuple[DraftSection, ...]`, picks the first H1 as `title` (or "Untitled draft");
   - resolves `(org_id, conversation_id, run_id, user_id)` from the deepagents `ToolRuntime` (which carries our `AgentRuntimeContext`);
   - resolves the next `version = max(version)+1` from `DraftStorePort.latest(...)`;
   - inserts a `DraftRecord` via `DraftStorePort.insert_version` (encrypted via `FieldCodec`);
   - calls `RuntimeEventProducer.append("draft_updated", payload, run_id, conversation_id, org_id, visibility=USER)` with monotonic `sequence_no`;
   - returns `WriteResult(path="/drafts/...")`.
5. The harness's `stream_tools.py` already projects the `write_file` _tool_call_ event with `large_result_artifact_tool_names`/internal handling. We do **not** need a second tool projection — `DRAFT_UPDATED` is a separate, draft‑specific event that the FE listens to directly.
6. `RuntimeWorker` flushes the event before the next stream chunk, and SSE delivers it to the active client immediately.

**Edit path:** `edit_file(path, old, new)` works the same way through `DraftBackend.aedit`, which:

- reads the latest version via `DraftStorePort.latest`;
- performs the string substitution server‑side (so we don't trust the model's idea of the current content);
- inserts `version+1` and emits `DRAFT_UPDATED`.

**Read path:** `read_file(path)` returns the latest version's `content_text`. The model can therefore inspect the current draft, summarize it, or use it as input to a subagent — no extra wiring needed.

**Cancel race (AC‑10):** writes are inside a single SQL transaction inside `DraftBackend.awrite`. If the run is cancelled after the row is committed, the event is already durable (we persist before fanout, per the existing `09-runtime-events-producer-consumer-spec.md`). If cancellation lands during the write, the transaction either commits fully or rolls back fully — partial drafts are impossible.

### 2.6 Send flow — how the approval gate is reused

```
FE Send button
   │  POST /v1/agent/drafts/{id}/send  { target_connector, target_metadata, expected_version }
   ▼
backend-facade  ──► ai-backend RuntimeApiService.send_draft
   │                 1. load latest DraftRecord; check expected_version (409 OptimisticConflict)
   │                 2. verify (target_connector) is reachable via existing capability registry
   │                 3. if NOT authenticated → 409 connector_auth_required (return mcp_server_id)
   │                 4. insert version+1 status=send_pending_approval
   │                 5. emit DRAFT_UPDATED
   │                 6. emit audit `draft.send.proposed`
   │                 7. enqueue RuntimeRunCommand(kind=draft_send, payload={draft_id, version+1})
   ▼
worker handlers/draft_send.py
   │   resumes the conversation's most recent run *or* starts a synthetic "send" run
   │   that calls the connector tool (e.g. `slack_post_message`) with the draft body —
   │   permission middleware in capabilities/permissions enforces the approval gate
   │   exactly like any in-run tool call (no special path).
   ▼
existing approval primitive  (runtime_approval_requests + APPROVAL_REQUESTED event)
   │
   ├── approved   ─► tool runs ─► insert version+1 status=sent  ─► audit `draft.send.completed`
   └── rejected   ─► insert version+1 status=draft              ─► audit `draft.send.rejected`
```

**No new approval primitive. No new audit chain. No new wire concept.** We reuse `runtime_approval_requests`, `APPROVAL_REQUESTED` / `APPROVAL_RESOLVED`, and the existing facade audit endpoints. The send endpoint is just _cause_ (a new command) for an _effect_ (an approval‑gated tool call) that already works.

### 2.7 HTTP routes (added to `runtime_api/http/drafts.py`, proxied via facade)

| Verb  | Path                                            | Body                               | 200                                                 | Errors                                                                                 |
| ----- | ----------------------------------------------- | ---------------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------- |
| GET   | `/v1/agent/conversations/{cid}/drafts`          | —                                  | `DraftListResponse` (latest version per `draft_id`) | `404`                                                                                  |
| GET   | `/v1/agent/drafts/{id}` (`?version=N` optional) | —                                  | `Draft`                                             | `404`                                                                                  |
| POST  | `/v1/agent/drafts/{id}/send`                    | `DraftSendRequest`                 | `{run_id, approval_id}`                             | `404`, `409 optimistic_conflict`, `409 connector_auth_required {mcp_server_id}`, `403` |
| POST  | `/v1/agent/drafts/{id}/discard`                 | `DraftDiscardRequest`              | `Draft` (status=discarded)                          | `404`, `409 optimistic_conflict`                                                       |
| PATCH | `/v1/agent/drafts/{id}`                         | `{content_text, expected_version}` | `Draft` (new version, status=draft, run_id=null)    | `404`, `409 optimistic_conflict`                                                       |

`PATCH` is the **edit‑in‑place from the FE** path — the FE writes a new version directly without going through the agent. This is intentional: the user can mutate their own draft without spinning a run, and the UI stays authoritative. The new version's `run_id` is `null`; `user_id` is the editor.

All routes require the existing identity headers (`x-enterprise-org-id`, `x-enterprise-user-id`). All routes are proxied 1:1 by `backend-facade`.

### 2.8 Frontend integration (high level — full FE PR is 3.2)

- `chatModel/eventReducer.ts`: add `DRAFT_UPDATED` handler. Maintains a per‑conversation `Map<draft_id, Draft>` keyed by latest version. Pure function, deterministic.
- `DraftTab.tsx`: subscribes to the reducer slice. Renders Markdown via Streamdown. Edit mode: `contenteditable="plaintext-only"` over the rendered content; on blur or ⌘S, calls `PATCH /drafts/{id}` with `expected_version`.
- "Send to {connector}" button: opens a small popover with target‑connector picker + metadata field (channel, recipient, …). On send, calls `POST /drafts/{id}/send`. While the run is approval‑pending, the button collapses into a `Waiting on you · Open approval` link that scrolls to the inline `ApprovalTool` card in the thread (existing component).
- On conversation switch: seed via `GET /drafts`, then live updates take over.

### 2.9 Edge cases (do not silently drop these)

| Edge case                                                                   | Behavior                                                                                                                                                                                                             |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `write_file` to a non‑UUID `/drafts/foo.md`                                 | `WriteResult(error="invalid_path")`. Model gets the standard deepagents error string and can recover.                                                                                                                |
| `edit_file` on a non‑existent `/drafts/{uuid}.md`                           | `EditResult(error="file_not_found")`.                                                                                                                                                                                |
| `edit_file` whose `old_string` is not unique and `replace_all=false`        | `EditResult(error="ambiguous match")`.                                                                                                                                                                               |
| Two subagents both `awrite` the same `draft_id` near‑concurrently           | Each gets a unique version (single row insert). Order is determined by SQL serializability; `DRAFT_UPDATED` events arrive in insert order. The FE shows the highest version.                                         |
| The user's `PATCH` collides with an in‑run agent edit                       | The endpoint with the wrong `expected_version` gets `409`. UI re‑fetches. We accept last‑writer‑wins because there's no merge story in v1.                                                                           |
| Run is cancelled mid‑write                                                  | See §2.5 cancel race.                                                                                                                                                                                                |
| Run fails after a draft write committed                                     | The draft persists. The FE Draft tab shows the last persisted version; the failed‑run banner is independent.                                                                                                         |
| Recipient of a shared conversation views a draft (post PR 6.1)              | Read‑only render. Citation chips that the recipient lacks access to render as "Source restricted". `target_metadata` (e.g. "post to #announcements") is hidden unless the recipient has the connector authenticated. |
| Send to a connector the workspace admin has disabled                        | `403 connector_workspace_disabled`.                                                                                                                                                                                  |
| Encryption key rotation mid‑conversation                                    | Old versions stay readable via stored `key_version`; new versions use current key — same invariant as `agent_messages`.                                                                                              |
| User attempts to discard a draft that's already `sent`                      | `409 status_immutable`.                                                                                                                                                                                              |
| User discards a draft that's `send_pending_approval`                        | Allowed; we also auto‑reject the underlying approval (existing flow handles it). Audit chain: `draft.discard.discarded` + `approval.rejected.cascade`.                                                               |
| Subagent writes to `/drafts/` while supervisor's permission set excludes it | Rejected at `FilesystemMiddleware` permission check before reaching `DraftBackend`. The subagent harness profile lists `/drafts/` as writable only when explicitly delegated.                                        |

### 2.10 Security

- **RLS** on `runtime_drafts` (policy in §2.4.1). Every connection sets `app.org_id` per request — the existing pattern from migration `0008_rls_tenant_isolation.sql`.
- **Encryption v1** mandatory on `title`, `content_text`, `target_metadata`. Strict‑reads gate (commit `31d08c6`) refuses `encryption_version=0`. New rows write v1 only.
- **Identity propagation**: facade carries `x-enterprise-org-id`, `x-enterprise-user-id`, `x-enterprise-roles`. ai-backend treats those as untrusted unless they came from a verified facade session token.
- **Audit chain**: `draft.send.proposed`, `.approved`, `.rejected`, `.completed`, `.failed`, `draft.discard.discarded`, `draft.edit.user`, `draft.edit.agent` all written through the existing append‑only `runtime_audit_log` (with HMAC chain), so the SIEM exporter (commit `0016_siem_export.sql`) ships them automatically.
- **Capability exposure**: `DraftBackend` is registered through `_composed_deep_backend`. Subagent permission profiles get `/drafts/` only when their `SubagentDefinition` lists it — we do _not_ default‑allow it for arbitrary subagents.
- **No model‑written `target_connector`** without auth: the `send` endpoint validates server‑side that `target_connector` is in the user's authenticated capability set (existing `capabilities/permissions/registry.py`). Model output is treated as untrusted per [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) "Untrusted inputs" rule.

### 2.11 Observability

| Signal                                                                                           | Where                                                  | Notes                                                                                                                              |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| Runtime event `draft_updated`                                                                    | `runtime_events`, SSE                                  | One per write/edit. `payload.summary` is display‑safe.                                                                             |
| Audit chain entries                                                                              | `runtime_audit_log`                                    | `draft.send.{proposed,approved,rejected,completed,failed}`, `draft.discard.discarded`, `draft.edit.{user,agent}`. SIEM‑exportable. |
| Metric `runtime.draft.write_total{org_id_hash, status}`                                          | `observability/`                                       | Counter.                                                                                                                           |
| Metric `runtime.draft.send_outcome_total{outcome}`                                               | `observability/`                                       | `outcome ∈ {approved, rejected, completed, failed, conflict}`.                                                                     |
| Histogram `runtime.draft.size_bytes`                                                             | `observability/`                                       | Distribution of `len(content_text)` per write — early warning of runaway agent dumps.                                              |
| Log line on path validation failure                                                              | `agent_runtime/capabilities/backends/draft_backend.py` | `level=warning, agent_intent="write_file outside /drafts/{uuid}.md format"`. No PII.                                               |
| `pg_stat_statements` automatically catches the new queries (commit `94e230e`) — no extra wiring. |                                                        |                                                                                                                                    |

### 2.12 Tests

**Unit (mandatory):**

- `test_drafts.py` (records): valid record, encryption v1 round‑trip, version monotonicity, status enum coverage, draft_id uuid‑hex validator, `citation_ids` propagation.
- `test_draft_backend.py`: awrite happy path; awrite path‑validation failure (returns `invalid_path`); aedit happy path; aedit on missing file (returns `file_not_found`); aread happy path; cross‑org refusal (returns `permission_denied`); event emission ordering (envelope appended _before_ return); composite routing fallthrough (writes to `/memories/foo.md` go to default backend, not DraftBackend); concurrent writers — second `aedit` on stale `latest` raises `OptimisticConflict` from the store and the backend surfaces it as `EditResult(error=...)` so the model can retry.
- `test_drafts_routes.py`: list / get / patch / send / discard — happy + 404 + 409 (optimistic / connector‑auth / status‑immutable). Identity header missing → 401.
- `test_draft_send.py` (worker): queue‑command translation creates an approval‑gated tool call; status transitions on approval/rejection; audit emission count.
- `test_draft_event.py` (schemas): `DraftUpdatedEvent` projector populates `activity_kind="draft"`, `display_title` from `summary`, `visibility=user`, redaction never strips fields the FE needs.

**Integration:**

- E2E "agent writes a draft" (`tests/integration/test_draft_flow.py`):
  1. start a run with a prompt that triggers `write_file("/drafts/{uuid}.md", ...)`;
  2. assert `DRAFT_UPDATED` arrives on SSE with `version=1`;
  3. issue a follow‑up that triggers `edit_file`;
  4. assert `version=2` event;
  5. `GET /v1/agent/conversations/{cid}/drafts` returns one draft, `version=2`;
  6. `PATCH` the draft from a non‑agent client;
  7. assert `version=3` event with `run_id=null`.
- E2E send (`tests/integration/test_draft_send.py`):
  1. seed an authenticated `slack` connector;
  2. `POST /send` with target=`slack`, metadata=`{channel: "#test"}`;
  3. assert approval card appears in the run; resolve approved;
  4. assert `slack_post_message` was called (stub); status flows to `sent`; audit chain has all four entries.
- Negative‑path send (`tests/integration/test_draft_send_unauth.py`):
  1. target connector not authenticated → 409 with `error_code=connector_auth_required` and `mcp_server_id=…`;
  2. FE emits a McpDiscoveryCard; user authenticates; FE retries; success.
- Multi‑subagent (`tests/integration/test_draft_concurrent_subagents.py`):
  1. spawn two subagents both writing the same `/drafts/{uuid}.md`;
  2. assert both versions persist; latest version wins in `latest_for_conversation`.
- Cross‑org (`tests/integration/test_draft_isolation.py`):
  1. org A creates a draft;
  2. request from org B → `404`.

**Frontend (`apps/frontend/src/features/chat/components/workspace/DraftTab.test.tsx`):**

- Reducer correctly seeds map from `GET /drafts`.
- Reducer applies `DRAFT_UPDATED` events to overwrite higher‑version entries only.
- Edit‑in‑place: blur with content change → calls `PATCH` with `expected_version`. On `409`, re‑fetches and rebases; user sees a non‑modal banner "Draft was updated; your edit was discarded."
- Send button: 200 → status pill "Waiting on you"; 409 connector_auth_required → wires `McpDiscoveryCard` inline.

---

## 3 · Architecture decisions worth calling out

### 3.1 Why a deepagents `BackendProtocol` impl, not a custom tool

The Atlas design is "agent produces a draft." The shortest line between the agent and that artifact is `write_file`, which **already exists** in deepagents and already has streaming, permissions, and tool‑call projection. A bespoke `produce_draft` tool would:

- duplicate I/O semantics already covered by `write_file`/`edit_file`;
- re‑invent tool‑call permission middleware and event projection;
- require the model to learn another tool name when it already knows write_file from training.

Routing a path prefix through `CompositeBackend` is the documented deepagents extension point (the existing `/subagents/` routing is precedent). One small adapter, no new tool surface, full reuse.

### 3.2 Why event‑driven, not poll‑driven, for the FE Draft tab

The existing SSE pipeline already guarantees `sequence_no` ordering, replay on reconnect, and `after_sequence` resume. Bolting a poll loop onto the FE for drafts would create a second consistency model (poll vs. event) and introduce latency that violates AC‑8. The FE already runs the event reducer; one new case in the switch.

### 3.3 Why send goes through the runtime queue

If `POST /send` invoked the connector directly inside the HTTP handler, we'd bypass the harness — meaning the approval middleware, capability checks, audit emission, and tool‑call event projection would all need to be re‑implemented in the route. By posting a `RuntimeRunCommand`, the worker treats the send as a normal tool call, so all those primitives engage automatically. This is the single clearest DRY win in the design.

### 3.4 Why versioned append‑only, not in‑place mutation

Drafts are review artifacts. We need scrollback (the design doc's "scroll back a week later and see the decision exactly where it happened" rule applies to drafts too). Versioning costs one row per write — small relative to event volume — and gives us free history, free conflict detection (`expected_version`), free read‑repair on concurrent writes, and matches the immutability invariant on `runtime_events` and `runtime_audit_log`.

### 3.5 What is _not_ in this PR

- The Draft tab UI itself ships with **PR 3.2 (Workspace pane right rail)**. This PR ships the wire, the persistence, the routes, and the reducer case. PR 3.2 wires them visually.
- Citation chips inside the draft body share the registry from **PR 1.1**. If PR 1.1 hasn't landed, this PR ships with `citation_ids` modeled but the FE renders chips as plain `[c<id>]` until 1.1 lands.
- Per‑chat connector scope from **PR 1.2** affects which connectors the send picker exposes. If 1.2 hasn't landed, the picker shows the user's full authenticated set.
- Sharing recipient view (PR 6.1) renders drafts read‑only — that PR adds the source‑restricted citation rendering and connector‑gated `target_metadata` hiding.

---

## 4 · Verification plan

End‑to‑end walkthrough on `make dev` after the PR lands:

1. `make dev` (frontend on :5173, ai-backend on :8000, facade on :8200).
2. Open http://localhost:5173, start a chat, prompt "Draft the FY26 Q1 launch announcement using the approved positioning".
3. Watch SSE devtools: `tool_call` for `write_file`, then `draft_updated` immediately after, with `version=1`.
4. Open Workspace pane → Draft tab. Verify content matches the model output. Inline citation chips render (assuming PR 1.1 landed).
5. Edit a sentence inline, blur. Network tab shows `PATCH /drafts/{id}` with `expected_version=1`; response is `Draft` with `version=2, run_id=null`. SSE emits `draft_updated v=2`.
6. Click `Send to Slack`, target `#announcements`. Network tab: `POST /send` returns `{run_id, approval_id}`. The thread inserts an approval card. Click Approve.
7. Watch the worker log: `slack_post_message` invoked through approval gate. Audit chain entries appear in `runtime_audit_log` (verify with `psql`: `select action from runtime_audit_log where action like 'draft.%' order by id desc limit 8;`).
8. SSE final `draft_updated` emits `version=3, status=sent`. Draft tab pill flips to "Sent · 10:42". Approvals tab logs the decision.
9. Cross‑tenant check: replay step 7 with a different `x-enterprise-org-id` → `404`.
10. Cancel‑race check: prompt the agent again, immediately `POST /cancel` while the model is writing. Verify either both the draft row and `draft_updated` event committed, or neither — never half.

If all ten pass, AC‑1 through AC‑10 are observably satisfied.
