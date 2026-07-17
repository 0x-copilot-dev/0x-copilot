# PR 1.5 — Subagent discovery + Workspace pane data feeds

> **Status:** Spec · v1 · Owner: TBD · Target wave: W1 (unblocker for W3.2 Workspace pane right rail)
> **Size:** **S**. No new tables, no new event types, no new domain logic. Two `GET` endpoints + read port + adapters + FE feed hooks.
> **Scope:** `services/ai-backend` (read port + service + routes) · `services/backend-facade` (proxy) · `packages/api-types` (contracts) · `apps/frontend` (two hooks + workspace‑pane wiring)
> **Reads alongside:** [`docs/new-design/01-citations-live-registry.md`](./01-citations-live-registry.md), [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](./pr-1-2-per-chat-connector-scope.md), [`docs/new-design/pr-1.3-draft-artifact.md`](./pr-1.3-draft-artifact.md), [`docs/new-design/pr-1.4-two-stage-approvals.md`](./pr-1.4-two-stage-approvals.md), [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md), [`services/ai-backend/docs/specs/10-agent-runtime-persistence-spec.md`](../../services/ai-backend/docs/specs/10-agent-runtime-persistence-spec.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md).

---

## 0 · TL;DR

The Atlas Workspace pane has five tabs (Sources / Agents / Draft / Approvals / Skills). PR 1.1 / 1.3 / 1.4 / pre‑existing skills cover four of them; this PR closes the last hole — **how the pane gets its initial data when the user re‑opens an old chat or switches threads**, and how the **Agents tab** stays in sync with running subagents.

Live updates are already covered by the existing SSE stream: `SUBAGENT_STARTED` / `SUBAGENT_PROGRESS` / `SUBAGENT_COMPLETED` events fire today out of [`runtime_worker/stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py:218) and persist to `runtime_events`. The historical archive (Sarah opens a chat from yesterday — what subagents ran, what they returned?) is not currently exposed over HTTP.

**Two new read endpoints, both backed by tables that already exist** (created in migration `0001_initial_runtime_persistence.sql`):

```
GET /v1/agent/conversations/{cid}/subagents?status=running|recent&limit=50
GET /v1/agent/conversations/{cid}/sources?run_id=...&limit=200
```

That's the entire backend surface. **No migration. No new events. No new tables. No new tools.** Tools already write to `runtime_async_tasks` + `runtime_subagent_results`; PR 1.1 already writes to `runtime_citations`. We are filling in two missing read ports.

LoC estimate (excluding tests): ai‑backend ≈ 250 · facade ≈ 40 · api‑types ≈ 30 · frontend ≈ 180. **Net new ≈ 500 LoC** plus tests.

---

## 1 · PRD

### 1.1 Problem

The right‑rail Workspace pane is the second pillar of the Atlas surface (chat is the first). Per the Design Doc:

> _"Tabbed, collapsible. Default tab depends on what the active message contains."_
> _Sources (N) — ranked list of cited docs/messages._
> _Agents (N) — running and recent subagents with progress._

Today:

- **Live**: while a run streams, the FE projects events through [`chatModel/eventReducer.ts`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts) which already understands `SUBAGENT_STARTED/PROGRESS/COMPLETED`. The chat thread renders subagents inline ([`SubagentTool.tsx`](../../apps/frontend/src/features/chat/components/tools/SubagentTool.tsx)). All good.
- **Archive read**: there is no HTTP endpoint that returns "for conversation `X`, what subagents have run, with their objective, status, and result?". Switching to a past conversation today would require replaying every run's events to rebuild the subagent state — that's `O(events)` work for `O(subagents)` data.
- **Sources tab**: PR 1.1 lands `runtime_citations` and the `source_ingested` event. It also needs an archive‑read endpoint when the Sources tab opens for a conversation that is not currently streaming.

The Workspace pane needs **a single round‑trip per tab on open**, then incremental updates from the live event stream. This PR adds the two missing GETs.

A sibling concern: **when does the pane auto‑open?** The user already decided "auto‑open when there are sources or agents" (plan W0 decision). That trigger requires the FE to know subagent count > 0 / source count > 0 _before_ a run begins streaming. Both endpoints support a `?status=recent` query so the pane host can preflight the count on conversation open.

### 1.2 Goals

1. **Backed by existing data, not new tables.** `runtime_async_tasks` + `runtime_subagent_results` (migration 0001) already record everything the Agents tab needs. `runtime_citations` (PR 1.1) covers Sources. We are the read‑side projection.
2. **Single round‑trip per tab on open.** The `subagents` route returns the latest status + objective + (optional) result for every subagent ever dispatched in the conversation, not per‑run. The `sources` route returns the ranked unique source list for the conversation (or, with `?run_id=…`, scoped to one run).
3. **Live updates via the existing SSE pipeline, no extra fetch.** Hook layer subscribes to the conversation's run events and projects them through pure reducers. The two GETs are idempotent seed reads.
4. **No new event types.** All live data already fires through `SUBAGENT_STARTED/PROGRESS/COMPLETED` and (PR 1.1) `source_ingested` / `final_response.citations[]`. Frontend reducer projection is the integration point — we touch it minimally.
5. **No bypassing tenant isolation.** Every query goes through the existing `org_id` GUC and RLS policies (migration 0008).
6. **DRY.** Reuse the existing `RuntimeApiService` shape, the existing `RuntimeServiceAuthenticator`, the existing facade `proxy_route` helper, the existing FE `useConnectors` / `useSkills` hook pattern, and (importantly) the existing in‑memory adapter test harness.

### 1.3 Non‑goals (this PR)

- **Pagination beyond `limit`.** v1 caps at 200 sources / 50 subagents per response. If a conversation has more than that, the older records are truncated; a future PR adds cursor pagination.
- **Cross‑conversation aggregation.** "All my running subagents" is a different surface (Tasks — a future feature). This endpoint is conversation‑scoped only.
- **Subagent kill/cancel from the workspace pane.** Cancel is a run‑scoped action via the existing `POST /v1/agent/runs/{id}/cancel`. The pane shows status; it does not introduce a per‑subagent cancel button in v1.
- **Source ranking heuristic.** v1 ranks by citation chip count (most‑referenced first), then freshness. Smarter ranking (BM25, recency decay) is a follow‑up.
- **Sources tab live‑update wire.** PR 1.1 owns the `source_ingested` event family. This PR depends on PR 1.1 having landed; the GET returns whatever PR 1.1 has persisted.
- **Subagent‑internal tool listing.** The Agents tab card shows objective + status + result summary + duration + token usage. The full step‑by‑step timeline lives inside the chat thread (`SubagentTool` already renders it); the workspace pane links there. Designing a duplicate timeline in the pane is out of scope.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                                                         | Verified by                              |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| AC‑1  | `GET /v1/agent/conversations/{cid}/subagents` returns one entry per `task_id` for the conversation, ordered most‑recent‑first, joining `runtime_async_tasks` with the latest `runtime_subagent_results` row.                                                                                                                                                                      | Postgres adapter test                    |
| AC‑2  | The response payload includes (per subagent): `task_id`, `subagent_name`, `status`, `objective_summary`, `display_title` (short summary), `started_at`, `completed_at`, `duration_ms`, `parent_run_id`, `result_summary` (`response_text` truncated 280 chars), `token_usage` (rolled up from `runtime_model_call_usage`). All redacted/encrypted fields go through `FieldCodec`. | Service unit test                        |
| AC‑3  | `?status=running` returns only subagents in `queued`/`running` state. `?status=recent` returns the last 50 across all states. Default returns the last 50.                                                                                                                                                                                                                        | API contract test                        |
| AC‑4  | `GET /v1/agent/conversations/{cid}/sources` returns one row per unique `source_doc_id` cited in the conversation, with `citation_count`, freshest `freshness_at`, and the most‑recent `citation_id` (so chips can resolve). With `?run_id=…`, the result is scoped to that run.                                                                                                   | Postgres adapter test                    |
| AC‑5  | Cross‑org access (a request with `x‑enterprise‑org‑id` not equal to the conversation's `org_id`) returns `404`. RLS denies at SQL level even if the service layer is bypassed.                                                                                                                                                                                                    | Persistence + facade test                |
| AC‑6  | The frontend `useConversationSubagents(conversationId)` hook returns the seed data plus a live `Map<task_id, SubagentEntry>` that is updated by the existing event reducer when `SUBAGENT_*` events arrive on any active run for the conversation.                                                                                                                                | FE hook test with mocked SSE             |
| AC‑7  | Opening a conversation with ≥1 subagent or ≥1 source auto‑opens the workspace pane. Opening a conversation with 0 of each leaves the pane closed.                                                                                                                                                                                                                                 | FE component test                        |
| AC‑8  | A subagent whose run was cancelled mid‑flight (`status='cancelled'`) renders in the Agents tab with a grey dot and the result row reads "Cancelled · Xs".                                                                                                                                                                                                                         | FE snapshot                              |
| AC‑9  | Both endpoints are scope‑gated by `RUNTIME_USE` (the same scope as the rest of the runtime router). Identity is enforced by `RuntimeServiceAuthenticator`; no anonymous reads.                                                                                                                                                                                                    | RBAC test                                |
| AC‑10 | The two endpoints together cap p99 ≤ 80 ms against the local stack for a conversation with 50 subagents and 200 citations.                                                                                                                                                                                                                                                        | Bench in `tests/perf` (existing harness) |

### 1.5 Risks

| Risk                                                          | Mitigation                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hot path leaks PII via `objective_summary` / `response_text`. | Both columns are stored encrypted v1 (`runtime_async_tasks.objective_summary`, `runtime_subagent_results.response_text` — verified in [`encrypt_existing_columns.py`](../../services/ai-backend/src/runtime_worker/jobs/encrypt_existing_columns.py)). Read path goes through `FieldCodec.decrypt` at the service boundary. The DTO is `RuntimeContract`‑validated before serialization. |
| FE diverges between live event projection and post‑seed read. | Both code paths feed the same reducer projection function (`projectSubagentEvent` / `projectSourceEvent`). The seed read is just `event‑shape rows`, fed in via the same code path. Single source of truth.                                                                                                                                                                              |
| N+1 query on result join.                                     | One query per endpoint: a `LEFT JOIN LATERAL` for `subagents` (latest result per task), a `GROUP BY source_doc_id` aggregate for `sources`. Indexes already exist (`idx_runtime_subagent_results_task`, `idx_runtime_async_tasks_org_run_status`). PR 1.1 adds `idx_runtime_citations_conversation_doc` (already in its plan); we depend on that.                                        |
| Endpoint becomes a backdoor for unauthenticated event peek.   | RLS + scope check + `404` (not `403`) on missing/foreign resources. Identical posture to other runtime read routes.                                                                                                                                                                                                                                                                      |
| Subagent rows accumulate forever, slowing the query.          | Bounded by `LIMIT 50` and the conversation lifecycle (PR 1.6 adds soft‑delete + retention). Conversations are short‑lived in practice; index already covers (`org_id, run_id, status`).                                                                                                                                                                                                  |
| Live reducer drops events during reconnect.                   | Existing reconnect path (`?after_sequence=N`) replays missed events; reducer is pure and idempotent on subagent identity (`task_id`). No additional logic needed.                                                                                                                                                                                                                        |

### 1.6 Unit testing requirements

Per [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md):

- `tests/unit/agent_runtime/persistence/test_subagent_store.py` — read port contract (in‑memory). Latest‑result join, status filter, limit, deterministic ordering.
- `tests/unit/runtime_adapters/postgres/test_subagent_store_postgres.py` — postgres adapter, RLS, cross‑org refusal, encrypted column round‑trip.
- `tests/unit/agent_runtime/persistence/test_source_store.py` — `runtime_citations` aggregate read, ranking, run scoping.
- `tests/unit/runtime_api/services/test_workspace_feed_service.py` — service joins + DTO shape; truncation; token usage rollup.
- `tests/unit/runtime_api/http/test_workspace_routes.py` — route shape, identity propagation, 404, scope gate.
- `tests/unit/runtime_api/schemas/test_workspace_dtos.py` — pydantic IO validation.

Frontend:

- `apps/frontend/src/features/chat/utils/projectSubagentEvent.test.ts` — reducer projection of `SUBAGENT_*` over an in‑memory map. Pure; no DOM.
- `apps/frontend/src/features/chat/hooks/useConversationSubagents.test.tsx` — seed + live merge + reconnect.
- `apps/frontend/src/features/chat/components/workspace/WorkspacePaneAutoOpen.test.tsx` — auto‑open trigger semantics.

---

## 2 · Spec

### 2.1 Architecture

```
                       ┌─────────────────────────────────────────────────┐
                       │  AGENT (deepagents harness, in worker)          │
                       │   spawns task() → SUBAGENT_STARTED, …PROGRESS,  │
                       │   …COMPLETED  (via stream_subagents.py)         │
                       └──────────────────────┬──────────────────────────┘
                                              │
                ┌──────────────── EXISTING ───┴────────────────┐
                │                                               │
       persist  ▼                                               ▼  emit
   ┌────────────────────────┐                         ┌────────────────────┐
   │ runtime_async_tasks    │ ◄── join ──┐            │ runtime_events     │
   │ runtime_subagent_      │            │            │  (RLS, sequence_no)│
   │   results              │            │            └─────────┬──────────┘
   │ runtime_citations      │ ◄──┐       │                      │ SSE stream + replay
   │   (PR 1.1)             │    │       │                      ▼
   └────────────────────────┘    │       │            ┌────────────────────┐
              ▲                  │       │            │ FE eventReducer    │
              │ READ             │       │            │ (existing —        │
              │                  │       │            │  no new variants)  │
   ┌──────────┴──────────┐       │       │            └─────────┬──────────┘
   │ SubagentStorePort ★ │       │       │                      │
   │ SourceStorePort   ★ │       │       │      project(seed)   │  project(live)
   │  (read-only)       │        │       │                      │
   └──────────┬──────────┘       │       │                      ▼
              │                  │       │            ┌────────────────────┐
   ┌──────────▼──────────┐       │       │            │ Workspace pane     │
   │ WorkspaceFeed       │ ──────┘       │            │  AgentsTab         │
   │   Service ★         │               │            │  SourcesTab        │
   └──────────┬──────────┘               │            └────────────────────┘
              │                          │                      ▲
   ┌──────────▼──────────┐               │                      │
   │ HTTP routes ★       │               │                      │
   │  GET …/subagents    │ ──────────────┘  HTTP via            │
   │  GET …/sources      │                  facade /v1/agent/…  │
   └──────────┬──────────┘                                       │
              │                                                  │
   ┌──────────▼──────────┐                          ┌────────────┴─────────┐
   │ backend-facade      │ ───────── proxies ────► │  apps/frontend/src/   │
   │  proxy_route        │                          │   api/agentApi.ts    │
   └─────────────────────┘                          │   (listSubagents,    │
                                                    │    listSources)      │
                                                    └──────────────────────┘
```

The whole PR is the four ★ boxes. Everything else is reused.

### 2.2 Module boundaries

| Layer                                                                                                                       | Module                                                                                                    | Owns                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `agent_runtime/persistence/ports.py` (extend)                                                                               | `SubagentStorePort`, `SourceStorePort` (read‑only `Protocol`s)                                            | Storage contract; in‑memory + Postgres. Read‑only — writers are upstream (worker + PR 1.1).         |
| `runtime_adapters/in_memory/subagent_store.py` (new)                                                                        | `InMemorySubagentStore`                                                                                   | Deterministic ordering for tests; reads from the existing in‑memory event log + run records.        |
| `runtime_adapters/in_memory/source_store.py` (new)                                                                          | `InMemorySourceStore`                                                                                     | Reads from the in‑memory citation registry seeded by PR 1.1.                                        |
| `runtime_adapters/postgres/subagent_store.py` (new)                                                                         | `PostgresSubagentStore`                                                                                   | One SQL function: `latest_per_task_for_conversation`. Uses existing pool + RLS GUC.                 |
| `runtime_adapters/postgres/source_store.py` (new)                                                                           | `PostgresSourceStore`                                                                                     | One SQL function: `aggregate_for_conversation`. Reuses PR 1.1 indexes.                              |
| `agent_runtime/api/workspace_feed_service.py` (new)                                                                         | `WorkspaceFeedService`                                                                                    | Compose store reads + `FieldCodec` decrypt + DTO shaping + token‑usage rollup. _No business rules._ |
| `runtime_api/schemas/workspace.py` (new)                                                                                    | `SubagentEntry`, `SubagentListResponse`, `SourceEntry`, `SourceListResponse`, `SubagentStatusFilter` enum | HTTP IO contracts.                                                                                  |
| `runtime_api/http/workspace.py` (new)                                                                                       | `WorkspaceFeedRoutes.list_subagents / list_sources`                                                       | FastAPI router; thin shim like `DraftRoutes`.                                                       |
| `runtime_api/http/routes.py` (modify)                                                                                       | Mount the new router under `/v1/agent` (two `add_api_route` calls).                                       |                                                                                                     |
| `runtime_api/dependencies.py` (modify)                                                                                      | Wire `WorkspaceFeedService` into the FastAPI DI container.                                                |                                                                                                     |
| `services/backend-facade/src/backend_facade/routes/agent_proxy.py` (modify)                                                 | Add proxy entries for the two new paths.                                                                  |                                                                                                     |
| `packages/api-types/src/index.ts` (extend)                                                                                  | `SubagentEntry`, `SourceEntry`, `SubagentStatusFilter`, list response types.                              | Single source of truth for FE/BE contract.                                                          |
| `apps/frontend/src/api/agentApi.ts` (extend)                                                                                | `listSubagents(cid, …)`, `listSources(cid, …)` HTTP clients via facade.                                   |                                                                                                     |
| `apps/frontend/src/features/chat/hooks/useConversationSubagents.ts` (new)                                                   | seed‑+‑live hook returning `Map<task_id, SubagentEntry>`.                                                 |                                                                                                     |
| `apps/frontend/src/features/chat/hooks/useConversationSources.ts` (new)                                                     | seed‑+‑live hook returning ranked `SourceEntry[]`.                                                        |                                                                                                     |
| `apps/frontend/src/features/chat/utils/projectSubagentEvent.ts` (new)                                                       | Pure reducer projecting one `RuntimeEventEnvelope` over an immutable map.                                 |                                                                                                     |
| `apps/frontend/src/features/chat/utils/projectSourceEvent.ts` (new)                                                         | Pure reducer for `source_ingested`.                                                                       |                                                                                                     |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx` (new — UI in PR 3.2; this PR ships the data feed only) | Renders the map.                                                                                          | (Lives in W3.2; this PR's contract is what AgentsTab consumes.)                                     |
| `apps/frontend/src/features/chat/components/workspace/SourcesTab.tsx` (new — UI in PR 3.2)                                  | Renders the ranked sources.                                                                               | (Same.)                                                                                             |
| `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneAutoOpen.ts` (new)                                    | Returns `{shouldAutoOpen}` from the two seed counts.                                                      |                                                                                                     |

### 2.3 What we do NOT add

- **No migration.** `runtime_async_tasks`, `runtime_subagent_results`, `runtime_citations` (PR 1.1) cover everything.
- **No new event type.** Existing `SUBAGENT_STARTED/PROGRESS/COMPLETED` and (PR 1.1) `source_ingested` carry every live update we need.
- **No new tool.** Subagents are spawned via the existing deepagents `task` tool; citations are emitted by existing read tools (PR 1.1).
- **No new auth scope.** Reuse `RUNTIME_USE`.
- **No new dep.** No external library required. (We considered `fastapi-pagination` for cursor pagination — deferred per non‑goal.)

### 2.4 Pydantic / TS contracts

#### 2.4.1 `SubagentEntry`

```python
# services/ai-backend/src/runtime_api/schemas/workspace.py
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt


class SubagentStatusFilter(str, Enum):
    """Coarse filter aligned with what the UI offers in the Agents tab."""

    ALL = "all"          # default — last 50 across all states
    RUNNING = "running"  # queued | running
    RECENT = "recent"    # last 50 across all states (alias of ALL kept for clarity)


class SubagentLifecycleStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SubagentTokenUsage(BaseModel):
    """Mirrors AssistantRunMetrics.subagent_rollup shape; reuse, do not redefine."""

    call_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt = 0


class SubagentEntry(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str | None
    subagent_name: str = Field(min_length=1, max_length=64)
    status: SubagentLifecycleStatus
    display_title: str | None = Field(default=None, max_length=160)
    objective_summary: str = Field(max_length=4096)            # decrypted from runtime_async_tasks
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: NonNegativeInt | None
    result_summary: str | None = Field(default=None, max_length=280)  # truncated response_text
    token_usage: SubagentTokenUsage | None
    safe_error_code: str | None
    safe_error_message: str | None


class SubagentListResponse(BaseModel):
    conversation_id: str
    subagents: tuple[SubagentEntry, ...]
    truncated: bool = False  # true iff hit the `limit` cap


class SourceEntry(BaseModel):
    citation_id: str          # latest citation_id for this doc (resolves chip → row)
    source_connector: str
    source_doc_id: str
    source_url: str | None
    title: str | None         # decrypted from runtime_citations
    snippet: str | None       # decrypted, truncated 280 chars
    freshness_at: datetime | None
    citation_count: PositiveInt
    last_cited_at: datetime


class SourceListResponse(BaseModel):
    conversation_id: str
    run_id: str | None
    sources: tuple[SourceEntry, ...]
    truncated: bool = False
```

#### 2.4.2 TypeScript mirror

```ts
// packages/api-types/src/index.ts (extend)
export type SubagentLifecycleStatus =
  | "queued"
  | "running"
  | "completed"
  | "cancelled"
  | "failed"
  | "timed_out";

export type SubagentStatusFilter = "all" | "running" | "recent";

export interface SubagentTokenUsage {
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
}

export interface SubagentEntry {
  task_id: string;
  parent_run_id: string | null;
  subagent_name: string;
  status: SubagentLifecycleStatus;
  display_title: string | null;
  objective_summary: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  result_summary: string | null;
  token_usage: SubagentTokenUsage | null;
  safe_error_code: string | null;
  safe_error_message: string | null;
}

export interface SubagentListResponse {
  conversation_id: string;
  subagents: SubagentEntry[];
  truncated: boolean;
}

export interface SourceEntry {
  citation_id: string;
  source_connector: string;
  source_doc_id: string;
  source_url: string | null;
  title: string | null;
  snippet: string | null;
  freshness_at: string | null;
  citation_count: number;
  last_cited_at: string;
}

export interface SourceListResponse {
  conversation_id: string;
  run_id: string | null;
  sources: SourceEntry[];
  truncated: boolean;
}
```

### 2.5 Read‑port `Protocol`s

```python
# services/ai-backend/src/agent_runtime/persistence/ports.py (extend)
from typing import Protocol


class SubagentStorePort(Protocol):
    async def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        status_filter: SubagentStatusFilter,
        limit: int,
    ) -> tuple[tuple[AsyncTaskRecord, SubagentResultRecord | None], ...]: ...


class SourceStorePort(Protocol):
    async def aggregate_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> tuple[CitationAggregateRow, ...]: ...
```

`CitationAggregateRow` is a small `RuntimeContract` subclass mirroring `runtime_citations` columns plus `citation_count` and `last_cited_at`. It lives in [`agent_runtime/persistence/records/citations.py`](../../services/ai-backend/src/agent_runtime/persistence/records/citations.py) — shared with PR 1.1, not duplicated here.

### 2.6 Postgres queries

**Subagents** — one statement, one round‑trip. RLS supplies `org_id` filtering via the session GUC `app.org_id` (set by `RuntimeServiceAuthenticator`).

```sql
-- list_for_conversation: latest result per task for a conversation
WITH tasks AS (
  SELECT t.*
  FROM runtime_async_tasks t
  WHERE t.conversation_id = $1
    AND ($2::text IS NULL OR t.status = ANY($2::text[]))
  ORDER BY COALESCE(t.completed_at, t.updated_at, t.started_at) DESC
  LIMIT $3
)
SELECT
  tasks.*,
  res.response_text,
  res.execution_summary,
  res.created_at AS result_created_at,
  res.error_json
FROM tasks
LEFT JOIN LATERAL (
  SELECT *
  FROM runtime_subagent_results r
  WHERE r.task_id = tasks.id
  ORDER BY r.created_at DESC
  LIMIT 1
) res ON TRUE;
```

`$2` is `NULL` for the default + `recent`, `ARRAY['queued','running']` for `running`. The two indexes that already exist cover this:

- `idx_runtime_async_tasks_org_run_status (org_id, run_id, status)` filters via RLS.
- `idx_runtime_subagent_results_task (task_id) UNIQUE` makes the LATERAL join an index lookup.

**Sources** — one statement against the table PR 1.1 ships:

```sql
SELECT
  source_connector,
  source_doc_id,
  MAX(source_url)        AS source_url,
  MAX(title)             AS title,        -- decrypted at the boundary
  MAX(snippet)           AS snippet,
  MAX(freshness_at)      AS freshness_at,
  COUNT(*)::int          AS citation_count,
  MAX(created_at)        AS last_cited_at,
  (ARRAY_AGG(citation_id ORDER BY created_at DESC))[1] AS citation_id
FROM runtime_citations
WHERE conversation_id = $1
  AND ($2::text IS NULL OR run_id = $2)
GROUP BY source_connector, source_doc_id
ORDER BY citation_count DESC, last_cited_at DESC
LIMIT $3;
```

PR 1.1 already plans `idx_runtime_citations_conversation_doc (org_id, conversation_id, source_connector, source_doc_id)` — this PR depends on that index existing. If PR 1.1 lands without it, this PR adds a one‑line `CREATE INDEX IF NOT EXISTS` patch.

### 2.7 HTTP routes

```python
# runtime_api/http/workspace.py
from fastapi import APIRouter, Depends, Query, Request

from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
from copilot_service_contracts.scopes import RUNTIME_USE
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.rbac import RequireScopes
from runtime_api.schemas import (
    SourceListResponse,
    SubagentListResponse,
    SubagentStatusFilter,
)


class WorkspaceFeedRoutes:

    @classmethod
    async def list_subagents(
        cls,
        request: Request,
        conversation_id: str,
        status: SubagentStatusFilter = Query(SubagentStatusFilter.ALL),
        limit: int = Query(50, ge=1, le=200),
    ) -> SubagentListResponse:
        org_id, _ = cls._scoped_identity(request)
        return await cls._service(request).list_subagents(
            org_id=org_id,
            conversation_id=conversation_id,
            status_filter=status,
            limit=limit,
        )

    @classmethod
    async def list_sources(
        cls,
        request: Request,
        conversation_id: str,
        run_id: str | None = Query(None, min_length=1, max_length=128),
        limit: int = Query(200, ge=1, le=500),
    ) -> SourceListResponse:
        org_id, _ = cls._scoped_identity(request)
        return await cls._service(request).list_sources(
            org_id=org_id,
            conversation_id=conversation_id,
            run_id=run_id,
            limit=limit,
        )

    @classmethod
    def attach(cls, router: APIRouter) -> None:
        router.add_api_route(
            "/conversations/{conversation_id}/subagents",
            cls.list_subagents,
            methods=["GET"],
            response_model=SubagentListResponse,
            dependencies=[Depends(RequireScopes((RUNTIME_USE,)))],
        )
        router.add_api_route(
            "/conversations/{conversation_id}/sources",
            cls.list_sources,
            methods=["GET"],
            response_model=SourceListResponse,
            dependencies=[Depends(RequireScopes((RUNTIME_USE,)))],
        )
```

The two `add_api_route` calls are added inside `RuntimeApiRoutes.create_router` next to the existing `connectors` route. Pattern is identical to [`DraftRoutes`](../../services/ai-backend/src/runtime_api/http/drafts.py:36).

### 2.8 Service

```python
# agent_runtime/api/workspace_feed_service.py
class WorkspaceFeedService:
    def __init__(
        self,
        *,
        subagent_store: SubagentStorePort,
        source_store: SourceStorePort,
        field_codec: FieldCodec,
        usage_rollup: UsageRollupReader | None = None,
    ) -> None:
        self._subagent_store = subagent_store
        self._source_store = source_store
        self._codec = field_codec
        self._rollup = usage_rollup  # optional — None during local dev with no metrics adapter

    async def list_subagents(self, *, org_id, conversation_id, status_filter, limit):
        rows = await self._subagent_store.list_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            status_filter=status_filter,
            limit=limit,
        )
        entries = tuple(self._project_subagent(task, result) for task, result in rows)
        return SubagentListResponse(
            conversation_id=conversation_id,
            subagents=entries,
            truncated=len(rows) >= limit,
        )

    def _project_subagent(self, task: AsyncTaskRecord, result: SubagentResultRecord | None) -> SubagentEntry:
        objective = self._codec.decrypt_text(task.objective_summary, task.encryption_version)
        response_text = (
            self._codec.decrypt_text(result.response_text, result.encryption_version)
            if result is not None and result.response_text is not None
            else None
        )
        return SubagentEntry(
            task_id=task.task_id,
            parent_run_id=task.run_id,
            subagent_name=task.subagent_name,
            status=SubagentLifecycleStatus(task.status.value),
            display_title=task.constraints.get("display_title") or _short(objective),
            objective_summary=objective,
            started_at=task.started_at,
            completed_at=task.completed_at,
            duration_ms=_duration_ms(task),
            result_summary=_truncate(response_text, 280),
            token_usage=self._rollup.subagent_rollup_for(task.task_id) if self._rollup else None,
            safe_error_code=task.safe_error_code,
            safe_error_message=task.safe_error_message,
        )
    # list_sources analogous
```

`_short`, `_truncate`, `_duration_ms` are existing helpers in `stream_subagents.StreamUpdateProcessor` — we extract the existing `truncate_task_summary` / `short_task_summary` into `agent_runtime/api/text_summary.py` and import from both places (DRY: same logic that built the `display_title` at write time builds it at read time when missing).

### 2.9 In‑memory adapter

The in‑memory `RuntimeApiStore` already maintains an `async_tasks: dict[str, AsyncTaskRecord]` and `subagent_results: dict[str, SubagentResultRecord]` (per [`runtime_adapters/in_memory/runtime_api_store.py`](../../services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py)). The new adapter is a thin wrapper around those:

```python
class InMemorySubagentStore(SubagentStorePort):
    def __init__(self, store: InMemoryRuntimeApiStore) -> None:
        self._store = store

    async def list_for_conversation(self, *, org_id, conversation_id, status_filter, limit):
        tasks = sorted(
            (t for t in self._store.async_tasks.values()
             if t.org_id == org_id and t.conversation_id == conversation_id
             and _status_filter_matches(status_filter, t.status)),
            key=lambda t: t.completed_at or t.updated_at or t.started_at or datetime.min,
            reverse=True,
        )[:limit]
        latest_result = lambda task_id: max(
            (r for r in self._store.subagent_results.values() if r.task_id == task_id),
            key=lambda r: r.created_at,
            default=None,
        )
        return tuple((t, latest_result(t.task_id)) for t in tasks)
```

No new persistence at all in dev mode; the worker already writes these records.

### 2.10 Frontend — feed hooks

Two hooks, one for each tab. Both follow the same shape: **seed once on conversation open, then merge live events through a pure reducer.**

```ts
// apps/frontend/src/features/chat/hooks/useConversationSubagents.ts
import { useEffect, useReducer } from "react";
import type {
  RuntimeEventEnvelope,
  SubagentEntry,
} from "@0x-copilot/api-types";

import { listSubagents } from "../../../api/agentApi";
import { projectSubagentEvent } from "../utils/projectSubagentEvent";

type State = {
  byTaskId: ReadonlyMap<string, SubagentEntry>;
  loading: boolean;
  error: string | null;
};

type Action =
  | { kind: "loading" }
  | { kind: "seed"; entries: SubagentEntry[] }
  | { kind: "live"; event: RuntimeEventEnvelope }
  | { kind: "error"; message: string };

function reducer(state: State, action: Action): State {
  switch (action.kind) {
    case "loading":
      return { ...state, loading: true, error: null };
    case "seed":
      return {
        loading: false,
        error: null,
        byTaskId: new Map(action.entries.map((e) => [e.task_id, e])),
      };
    case "live":
      return {
        ...state,
        byTaskId: projectSubagentEvent(state.byTaskId, action.event),
      };
    case "error":
      return { ...state, loading: false, error: action.message };
  }
}

export function useConversationSubagents(opts: {
  conversationId: string | null;
  identity: RequestIdentity;
  liveEvent: RuntimeEventEnvelope | null; // wired by ChatScreen — last received envelope
}): State & { entries: SubagentEntry[] } {
  const [state, dispatch] = useReducer(reducer, {
    byTaskId: new Map(),
    loading: false,
    error: null,
  });

  // seed
  useEffect(() => {
    if (opts.conversationId === null) return;
    let cancelled = false;
    dispatch({ kind: "loading" });
    listSubagents(opts.conversationId, opts.identity, {
      status: "recent",
      limit: 50,
    })
      .then(
        (response) =>
          !cancelled && dispatch({ kind: "seed", entries: response.subagents }),
      )
      .catch(
        (err) =>
          !cancelled && dispatch({ kind: "error", message: errorMessage(err) }),
      );
    return () => {
      cancelled = true;
    };
  }, [opts.conversationId, opts.identity]);

  // live merge
  useEffect(() => {
    if (opts.liveEvent === null) return;
    dispatch({ kind: "live", event: opts.liveEvent });
  }, [opts.liveEvent]);

  return {
    ...state,
    entries: Array.from(state.byTaskId.values()).sort(byRecency),
  };
}
```

`projectSubagentEvent` is the single point of truth for "given an envelope and a map, return the new map":

```ts
export function projectSubagentEvent(
  current: ReadonlyMap<string, SubagentEntry>,
  event: RuntimeEventEnvelope,
): Map<string, SubagentEntry> {
  if (!isSubagentEvent(event)) return new Map(current);
  const taskId = event.payload?.task_id;
  if (typeof taskId !== "string") return new Map(current);
  const next = new Map(current);
  const existing = next.get(taskId);
  next.set(taskId, mergeSubagent(existing, event));
  return next;
}
```

`mergeSubagent` walks the three event types (`SUBAGENT_STARTED` → seed entry with status=`running`, `SUBAGENT_PROGRESS` → update `display_title`, `SUBAGENT_COMPLETED` → set `status` + `result_summary` + `duration_ms` + `token_usage`). All shapes already exist in the projector at write time; we read the same fields the projector already writes.

`useConversationSources` is symmetric. Reducer is `projectSourceEvent` and only handles `source_ingested`.

### 2.11 Workspace pane auto‑open

```ts
// apps/frontend/src/features/chat/components/workspace/useWorkspacePaneAutoOpen.ts
export function useWorkspacePaneAutoOpen(opts: {
  subagents: SubagentEntry[];
  sources: SourceEntry[];
  draftCount: number;
  pendingApprovalsCount: number;
}): { shouldAutoOpen: boolean } {
  return {
    shouldAutoOpen:
      opts.subagents.length > 0 ||
      opts.sources.length > 0 ||
      opts.draftCount > 0 ||
      opts.pendingApprovalsCount > 0,
  };
}
```

`ChatScreen` consumes this and seeds `workspacePaneOpen` on conversation switch:

```ts
const { shouldAutoOpen } = useWorkspacePaneAutoOpen({
  subagents,
  sources,
  draftCount,
  pendingApprovalsCount,
});
useEffect(() => {
  setWorkspacePaneOpen(shouldAutoOpen);
}, [conversationId]); // NB: only on conversation switch, not on every event
```

Once the user manually toggles the pane, their preference wins — we do not auto‑close on subsequent events. That matches the design's "auto‑open when there are sources/agents" decision.

### 2.12 Edge cases

| Case                                                                                              | Behavior                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Conversation has zero subagents and zero sources.                                                 | Both endpoints return empty arrays. Pane stays closed.                                                                                                                                                                  |
| Subagent finished but result row not yet written (race).                                          | Entry returns with `status=completed` and `result_summary=null`. The `?after_sequence=N` reconnect will deliver the missing `SUBAGENT_COMPLETED` payload, the live reducer overwrites with the proper `result_summary`. |
| Subagent in‑flight at server crash.                                                               | `runtime_async_tasks.status` will reach `failed` via the worker's recovery path (existing). The tab shows the failed card.                                                                                              |
| Citation indexed but its source connector was disconnected since (e.g. user revoked Slack OAuth). | Source row still appears (we never prune historical citations). Sharing recipient view (W6) is the only place we redact.                                                                                                |
| Conversation deleted (PR 1.6).                                                                    | Soft‑delete sets `deleted_at`. The endpoints filter on `deleted_at IS NULL`; deleted conversations 404.                                                                                                                 |
| User in different org reads the conversation.                                                     | RLS filters at SQL level; service returns empty list; route returns `404` (via existing `RuntimeApiService.assert_conversation_visible` helper).                                                                        |
| Limit hit.                                                                                        | `truncated=true` in response. UI shows "Showing first 50". No pagination in v1.                                                                                                                                         |
| Cancellation mid‑stream.                                                                          | `status='cancelled'`; `duration_ms` computed from `cancelled_at - started_at`. Card renders with grey dot.                                                                                                              |

### 2.13 Security / RLS / encryption

- Both reads pass through `RuntimeServiceAuthenticator` (existing) which sets `app.org_id` on the connection. RLS on `runtime_async_tasks`, `runtime_subagent_results`, `runtime_citations` enforces tenant isolation at SQL.
- `objective_summary`, `response_text`, `title`, `snippet` are encrypted v1; the service decrypts at the boundary with `FieldCodec`. We do **not** return ciphertext.
- The strict‑reads gate (commit `31d08c6`) refuses any `encryption_version=0` row — historical rows backfilled before the gate are protected.
- The service trims `response_text` and `snippet` to 280 chars before returning. Full text is only available inline in the chat thread (where the user already has access).
- No PII in URL paths. `conversation_id` is opaque; `run_id` is opaque.
- Audit: read endpoints do not emit audit events (read‑only, identity‑bound, RLS‑filtered) — consistent with existing `GET /v1/agent/conversations/{id}` and `GET /v1/agent/conversations/{id}/messages`.

### 2.14 Observability

- One span per request via the existing `TraceContext` (see `agent_runtime/observability/tracing.py`). Tags: `endpoint`, `conversation_id_hash`, `org_id_hash`, `subagent_count` / `source_count`, `truncated`.
- One Prometheus histogram each: `workspace_feed_subagents_seconds`, `workspace_feed_sources_seconds` (registered alongside existing facade metrics in [`runtime_api/observability.py`](../../services/ai-backend/src/runtime_api/observability.py)).
- `pg_stat_statements` (commit `94e230e`) will surface the two new SQL queries; we verify post‑deploy they are within p99 budgets.

### 2.15 Tests

| File                                                                                     | Asserts                                                                                                                                                                  |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/unit/agent_runtime/persistence/test_subagent_store_inmemory.py`                   | seed → list → status filter → limit → truncated flag                                                                                                                     |
| `tests/unit/runtime_adapters/postgres/test_subagent_store_postgres.py`                   | RLS isolation, encrypted field round‑trip, latest‑result join correctness, status filter, no N+1 (assert one `pg_stat_statements` row)                                   |
| `tests/unit/agent_runtime/persistence/test_source_store_inmemory.py`                     | aggregate ranking (citation_count desc, last_cited_at desc), run scoping, limit                                                                                          |
| `tests/unit/runtime_adapters/postgres/test_source_store_postgres.py`                     | aggregate query, RLS, run scoping                                                                                                                                        |
| `tests/unit/agent_runtime/api/test_workspace_feed_service.py`                            | DTO shape, decryption, truncation, token‑usage rollup, error surface for missing FieldCodec key                                                                          |
| `tests/unit/runtime_api/http/test_workspace_routes.py`                                   | route shape (200 / 404 / 422 / 403), identity propagation, scope gate, query param validation                                                                            |
| `tests/integration/runtime_api/test_workspace_feed_e2e.py`                               | end‑to‑end against an in‑process worker: spawn 2 subagents + read 3 docs, then `GET …/subagents` and `GET …/sources` and assert payload matches the events that streamed |
| `apps/frontend/src/features/chat/utils/projectSubagentEvent.test.ts`                     | reducer correctness across all three lifecycle events; idempotency under replay; reorder safety                                                                          |
| `apps/frontend/src/features/chat/hooks/useConversationSubagents.test.tsx`                | seed + live merge + reconnect path                                                                                                                                       |
| `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneAutoOpen.test.tsx` | open when count>0; closed when zero; respect manual toggle                                                                                                               |
| `apps/frontend/src/api/agentApi.test.ts` (extend)                                        | `listSubagents` / `listSources` request shape, identity headers, error mapping                                                                                           |

---

## 3 · Why this is small

Quoting the user: _"check internet and use it. Be strict to DRY, writing elegant but simple architecture and as less code as needed."_

What we considered and rejected:

| Idea                                                                              | Why we said no                                                                                                                                                                             |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Separate `runtime_subagent_view` materialized view                                | Indexes already cover the access pattern (≤ 80 ms for our cardinality). One more thing to maintain.                                                                                        |
| Add `parent_run_id` column to `agent_conversations` for fast counts               | `runtime_async_tasks.run_id` already joins to runs, runs join to conversation. Counts are query‑time.                                                                                      |
| New event type `subagent_archive` for replay                                      | Confuses live vs. archive concerns. SSE replay (`?after_sequence=0`) already returns full history; the new GET is the **summary** projection, not a replay.                                |
| FastAPI‑pagination dependency                                                     | One extra dep to support a feature we are deferring.                                                                                                                                       |
| GraphQL endpoint to bundle subagents+sources+drafts+approvals into one round‑trip | Tempting, but every other RuntimeApi endpoint is REST; introducing GraphQL for two reads breaks the convention and complicates the facade proxy. The two GETs are ~80 ms each in parallel. |
| Server‑sent‑event for archive (open a stream that yields rows then closes)        | Over‑engineered for ≤ 50 rows.                                                                                                                                                             |
| New "Activity" service that owns the projection across all five tabs              | Premature unification. Sources, Drafts, Approvals, Skills each have their own owner PR. The auto‑open hook is a single composition point in the FE; the backend stays per‑resource.        |

What we **reuse** from the existing codebase:

| Concern                 | Reused primitive                                                                                                                        | File                                                                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Identity / scope check  | `RuntimeServiceAuthenticator` + `RequireScopes`                                                                                         | [`runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py), [`runtime_api/rbac.py`](../../services/ai-backend/src/runtime_api/rbac.py) |
| Pydantic base           | `RuntimeContract` (existing)                                                                                                            | [`agent_runtime/execution/contracts.py`](../../services/ai-backend/src/agent_runtime/execution/contracts.py)                                           |
| Field encryption        | `FieldCodec`                                                                                                                            | [`agent_runtime/persistence/encryption.py`](../../services/ai-backend/src/agent_runtime/persistence/encryption.py)                                     |
| Postgres pool / RLS GUC | shared connection pool + `set_config('app.org_id', …)`                                                                                  | [`runtime_adapters/postgres/__init__.py`](../../services/ai-backend/src/runtime_adapters/postgres/__init__.py)                                         |
| Token‑usage rollup      | `AssistantRunMetrics.subagent_rollup`                                                                                                   | [`runtime_worker/run_metrics.py`](../../services/ai-backend/src/runtime_worker/run_metrics.py)                                                         |
| Short summary helpers   | `StreamUpdateProcessor.short_task_summary` (extracted to `agent_runtime/api/text_summary.py` to avoid a worker import in the API layer) | [`runtime_worker/stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py)                                               |
| Live event reducer      | existing `chatModel/eventReducer.ts` `SUBAGENT_*` cases                                                                                 | [`apps/frontend/src/features/chat/chatModel/eventReducer.ts`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts)                         |
| Facade proxy            | `proxy_route` helper                                                                                                                    | [`services/backend-facade/src/backend_facade/routes/agent_proxy.py`](../../services/backend-facade/src/backend_facade/routes/agent_proxy.py)           |
| FE HTTP helpers         | `fetchJson` + identity headers                                                                                                          | [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts)                                                                         |
| Hook style              | matches `useConnectors`, `useSkills`                                                                                                    | [`apps/frontend/src/features/connectors/useConnectors.ts`](../../apps/frontend/src/features/connectors/useConnectors.ts)                               |

---

## 4 · End‑to‑end walk‑through

Sarah re‑opens the **"FY26 Q1 launch announcement draft"** chat after lunch. The chat had — earlier today — dispatched two subagents (`competitive-frame`, `gtm-dates`) and read 6 docs across Notion / Drive / Slack. No run is currently active.

1. `ChatScreen.loadConversationById('t-launch')` resolves identity + history (existing).
2. `useConversationSubagents({ conversationId: 't-launch', … })` fires `GET /v1/agent/conversations/t-launch/subagents?status=recent&limit=50` via the facade. Response: 2 entries, `status='completed'`, with `objective_summary`, `result_summary`, `duration_ms`, `token_usage`.
3. `useConversationSources({ conversationId: 't-launch' })` fires `GET /v1/agent/conversations/t-launch/sources?limit=200` in parallel. Response: 6 entries, ordered by `citation_count` desc.
4. `useWorkspacePaneAutoOpen` returns `shouldAutoOpen=true` because subagents.length > 0. `ChatScreen` sets `workspacePaneOpen=true`.
5. `WorkspacePane` mounts with the `Sources` tab active (default tab logic from PR 3.2: prefer Sources when both have data).
6. `AgentsTab` renders the 2 cards from the seed map. `SourcesTab` renders the 6 cards.
7. Sarah clicks `Approve & continue` on a queued draft‑send approval (PR 1.4 ships this). The run resumes; `streamRunEvents` reconnects with `after_sequence=N` and delivers `SUBAGENT_STARTED` for a new `voice-review` subagent.
8. The reducer projects the event over the existing 2‑entry map; AgentsTab now shows 3 cards. No re‑fetch.
9. `voice-review` completes: `SUBAGENT_COMPLETED` arrives. The card's status flips to `completed`, `result_summary` populates, `duration_ms` set. AgentsTab re‑renders that single card.

No new tables. No new event types. No new tools. Two HTTP routes, two FE hooks, two pure reducers.

---

## 5 · Sequencing within Wave 1

```
PR 1.1 citations (lands first — owns runtime_citations table + source_ingested event)
   │
   ▼
PR 1.5 (this PR) — depends on PR 1.1 for runtime_citations + index
   ▲
   │
PR 1.2 / 1.3 / 1.4 — independent of this PR; can land before, after, or in parallel
```

PR 1.5 has **no migration**, so it can technically land before PR 1.1 by stubbing `SourceStorePort` with an empty in‑memory adapter. That is not recommended — it would force two PRs to ship the Sources tab. Land PR 1.1 first; PR 1.5 second; PR 3.2 (Workspace pane UI) consumes both.

---

## 6 · Open questions

1. **Token usage on the Agents tab — show always or only when > 0?** Lean: always show (call_count=0 is informative — "no model calls, just tools"). Confirm with design before W3.2.
2. **`status=recent` vs default behavior** — currently identical. Drop one of them or keep both for forwards‑compat? Lean: keep both; deprecate after we add cursor pagination.
3. **Empty‑state copy.** Today the FE strings live in `messages.tsx`; carry over the design's copy ("Subagents run here when Atlas dispatches parallel work."). UI PR (W3.2) owns the strings.

---

## 7 · Out of scope (explicitly)

- Workspace pane UI itself (W3.2 owns it).
- Sources tab live event projection (PR 1.1 owns the event; this PR consumes it).
- Drafts / Approvals / Skills tabs (PR 1.3 / 1.4 / pre‑existing; this PR's auto‑open hook composes their counts but does not own their feeds).
- Cursor pagination (deferred).
- Per‑subagent cancel UI (use run cancel; deferred).
- Cross‑conversation aggregation ("My running tasks" surface; deferred).
- Smarter source ranking (deferred).
