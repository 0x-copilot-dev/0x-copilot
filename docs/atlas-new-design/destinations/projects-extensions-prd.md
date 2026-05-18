# Projects Extensions — Sub-PRD (Phase 6.5)

**Status:** draft (2026-05-18)
**Owner:** parth (orchestrator) — implementation delegated to Phase 6.5 impl agents
**Parent sub-PRD:** [projects-prd.md](projects-prd.md) — Phase 6 Projects destination
**Master:** [destinations-master-prd.md](../destinations-master-prd.md) §5.4 (Projects)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer + thread canvas
**Binding cross-PRD decisions:** [cross-audit.md](../cross-audit.md) — `ItemRef` (§1.1), ports (§1.2), project-scoped ACL (§1.3), audit `context` (§1.4), filter axis OR (§1.5), `<PageHeader>` (§1.6), branded IDs (§2.1), cascade default (§5.3), Projects Q3 / Q4 / Q6 (recorded in this document, to be back-filled into cross-audit §9.8 on merge)
**Precedent:** Todos Q6 context-aware project default ([cross-audit §9.6](../cross-audit.md), [todos-prd.md §6](todos-prd.md))
**Implementation phasing:** §11 below; six impl agents in parallel (P6.5-A1 / A2 / B1 / B2 / C1 / C2)
**Time-budget assumption:** Phase 6 ships first; Phase 6.5 follows on the same merge train but as a separate branch family — keeps the Phase 6 review surface bounded.

---

## §1 Purpose + relationship to Phase 6

### 1.1 Why a sub-PRD, not folded into Phase 6

Phase 6 (Projects) shipped the **product foundation**: `Project` shape, members, color/icon, list/detail screens, project-scoped ACL across destinations, archive (soft-delete). During the post-spec product review, five follow-ups surfaced that are **adjacent to Phase 6** but each carries non-trivial implementation surface:

1. A new backend module (`liveness/`) that is **load-bearing across the whole workspace** (not just Projects).
2. A new field (`default_connector_allowlist`) plus inheritance hooks on **two other destinations** (Chats, Routines).
3. A new sub-product (`ProjectTemplate`) with its own table, endpoints, and UI surface.
4. A frontend wiring fix that touches **the chat composer**, not Projects.
5. A 409 contract on the existing archive endpoint.

Folding all five into Phase 6 mid-stream would:

- **Bloat the Phase 6 review surface** beyond what one orchestrator pass can responsibly cover.
- **Block Phase 6** on the liveness service (which itself spans three services).
- **Force re-spec** of the Phase 6 PRD post-merge — worse than a new sub-PRD with a clear pointer.

A separate sub-PRD keeps Phase 6 clean (foundation lands, archive returns 200 today), and lets Phase 6.5 land the cross-cutting concerns once the foundation is stable. Same precedent the orchestrator used for cross-audit §9.6 (Todos Q6 as a later revision) and §9.7 (Routines Q1-Q14 as a binding revision after sub-PRD merge).

### 1.2 What Phase 6 already owns (do not re-spec here)

- `Project` core wire shape (id, tenant_id, owner_user_id, name, description, color_hue, icon_emoji, members, starred-per-user, created_at, updated_at, archived_at).
- `project_members` table + role enum (owner / editor / viewer).
- Project list / detail / editor screens.
- Project-scoped ACL applied to Todos / Routines / Inbox / Library — already binding via cross-audit §1.3.
- `DELETE /v1/projects/{id}` (archive = soft-delete; 200 today).
- Per-user `starred` flag (separate table `project_user_stars`).
- Audit actions `project.created`, `project.updated`, `project.member_added`, `project.member_removed`, `project.member_role_changed`, `project.archived`.

Phase 6.5 **extends** these. It does **not** redefine them.

### 1.3 What Phase 6.5 deliberately does NOT cover

- Cross-tenant project sharing (security boundary).
- External collaborator support (master §5.4 OQ — stays out).
- Admin force-transfer of project ownership (cross-audit §9.7 Q12 precedent: deferred indefinitely).
- Project-level color/icon palette editing (Phase 6 covers).
- Project memberships UI rewrites.
- Hard-delete of archived projects (already in Phase 6 retention sweeper at 90d).

---

## §2 Concerns covered (the five)

| #   | Concern                                               | Trigger                                                                                             | Phase          | Owner agent     |
| --- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------- | -------------- | --------------- |
| 1   | **Context-aware chat creation**                       | Mirrors Todos Q6 (cross-audit §9.6) — current route should determine `project_id` on new chat       | Frontend only  | P6.5-C1         |
| 2   | **Liveness orchestrator service**                     | Projects Q4 — "Is anything running for this project?" needs ONE answer; archive/routine/revoke etc. | Backend module | P6.5-A1         |
| 3   | **`Project.default_connector_allowlist` inheritance** | Projects Q3 — owner sets project connector defaults; new chats/routines inherit on create           | Backend + UI   | P6.5-A1 + B1    |
| 4   | **Archive blocked when running**                      | Projects Q4 (cont.) — archive must reject if anything is live; returns 409 with `LivenessReport`    | Backend + UI   | P6.5-A1 + B2    |
| 5   | **Project templates + forking**                       | Projects Q6 — save a project's configuration as a Template; fork instantiates a new Project         | Backend + UI   | P6.5-A1 + B1+C2 |

Each is small enough to land independently. They share `LivenessReport` (concern 2), the new `default_connector_allowlist` field (concerns 3 + 5), and the archive endpoint (concern 4). The liveness service is the load-bearing piece — every other concern can refer to it.

---

## §3 Liveness orchestrator service (THE load-bearing decision)

### 3.1 What it is

A **backend module** at `services/backend/src/backend_app/liveness/` that exposes a single read API: **"is anything running for project X?"**

It is:

- A **module**, not a service. No new Dockerfile, no new venv, no new deploy path.
- A **read-only** aggregator. Never writes domain state. The only side effect is an audit row on cache-miss (§3.7).
- **One concern.** Aggregating cross-destination liveness reads. Nothing else.
- **A single source of truth.** Archive, routine pre-fire validation, connector revoke pre-check, and project transfer pre-check **all call this same module** (or the same endpoint, when cross-service). No other code path computes "is this project alive" independently. (DRY — this is the binding rule.)

It is **not**:

- A run-lifecycle owner (ai-backend owns runs).
- A scheduler (runtime_worker owns routine fires).
- A god class. Method count ≤ 3; LOC budget ≤ 250 across the module. Anything beyond aggregation belongs elsewhere.

### 3.2 Why it lives in `backend`, not `ai-backend` or `backend-facade`

| Candidate location | Why rejected                                                                                                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ai-backend`       | ai-backend owns the runs table but not routines / inbox / projects. Liveness aggregates four sources; putting it in ai-backend forces three cross-service calls in the hot path. |
| `backend-facade`   | Facade is a proxy — should not introduce its own product logic. Master CLAUDE.md "Don't put AI orchestration in `backend-facade`" applies by analogy to product aggregation.     |
| **`backend`**      | Owns projects, routines, and inbox tables. Calls ai-backend for runs/approvals (already a peer over HTTP). Smallest network footprint. **Selected.**                             |

### 3.3 `LivenessReport` wire shape

```python
# services/backend/src/backend_app/liveness/schema.py (NEW)

from pydantic import BaseModel, Field
from typing import Literal


class LivenessDetail(BaseModel):
    """One row per upstream source the aggregator queried."""
    source: Literal[
        "ai_backend.runs",
        "ai_backend.approvals",
        "backend.routines",
        "backend.inbox",
    ]
    count: int                              # 0 when no liveness signal from this source
    is_alive: bool                          # source-local view of liveness
    error: str | None = None                # populated when the upstream errored; count=0, is_alive=False
    fetched_at: str                         # ISO timestamp; for cache-hit observability


class LivenessReport(BaseModel):
    """Aggregated liveness across destinations, per-project."""
    project_id: str
    tenant_id: str
    is_alive: bool                          # OR across all details where error is None
    active_runs: int = Field(ge=0)          # ai-backend runs in {queued, running}
    pending_approvals: int = Field(ge=0)    # ai-backend approvals in {pending}
    active_routines: int = Field(ge=0)      # backend routines in {active}; webhook/event/manual count as alive
    in_flight_inbox: int = Field(ge=0)      # inbox items in {unread, snoozed} referencing the project
    details: list[LivenessDetail]
    computed_at: str                        # ISO; the report timestamp (cache key dimension)
    cache_hit: bool                         # true → returned from 2s cache; false → fresh aggregate
```

**TypeScript mirror** (`packages/api-types/src/liveness.ts`):

```typescript
import type { ProjectId, TenantId } from "./brands";

export type LivenessDetailSource =
  | "ai_backend.runs"
  | "ai_backend.approvals"
  | "backend.routines"
  | "backend.inbox";

export interface LivenessDetail {
  readonly source: LivenessDetailSource;
  readonly count: number;
  readonly is_alive: boolean;
  readonly error: string | null;
  readonly fetched_at: string;
}

export interface LivenessReport {
  readonly project_id: ProjectId;
  readonly tenant_id: TenantId;
  readonly is_alive: boolean;
  readonly active_runs: number;
  readonly pending_approvals: number;
  readonly active_routines: number;
  readonly in_flight_inbox: number;
  readonly details: ReadonlyArray<LivenessDetail>;
  readonly computed_at: string;
  readonly cache_hit: boolean;
}
```

### 3.4 Module layout

```
services/backend/src/backend_app/liveness/
  __init__.py
  schema.py            # LivenessReport / LivenessDetail (above)
  service.py           # LivenessService: one method, is_project_alive(...)
  cache.py             # in-process TTL cache, 2s default
  ai_backend_client.py # HTTP client for ai-backend (runs + approvals)
  routes.py            # GET /internal/v1/liveness/project/{project_id}
```

Total LOC budget: ≤ 250 across the module. If a file grows beyond ~80 LOC, it's evidence we are leaking concerns; refactor before merging.

### 3.5 API

**Internal** (consumed by other backend modules + by the facade for the archive 409 contract):

| Method | Path                                         | Auth                                                                                           | Purpose                                                                     |
| ------ | -------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| GET    | `/internal/v1/liveness/project/{project_id}` | `ENTERPRISE_SERVICE_TOKEN` + `x-enterprise-org-id` + `x-enterprise-user-id` (master CLAUDE.md) | Returns `LivenessReport`. 200 always; partial-failure in `details[].error`. |

There is **no app-facing `/v1/liveness/...` endpoint**. The frontend never reads liveness directly; it sees `LivenessReport` only embedded in 409 archive responses (§6) and template-fork pre-validation (§7). This is intentional — there is no UX for "show me liveness" outside of the contexts that gate on it.

**In-process API** (for backend modules):

```python
class LivenessService:
    async def is_project_alive(
        self,
        *,
        tenant_id: TenantId,
        project_id: ProjectId,
        force_refresh: bool = False,
    ) -> LivenessReport: ...
```

Callers (single source of truth):

1. **Archive endpoint** (`backend/projects/routes.py::archive_project`) — `force_refresh=False`; rejects with 409 if `report.is_alive`.
2. **Routine pre-fire validation** (`backend/routines/service.py::activate`) — `force_refresh=True` only on operator-triggered activate (not on every scheduler tick; the scheduler uses its own optimistic next-fire-at logic).
3. **Connector revoke pre-check** (`backend/api_keys/...` or wherever the connector destination revoke handler lives) — `force_refresh=False`; warns the user in-UI if connector is in use by an active routine in this project.
4. **Project transfer pre-check** (out of scope Phase 6.5 — but the hook exists; transfer-to-new-owner will gate on liveness in Phase 7+).
5. **Template fork pre-validation** (§7.3) — `force_refresh=False`; not strictly required (forking a paused-project template is fine) but used for the "this project has live work" warning banner.

**Any other call site is a bug.** Code-review rule: a new file that computes "is this project alive?" without going through `LivenessService` should be rejected.

### 3.6 Aggregation strategy

The `is_project_alive` method fans out four parallel HTTP / DB calls and aggregates:

```python
async def is_project_alive(
    self, *, tenant_id, project_id, force_refresh=False,
) -> LivenessReport:
    if not force_refresh:
        cached = self._cache.get(tenant_id, project_id)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True})

    # Fan out in parallel; tolerate partial failure.
    runs_task        = self._ai_backend.count_active_runs(tenant_id, project_id)
    approvals_task   = self._ai_backend.count_pending_approvals(tenant_id, project_id)
    routines_task    = self._routines_store.count_active_routines(tenant_id, project_id)
    inbox_task       = self._inbox_store.count_in_flight(tenant_id, project_id)

    results = await asyncio.gather(
        runs_task, approvals_task, routines_task, inbox_task,
        return_exceptions=True,
    )

    details = [
        _build_detail("ai_backend.runs",       results[0]),
        _build_detail("ai_backend.approvals",  results[1]),
        _build_detail("backend.routines",      results[2]),
        _build_detail("backend.inbox",         results[3]),
    ]

    report = LivenessReport(
        project_id=project_id, tenant_id=tenant_id,
        active_runs        = _value(details[0]),
        pending_approvals  = _value(details[1]),
        active_routines    = _value(details[2]),
        in_flight_inbox    = _value(details[3]),
        is_alive=any(d.is_alive for d in details if d.error is None),
        details=details,
        computed_at=utcnow_iso(),
        cache_hit=False,
    )
    self._cache.put(tenant_id, project_id, report)
    return report
```

Aggregation rules:

- `is_alive` = `any(d.is_alive for d in details if d.error is None)`. **A source that errored is NOT counted as alive** (fail-open for archive — see §3.8 for the tradeoff).
- Each `LivenessDetail` is independent. One upstream slow ≠ block the rest. `asyncio.gather(return_exceptions=True)` ensures we never block on any one source.
- Cache key: `(tenant_id, project_id)`. **NOT** `(tenant_id, project_id, user_id)` — liveness is a project property, not a user property. The cache is a tenant-isolated dict; size bounded by tenant active-project count (~10³).

### 3.7 Caching

- **TTL = 2 seconds** (configurable via `LIVENESS_CACHE_TTL_SECONDS`).
- **Scope:** in-process per-instance. Liveness is read-mostly and tolerates 2s of staleness; cross-instance cache coherence is not worth the Redis hop.
- **Eviction:** TTL only. No LRU (the working set is bounded by number of projects with archive/revoke attempts in any 2s window, ≤ 100 in practice).
- **Cache-miss observability:** emit a `liveness.cache_miss` counter + a debug audit row when `_cache_miss_audit_enabled=True` (off by default; turned on by operators when investigating). Per spec rule "never mutates state, never enqueues, never side-effects beyond audit on cache miss" — this audit is the ONLY allowed write.
- **Force refresh:** `force_refresh=True` skips the read but still writes the result to cache (so the next caller within 2s benefits).

### 3.8 Partial-failure tolerance

A core design rule: **if ai-backend is slow or down, liveness must still return.** The alternative — blocking archive forever — is worse UX than a partial answer.

The tradeoff:

- **Pro:** archive does not hang on a transient upstream outage. Pre-fire validation does not block routine activation forever.
- **Con:** if ai-backend is down AND there are 5 running runs for the project, archive **may succeed** (because the runs source errored and was excluded from `is_alive`). The runs will then complete against a soft-deleted project (already a documented dead-link state per cross-audit §5.3 cascade default).

Mitigation: when `details[*].error` is non-empty, the archive 409 UI shows the partial answer + an "Errored upstream sources" pill, so the user can re-attempt after the outage clears. We accept the soft-archive race as the lesser evil. Documented in §6.4.

### 3.9 What it does NOT own

Listed because past god-class drift came from creeping responsibilities:

- Does NOT mutate run state, route runs, cancel runs, or pause routines.
- Does NOT make policy decisions ("archive is OK") — it returns the report; the calling endpoint makes the policy call.
- Does NOT carry connector / billing / quota concerns.
- Does NOT emit SSE envelopes (no subscribers exist; the report is request/response only).
- Does NOT include user-scoped fields (it's project-level, not user-level — no "is this user looking at the project" signal).

If a downstream caller needs additional liveness signal (e.g., "is the current viewer typing in a chat?"), that is a **different concern** with its own type. Do not extend `LivenessReport`.

### 3.10 Tests (§10 elaborates)

Mandatory test coverage for the module:

1. **Aggregation correctness** — populate fake upstream counts, assert `LivenessReport` fields match.
2. **Cross-tenant isolation** — project P belongs to tenant T1; querying with `tenant_id=T2` returns 404 (or `is_alive=False` with all zero counts, depending on the auth layer — service-token internal route → existence-not-leaked is the default).
3. **Partial failure** — one upstream raises; report returns with `details[].error` populated; `is_alive` reflects the others.
4. **2-second cache** — two calls within 2s share the same `computed_at`; second call has `cache_hit=True`. After 2s, `cache_hit=False`.
5. **Idempotent calls** — N parallel calls for the same (tenant, project) → only one fan-out actually executes (single-flight is a nice-to-have; not required for Phase 6.5 if it complicates the cache).
6. **Read-only invariant** — calling `is_project_alive` does not change any row in any table (assert via DB snapshot before/after).
7. **Force-refresh bypass** — `force_refresh=True` bypasses cache; subsequent normal call within 2s returns the fresh value with `cache_hit=True`.
8. **Cache-miss audit emitted** when feature flag is on; off by default.

---

## §4 Context-aware chat creation

### 4.1 Today

`POST /v1/agent/conversations` already accepts an optional `project_id` field on the create payload (Phase 1 chats canvas wired it). The bug: the frontend does **not** populate it from route context. A user clicking "New chat" while at `/projects/<id>` gets a chat with `project_id=null` (Unfiled) — wrong default.

This mirrors the Todos Q6 bug (cross-audit §9.6) where todos created from `/projects/<id>` defaulted to Unfiled. The fix is the same shape; just applied to chats this time.

### 4.2 The change

Frontend-only wiring. Three rules:

| User is on...                              | New-chat default `project_id`                                               |
| ------------------------------------------ | --------------------------------------------------------------------------- |
| `/projects/<id>` (any project view)        | `<id>`                                                                      |
| `/projects/<id>/<tab>` (subroute)          | `<id>`                                                                      |
| `/chats` (direct)                          | `null` (Unfiled)                                                            |
| `/chats/<conversation_id>`                 | inherits the **current conversation's** `project_id` (preserves continuity) |
| `/inbox`, `/todos`, etc. + inline composer | `null` (no project context outside Projects routes)                         |
| `/projects/<id>/library/<page>`            | `<id>` (project ID still wins; the page route is nested)                    |

Composer respects a UI-level override: a `[Filed under: ▾]` chip on the composer footer lets the user clear or switch the project before sending. The chip is **always visible** so the inheritance is observable, not invisible. (Compliance: untrusted-input rule — the chip's selected value is what the request sends; the route is the default, not a hard binding.)

### 4.3 Backend wire shape

Unchanged. `POST /v1/agent/conversations` continues to accept `project_id: ProjectId | null`. The backend already validates project membership before accepting the chat-create (Phase 6 ACL hook). The 403 path is unchanged.

### 4.4 Test gates (frontend)

- Click "New chat" on `/projects/<id>` → POST body carries `project_id=<id>`.
- Click "New chat" on `/chats` → POST body carries `project_id=null`.
- Click "New chat" on `/projects/<id>/library/<page>` → POST body carries `project_id=<id>`.
- Send-message on `/chats/<conversation_id>` where conv has `project_id=X` → if conversation was loaded with `X`, no new conversation is created; if creating mid-typing (no conversation yet but the user navigated from a project), POST carries the route's project_id.
- Composer chip override: user is on `/projects/<id>` → chip shows `<id>`; user clicks chip and selects "Unfiled" → POST body carries `null`.

### 4.5 Audit

No new audit action. The existing `conversation.created` audit row already carries `context.project_id` (cross-audit §1.4 `context` envelope) — when this change lands, that field becomes populated for the route-context case, matching the existing Todos behavior.

---

## §5 `Project.default_connector_allowlist` (Q3 inheritance)

### 5.1 New field

Add to `Project`:

```typescript
// packages/api-types/src/projects.ts (extend)
export interface Project {
  // ... existing fields from Phase 6 ...
  readonly default_connector_allowlist: ReadonlyArray<ConnectorSlug> | null;
}
```

`ConnectorSlug` is the **kind** of connector (`"salesforce"`, `"gmail"`, etc.), not a `ConnectorId` (a specific OAuth grant). Allowlists travel as kinds because they need to outlive a re-grant — if a user disconnects+reconnects Salesforce, the project's "Salesforce is allowed here" rule should persist.

`null` = "no project default; chats/routines inherit owner's default at create time" (existing Phase 1 behavior).
`[]` (empty array) = "no connectors allowed in this project" — explicit denial. Saves users a step in regulated-buyer projects where the policy is "this project only ever uses internal tools."
`["salesforce", "gmail"]` = allowlist; only these connector kinds are pre-enabled.

### 5.2 Storage

Migration adds one column to `projects`:

```sql
ALTER TABLE projects
  ADD COLUMN default_connector_allowlist jsonb DEFAULT NULL;
```

JSONB (not text[]) for forward compatibility — if we extend `ConnectorSlug` to a richer `{slug, scope}` shape later, the column already accepts it.

### 5.3 Editor UI (ProjectEditor extension)

In `ProjectEditor`, add a new section **"Default connectors for new chats and routines"** with three modes:

- ◯ **Inherit owner defaults** (`null`) — current behavior.
- ◯ **No connectors by default** (`[]`) — explicit empty.
- ◯ **Specific allowlist** — multi-select of connector kinds. Picker shows only the **owner's connected** connector kinds (cross-audit §2.1 + the existing connector destination's user-scoped list).

The setting is **owner-only** (matches Phase 6 mutation rule: only project owners edit project config).

### 5.4 Inheritance hook (server-side, owner-only)

When a new chat (`POST /v1/agent/conversations`) or routine (`POST /v1/routines`) is created with `project_id` set:

```python
# pseudocode inside ConversationCreateHandler.create / RoutineService.create
if payload.project_id is not None:
    project = await projects.get(payload.project_id, tenant_id)
    if project.default_connector_allowlist is not None and not payload.connectors:
        payload.connectors = _materialize_allowlist(
            project.default_connector_allowlist,
            owner_user_id=current_user_id,
        )
```

Rules:

- **Only when the caller did not pass an explicit `connectors` list.** If the user already chose connectors in the composer, that choice wins.
- **Owner's connector availability still gates.** If the project allowlist says `["salesforce"]` but the creating user does not have a Salesforce grant, the materialize step skips it — there is no auto-prompt-to-connect (Phase 7+ may add that flow).
- **Server-side only.** Frontend does not duplicate the materialize logic; the composer just sees the "inherited" connectors in the chat's first state once the server response comes back.

### 5.5 Override behavior

After create, the existing per-chat / per-routine connector-scope PATCH APIs are unchanged. The user can narrow or widen the scope as the conversation evolves. The project default is **a default at create time, not a continuing constraint**.

(Aside: a future "project policy" feature might add a hard-constraint mode — `Project.connector_policy: { mode: "default" | "hard_constraint" }`. Phase 6.5 ships only the default mode. The wire field name `default_connector_allowlist` is forwards-compatible with that future extension.)

### 5.6 Audit

New audit action:

- `project.default_connector_allowlist_changed` — fires on PATCH; `context = { before: [...], after: [...] }`. The existing `project.updated` audit row also carries the diff in `changed_fields`, so this is a refined variant for ops dashboards that care about connector policy specifically.

The chat-create / routine-create audit rows gain a `context.inherited_from_project_default: bool` flag so ops can answer "what fraction of chats inherit project defaults vs. user-chosen?"

---

## §6 Archive blocked when running

### 6.1 The contract

`DELETE /v1/projects/{id}` (the archive endpoint Phase 6 introduced) now:

1. Calls `LivenessService.is_project_alive(tenant_id, project_id)`.
2. If `report.is_alive == True` → returns **HTTP 409 Conflict** with body:

```json
{
  "error": "project_archive_blocked_live_work",
  "message": "Cannot archive project with active runs / routines / approvals / inbox items.",
  "liveness": {
    /* full LivenessReport */
  }
}
```

3. If `report.is_alive == False` → proceeds with the existing soft-delete flow (sets `archived_at`, emits `project.archived` audit, returns 200).

### 6.2 No force-archive

There is **no** `?force=true` flag, no admin override. Cross-audit §9.7 Q12 set the precedent — "Admin force-reassign owner / force-pause: out of scope (deferred indefinitely)." Same rule here: archive is a userspace operation; if live work exists, the user must cancel or wait. Admin override is a future compliance-audit-driven decision, not a Phase 6.5 concern.

### 6.3 UI: archive dialog renders `LivenessReport`

When the frontend gets 409, it renders the `LivenessReport` in the archive confirmation modal:

```
┌────────────────────────────────────────────────────────────┐
│ Cannot archive "Acme Renewal"                              │
│                                                            │
│ This project has work in flight:                           │
│   ● 2 active runs                       [View runs ›]      │
│   ● 1 pending approval                  [View approval ›]  │
│   ● 3 active routines                   [View routines ›]  │
│   ● 5 in-flight inbox items             [View inbox ›]     │
│                                                            │
│ Cancel each, or pause routines, before archiving.          │
│                                                            │
│           [ Cancel ]    [ Refresh status ]                 │
└────────────────────────────────────────────────────────────┘
```

The "View ..." links use the existing `<ItemLink>` registry (cross-audit §3.3) with project-scoped filter pre-applied. "Refresh status" re-calls the archive endpoint (force-refresh on the server side, see §3.7).

When `details[*].error` is non-empty (partial-failure case), the modal shows a yellow banner: "One or more checks errored — showing partial result. Retry the archive in a few seconds."

### 6.4 Trade-off: soft-archive race documented

Per §3.8, if ai-backend is down AND there are running runs, archive may succeed. Documented as a known limitation:

- The orphaned runs land in the soft-archived project's records (dead-link per cross-audit §5.3 cascade default).
- The 90d retention sweeper for archived projects (Phase 6) will eventually GC them; in the interim, the runs are queryable via direct run ID lookup but invisible from the project view.
- This is the lesser evil compared to blocking archive on transient outages.

### 6.5 Audit

New audit action:

- `project.archive_blocked` — fires on 409 response; `context = { liveness: <LivenessReport-without-details>, attempt_n: <int> }`. Useful for ops: which projects keep failing to archive? Which destination is keeping them alive? Acts as a leading indicator for "user is confused about what's running."

The existing `project.archived` audit row gains `context.liveness_at_archive: LivenessReport` (the 200-path report) so the audit chain can later prove "yes, the project was clean when archived."

### 6.6 Tests

- Archive when nothing live → 200, audit row contains `liveness_at_archive` with all zeros.
- Archive with 1 active run → 409, response body carries full `LivenessReport`, `project.archive_blocked` audit row written.
- Archive with 1 active routine → 409.
- Archive with 1 pending approval → 409.
- Archive with 1 unread inbox item → 409.
- Archive when ai-backend errors (simulated) → behavior matches §3.8 — `details[].error` populated, `is_alive` reflects only successful sources, archive may proceed depending on remaining sources.
- Cross-tenant: archive of project P in tenant T1 from tenant T2 → 404 (existence not leaked, master rule).
- Force-archive bait: any `?force=...`, `?override=...`, `X-Atlas-Force` header → ignored; 409 still fires.

---

## §7 Project templates + forking

### 7.1 Premise

Atlas users repeatedly create similar projects ("New customer onboarding," "Quarterly compliance review," "RFP response — Customer N"). A `ProjectTemplate` captures a saved configuration; **forking** instantiates a new Project from a template. Forking is a **copy operation** — no live link from template to forked project.

Cross-audit §9.7 Q13 said "Routine forking / templates: Wave 5+" — but that was about routine forking specifically. Project templates is a separate concern with separate scope (carries project shape, not routine shape) and Phase 6 is the right home for the Project equivalent. Phase 6.5 ships it.

### 7.2 `ProjectTemplate` wire shape

```typescript
// packages/api-types/src/project-templates.ts (NEW)

import type {
  ProjectTemplateId,
  ProjectId,
  TenantId,
  UserId,
  ConnectorSlug,
  TodoId,
  RoutineId,
} from "./brands";

export interface ProjectTemplateSeededTodo {
  readonly text: string; // ≤ 280 chars
  readonly priority: "low" | "normal" | "high" | null;
  readonly relative_due_days: number | null; // days from fork time; null = no due
  readonly labels: ReadonlyArray<string>;
}

export interface ProjectTemplateSeededRoutine {
  readonly name: string;
  readonly description: string;
  readonly instructions_template: string; // ≤ 16KB; supports {{project.name}} placeholder
  readonly triggers: ReadonlyArray<{
    readonly kind: "schedule" | "manual"; // webhook/event templates deferred (need fresh secrets)
    readonly cron?: string;
    readonly tz?: string;
  }>;
}

export interface ProjectTemplateSnapshot {
  readonly default_member_user_ids: ReadonlyArray<UserId>; // owner-side suggestion; user can edit before fork-confirm
  readonly default_connector_allowlist: ReadonlyArray<ConnectorSlug> | null;
  readonly color_hue: number | null;
  readonly icon_emoji: string | null;
  readonly seeded_todos: ReadonlyArray<ProjectTemplateSeededTodo>;
  readonly seeded_routines: ReadonlyArray<ProjectTemplateSeededRoutine>;
}

export interface ProjectTemplate {
  readonly id: ProjectTemplateId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly name: string;
  readonly description: string;
  readonly snapshot: ProjectTemplateSnapshot;
  readonly source_project_id: ProjectId | null; // null if template was authored from scratch
  readonly created_at: string;
  readonly updated_at: string;
}
```

Notes:

- Webhook + event triggers are **not** templated (they need fresh secrets and connector grants; templating them would be a security footgun). Only `schedule` and `manual` survive into a template.
- `default_member_user_ids` is a **suggestion** — the fork-confirm UI lets the user review/edit before instantiation. We do not auto-add users to a project from a template without their or the owner's confirmation.
- `instructions_template` supports a tiny set of placeholders (`{{project.name}}`, `{{project.created_at}}`, `{{owner.display_name}}`). No DSL. No turing-complete substitution.
- `source_project_id` is informational — a forked project's source is captured for audit, but template-to-project is not a parent-child relationship. Fork is a copy.

### 7.3 Endpoints

| Method | Path                                 | Purpose                                                                                                                                     |
| ------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/v1/projects/{id}/save-as-template` | Snapshot the project's current configuration (members, allowlist, color/icon, project-filed todos & routines) into a new `ProjectTemplate`. |
| GET    | `/v1/project-templates`              | List templates in the caller's tenant. Sort/filter: `owner_user_id`, `q` (name/description full-text), `sort=created_at:desc` default.      |
| GET    | `/v1/project-templates/{id}`         | Single template. Read scope: tenant-wide (templates are not project-member-scoped; they describe a _would-be_ project).                     |
| POST   | `/v1/project-templates/{id}/fork`    | Create a new project from the template. Body: `{ name, description?, color_hue?, icon_emoji?, member_overrides?, connector_overrides? }`.   |
| PATCH  | `/v1/project-templates/{id}`         | Edit template metadata (name, description). Snapshot is immutable post-create (see §7.5).                                                   |
| DELETE | `/v1/project-templates/{id}`         | Soft-delete; 90d retention then hard-delete.                                                                                                |

**ACL:**

- `save-as-template` — caller must be the source project's owner.
- `list` / `get` — any tenant member can read templates in their tenant (templates are not secret; they describe configuration patterns).
- `fork` — any tenant member can fork. The forked project's owner is the **caller**, not the template author.
- `patch` / `delete` — template owner only (the user who created the template), OR a tenant admin (read-only compliance scope does not include delete).

**Cross-tenant sharing: NO.** Templates are tenant-scoped. Cross-tenant template marketplace is out of scope (mirrors routine no-cross-tenant rule, cross-audit §9.7 anti-goals).

### 7.4 Fork atomicity

Fork is **a single DB transaction**. Either:

- The new project, all seeded todos, all seeded routines, member rows, audit rows all land — OR —
- None of them do, and the response is 5xx.

No half-forked state. Rollback on any failure of:

- Project insert.
- Member-row inserts (skipped silently if `member_overrides` excludes a user, but never half-inserts a member set).
- Seeded-todo inserts.
- Seeded-routine inserts.
- Audit row writes.

Implementation: use the existing Phase 6 `projects` transactional helper + a new `templates_forker.py` that wraps the inserts. Cross-service concern: seeded routines live in `backend.routines` (same DB as projects, so the same transaction works). Seeded chats do NOT travel in templates (chats are interactive history; making a "fresh chat with no history" is just creating a new chat, no template needed).

### 7.5 Snapshot immutability

`ProjectTemplate.snapshot` is **immutable after create.** Reason: a forked project carries a `source_template_snapshot_hash` in its audit row; if templates were editable, the audit trail loses meaning. Users who want to change a template duplicate it (save-as-template on the source project again) — saves are cheap.

`name` and `description` of the template itself are editable. Only the snapshot is immutable.

### 7.6 UI: TemplateGallery + fork flow

Two new screens:

**TemplateGallery (`/project-templates`):**

- `<PageHeader title="Project templates" primaryAction={{ label: "New from project", onClick: navigateToProjectListWithSaveCTA }} />`
- `<FilterTabs value={filter} options={["all", "mine"]} />` — multi-value OR per cross-audit §1.5.
- Card grid: each card shows template name, description (truncated), owner chip, "seeded N todos · M routines" summary, `[Fork] [Edit] [Delete]` actions.

**TemplateEditor (`/project-templates/<id>/edit`):**

- Edit name / description only (snapshot is read-only with a "View snapshot details" expander showing what will be forked).

**Fork dialog:** opened from the gallery card's `[Fork]` button. Shows:

- Required: new project name.
- Optional: description, color/icon (pre-filled from snapshot).
- Editable: member list (pre-filled from `default_member_user_ids`, but the caller can add/remove).
- Editable: connector allowlist (pre-filled from snapshot's `default_connector_allowlist`).
- Preview: "Will seed N todos and M routines into the new project."
- `[Fork]` → POST → on success, navigate to the new project's detail view.

Also: a `[Save as template]` action lives in the Project detail view's `[⋯]` menu (Phase 6 already has this menu).

### 7.7 Storage

New table `project_templates`:

| Column                                     | Type / Notes                                                             |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `id` (ProjectTemplateId) / `tenant_id`     | uuid PK / NN                                                             |
| `owner_user_id`                            | uuid NN; immutable post-create                                           |
| `name` / `description`                     | text NN (≤ 80 / ≤ 200)                                                   |
| `snapshot`                                 | jsonb NN; matches `ProjectTemplateSnapshot`                              |
| `source_project_id`                        | uuid NULL; informational; no FK (we want template to outlive its source) |
| `created_at` / `updated_at` / `deleted_at` | timestamptz; soft-delete pattern                                         |

Indexes:

- `project_templates_tenant_idx` — B-tree on `(tenant_id, created_at DESC) WHERE deleted_at IS NULL`.
- `project_templates_owner_idx` — B-tree on `(tenant_id, owner_user_id, created_at DESC) WHERE deleted_at IS NULL`.
- `project_templates_search_idx` — GIN on `to_tsvector('simple', name || ' ' || description) WHERE deleted_at IS NULL`.

### 7.8 Retention

- Soft-deleted templates retained 90d, then hard-deleted by the same backend retention cron Phase 6 extended.
- Forked-from audit rows are append-only (audit-chain) and outlive the template — required for "this project was created from template X on date Y" compliance.

### 7.9 Audit

New audit actions:

- `project.template_saved` — `target_kind=project_template`, `context = { source_project_id, snapshot_hash }`.
- `project.template_forked` — `target_kind=project` (the new project's id), `context = { source_template_id, snapshot_hash, seeded_todos_count, seeded_routines_count }`.
- `project.template_updated` — metadata edit (name/description only).
- `project.template_deleted` — soft-delete.

The audit chain proves: given a project's id, was it template-forked? From which template? When?

### 7.10 Tests

- **Snapshot integrity** — save-as-template on project P captures every owner-editable field; round-trip fork into P' produces a project with the same shape (modulo new id, new timestamps, new owner=caller).
- **Fork creates owner-scoped project** — caller is owner of the forked project regardless of who authored the template.
- **Tenant isolation** — template T in tenant T1 not visible from tenant T2; fork attempts → 404.
- **Atomic rollback** — inject a fault in seeded-routine insert; assert no project row, no todo rows, no member rows survive.
- **Snapshot immutability** — PATCH on template's snapshot field → 422 ("snapshot is immutable; duplicate the template instead").
- **Webhook/event triggers stripped** — template authored from a project with a webhook routine; saved snapshot has only `schedule`/`manual` triggers.
- **`{{project.name}}` substitution** — fork into "My Quarterly Review"; seeded routine instructions render with that string.
- **Audit completeness** — save + fork + delete each produces the expected audit row with the expected `context`.
- **Member override** — fork dialog excludes a user from `default_member_user_ids`; forked project has only the override-confirmed members.
- **90d retention** — soft-delete + 91 days → row hard-deleted by retention cron + summary audit.

---

## §8 Storage

### 8.1 Schema additions

```sql
-- 8.1.1 default_connector_allowlist on projects
ALTER TABLE projects
  ADD COLUMN default_connector_allowlist jsonb DEFAULT NULL;

-- 8.1.2 project_templates
CREATE TABLE project_templates (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  owner_user_id       uuid NOT NULL,
  name                text NOT NULL CHECK (length(name) <= 80),
  description         text NOT NULL DEFAULT '' CHECK (length(description) <= 200),
  snapshot            jsonb NOT NULL,
  source_project_id   uuid NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz NULL
);

CREATE INDEX project_templates_tenant_idx
  ON project_templates (tenant_id, created_at DESC)
  WHERE deleted_at IS NULL;

CREATE INDEX project_templates_owner_idx
  ON project_templates (tenant_id, owner_user_id, created_at DESC)
  WHERE deleted_at IS NULL;

CREATE INDEX project_templates_search_idx
  ON project_templates
  USING gin (to_tsvector('simple', name || ' ' || description))
  WHERE deleted_at IS NULL;
```

### 8.2 Migration order

1. Phase 6 lands `projects` + `project_members` + `project_user_stars` tables (prerequisite).
2. Phase 6.5 migration adds `projects.default_connector_allowlist` column (additive; no rewrite).
3. Phase 6.5 migration creates `project_templates` table + indexes.

Both Phase 6.5 migrations are non-blocking (additive); zero-downtime deploy is feasible.

### 8.3 Liveness service has NO new tables

The liveness service is read-only against existing tables (runs in ai-backend, routines/inbox in backend). No schema changes for liveness itself.

---

## §9 Audit

New audit actions introduced by this sub-PRD (in addition to the inherited Phase 6 set):

| Action                                            | Trigger                                            | `context` (cross-audit §1.4)                                                                       |
| ------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `project.default_connector_allowlist_changed`     | PATCH on `projects` changing the allowlist         | `{ before: [...], after: [...] }`                                                                  |
| `project.archive_blocked`                         | `DELETE /v1/projects/{id}` returns 409             | `{ liveness: <LivenessReport>, attempt_n }`                                                        |
| `project.archived` (existing — gains context)     | `DELETE /v1/projects/{id}` returns 200             | `{ liveness_at_archive: <LivenessReport> }` (additive context)                                     |
| `project.template_saved`                          | `POST /v1/projects/{id}/save-as-template`          | `{ template_id, source_project_id, snapshot_hash }`                                                |
| `project.template_forked`                         | `POST /v1/project-templates/{id}/fork` succeeds    | `{ new_project_id, source_template_id, snapshot_hash, seeded_todos_count, seeded_routines_count }` |
| `project.template_updated`                        | `PATCH /v1/project-templates/{id}`                 | `{ changed_fields }`                                                                               |
| `project.template_deleted`                        | `DELETE /v1/project-templates/{id}`                | `{ soft: true }`                                                                                   |
| `liveness.cache_miss` (debug; flag-off default)   | LivenessService cache miss with debug flag on      | `{ project_id, sources: ["ai_backend.runs", ...] }`                                                |
| `conversation.created` (existing — gains context) | new chat created from route-context inheritance    | `{ project_id, inherited_from_project_default: bool }` (additive context)                          |
| `routine.created` (existing — gains context)      | new routine created from project allowlist inherit | `{ project_id, inherited_from_project_default: bool }` (additive context)                          |

All actions write via the existing `packages/audit-chain` immutable adapter. No new writers, no in-memory adapters. Same SIEM export path.

---

## §10 Test plan

### 10.1 Liveness service (P6.5-A1)

1. **Happy-path aggregation:** 2 active runs + 1 pending approval + 3 active routines + 5 unread inbox items → `LivenessReport` exactly carries those counts and `is_alive=True`.
2. **All-clear:** zero of everything → `is_alive=False`, all counts 0.
3. **Cross-tenant isolation:** project P in T1 → query from T2 → existence-not-leaked (404 at the route, all-zero counts at the in-process API).
4. **Partial failure — runs source errors:** mock ai-backend `count_active_runs` raises `httpx.ReadTimeout`; report returns with `details[runs].error` populated, `details[runs].is_alive=False`, other sources' counts honored.
5. **Partial failure — all sources error:** every upstream errors → `is_alive=False`, every `details[].error` populated. Critical: report still returns 200, not 500.
6. **2s cache:** call 1 at t=0; call 2 at t=1s → same `computed_at`, second `cache_hit=True`. Call 3 at t=2.5s → fresh `computed_at`, `cache_hit=False`.
7. **Force refresh:** call with `force_refresh=True` skips cache; subsequent call within 2s returns cached.
8. **Read-only invariant:** call N times; DB snapshot before/after identical (no row mutations).
9. **Concurrent calls:** 10 parallel calls for the same (tenant, project) → may produce 1-10 fan-outs (depends on whether we add single-flight); never produce errors; all returned reports are equivalent.
10. **Cache-miss audit:** flag on → cache miss writes one audit row; flag off (default) → zero audit rows.

### 10.2 Context-aware chat creation (P6.5-C1)

11. New-chat from `/projects/X` → POST body `project_id=X`.
12. New-chat from `/chats` → POST body `project_id=null`.
13. New-chat from `/projects/X/library/Y` → POST body `project_id=X`.
14. Existing conversation with `project_id=X`, user sends a message → no new conversation; no POST `project_id` change.
15. Composer chip override: route is `/projects/X`, user clicks chip → switches to "Unfiled" → POST `project_id=null`.
16. Composer chip override: route is `/chats`, user clicks chip → switches to project Y → POST `project_id=Y`.
17. Composer chip is always visible (no run-state gating; mirrors the chat-surface invariant in apps/frontend CLAUDE.md).
18. Backend wire shape unchanged: `POST /v1/agent/conversations` still accepts the same field; no migration on ai-backend side.

### 10.3 `default_connector_allowlist` inheritance (P6.5-A1 + B1)

19. Project has allowlist `["salesforce", "gmail"]`; new chat created with `project_id=<that project>` and no `connectors` in payload → response includes pre-populated Salesforce + Gmail connectors.
20. Project has allowlist `[]` (explicit empty); new chat with `project_id=<that project>` and no `connectors` → response has zero connectors.
21. Project has allowlist `null`; new chat inherits **owner's** default connectors (existing Phase 1 behavior; no regression).
22. Caller passes explicit `connectors=[...]` AND `project_id=<has allowlist>` → caller's explicit list wins; allowlist NOT applied.
23. Allowlist refers to a connector the **owner does not have granted** → that connector is skipped (no auto-prompt-to-connect).
24. New routine with `project_id=<has allowlist>` and no connectors in payload → inherits in the same shape.
25. PATCH project's `default_connector_allowlist` → audit row `project.default_connector_allowlist_changed` with before/after.
26. Owner-only mutation: non-owner project member PATCH → 403; non-member → 404.
27. Cross-tenant: PATCH project P from tenant T2 → 404.

### 10.4 Archive blocked when running (P6.5-A1 + B2)

28. Archive clean project → 200, `project.archived` audit row carries `liveness_at_archive` with all-zero counts.
29. Archive with 1 active run → 409, body contains full `LivenessReport`, `project.archive_blocked` audit row.
30. Archive with 1 active routine → 409.
31. Archive with 1 pending approval → 409.
32. Archive with 1 unread inbox item → 409.
33. Force-archive bait: query param `?force=true` ignored; 409 still fires.
34. Archive when ai-backend errors (simulated) and other sources clean → archive proceeds per §3.8 trade-off; documented in test assertion comment.
35. Cross-tenant: archive of project P in T1 from T2 → 404 (not 409).
36. UI: 409 response → archive dialog renders item counts + `<ItemLink>` rows + "Refresh status" button works.
37. Repeated archive attempts: each blocked attempt increments `attempt_n` in audit context.

### 10.5 Project templates + forking (P6.5-A1 + B1/C2)

38. **Save-as-template happy path:** owner saves project P → new `ProjectTemplate` with snapshot capturing color/icon, allowlist, default members, project-filed todos/routines (only `schedule`/`manual` triggers).
39. **Snapshot integrity:** all fields in source project's `default_connector_allowlist`, color/icon, member list (as user-id suggestions) reproduce in fork.
40. **Webhook routine trigger stripped:** source project has a webhook-trigger routine → template snapshot has only the routine's `schedule`/`manual` triggers (or omits the routine if it had only webhook triggers).
41. **Fork creates owner-scoped project:** template authored by Alice; Bob forks → new project's `owner_user_id=Bob`.
42. **Atomic rollback:** inject fault in seeded-routine insert → no project row, no todo rows, no member rows persisted.
43. **`{{project.name}}` substitution:** fork with name "Acme Q4 Renewal" → seeded routine instructions string has `Acme Q4 Renewal` substituted.
44. **Member override:** fork dialog removes one user from `default_member_user_ids` → forked project has only the kept members.
45. **Connector override:** fork dialog changes `default_connector_allowlist` → forked project's column reflects the override, not the snapshot's value.
46. **Snapshot immutability:** PATCH on `project_templates.snapshot` → 422; PATCH on `name`/`description` → 200.
47. **Cross-tenant isolation:** template in T1 not visible from T2; fork attempt from T2 → 404.
48. **Delete + retention:** delete template → soft-deleted; 91-day-later cron run → hard-deleted + summary audit.
49. **Audit on save/fork/delete:** every action produces the expected row with the expected `context`.
50. **Forked-from-template trace:** new project's audit history contains `project.template_forked` → the `source_template_id` is queryable.

### 10.6 End-to-end smoke (added to `docs/dev-testing.md`)

```bash
export TOKEN=$(make dev-bearer)

# Set project allowlist
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  http://127.0.0.1:8200/v1/projects/<id> \
  -d '{"default_connector_allowlist": ["salesforce"]}'

# Create chat filed under project (inherits allowlist)
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  http://127.0.0.1:8200/v1/agent/conversations \
  -d '{"project_id": "<id>", "title": "test"}'

# Save project as template
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8200/v1/projects/<id>/save-as-template \
  -d '{"name": "Quarterly review template"}'

# Fork the template
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  http://127.0.0.1:8200/v1/project-templates/<tid>/fork \
  -d '{"name": "Acme Q4 Renewal"}'

# Try to archive a project with live work — expect 409 with LivenessReport
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/projects/<id>
```

---

## §11 Implementation phasing

Per Master §7 + Implementation-plan §2, Phase 6.5 fans out into **six impl agents** running in parallel inside the phase. Phase 6 (Projects core) must be merged first. Each agent has exclusive file ownership.

### 11.1 Agent boundaries (no overlap with shared files)

**P6.5-A1 backend foundations — `worktree-agent-phase6-5-a1-backend`**

Prereqs: Phase 6 P6-A (projects backend) merged. SP-1 (`brands.ts` for `ProjectTemplateId`).

Exclusive files:

- `packages/api-types/src/liveness.ts` (NEW); `packages/api-types/src/project-templates.ts` (NEW); append re-exports to `packages/api-types/src/index.ts`.
- `services/backend/src/backend_app/liveness/` (NEW module): `__init__.py`, `schema.py`, `service.py`, `cache.py`, `ai_backend_client.py`, `routes.py`.
- `services/backend/src/backend_app/projects/` — extend Phase 6's project module with:
  - schema migration adding `default_connector_allowlist`.
  - PATCH handler validation for `default_connector_allowlist`.
  - archive endpoint: pre-flight liveness check + 409 path.
- `services/backend/src/backend_app/project_templates/` (NEW): `__init__.py`, `routes.py`, `service.py`, `store.py`, `schema.py`, `forker.py`, `migrations.py`.
- `services/backend/src/backend_app/app.py` — append `include_router(liveness_router)` + `include_router(project_templates_router)` lines (merge after Phase 6).
- `services/backend-facade/src/backend_facade/project_templates_routes.py` (NEW).
- Tests in `services/backend/tests/{liveness,project_templates}/`.

Deliverables: liveness module + service + internal route + cache + tests; `default_connector_allowlist` column + PATCH + audit; archive 409 contract + audit; project_templates CRUD + fork + atomic transaction + retention extension + audit.

**P6.5-A2 ai-backend hook — `worktree-agent-phase6-5-a2-aibackend`**

Prereqs: P6.5-A1's `liveness.ts` and `default_connector_allowlist` shape merged (rebases on it). Phase 1 chat-create endpoint as-is.

Exclusive files:

- `services/ai-backend/src/runtime_api/http/routes.py` — extend `create_conversation` handler to call backend's project-get + materialize allowlist (server-side only; matches the inheritance hook in §5.4).
- `services/ai-backend/src/agent_runtime/api/conversation_coordinator.py` (or equivalent) — extend to apply allowlist when caller did not pass explicit `connectors`.
- Tests in `services/ai-backend/tests/runtime_api/test_conversation_create_inheritance.py`.

Also: the parallel hook on `POST /v1/routines` lives in `services/backend/src/backend_app/routines/service.py` — that file's owner is P5-A historically; **P6.5-A1** appends the routine-create inheritance hook to it (single-file owner for routines stays with the routines team conceptually; we make the targeted extension in P6.5-A1's branch). Both inheritance hooks land via the matching backend or ai-backend agent.

Deliverables: chats endpoint inherits project allowlist; routines endpoint inherits project allowlist; audit rows carry `inherited_from_project_default: bool`.

**P6.5-B1 project UI (allowlist + templates) — `worktree-agent-phase6-5-b1-surface`**

Prereqs: P6.5-A1 wire contracts. SP-1 (`<PageHeader>`, `<FilterTabs>`, `<EmptyState>`, `<ItemLink>`).

Exclusive files:

- `packages/chat-surface/src/destinations/projects/ProjectEditor.tsx` — extend with `default_connector_allowlist` section (3-mode picker).
- `packages/chat-surface/src/destinations/project-templates/` (NEW): `TemplateGallery.tsx`, `TemplateEditor.tsx`, `TemplateCard.tsx`, `ForkDialog.tsx`, `index.ts`.
- `packages/chat-surface/src/shell/destinations.ts` — add `"project-templates"` slug (gated; not a top-level rail destination if product decides — see §12 Q1).
- `apps/frontend/src/api/projectTemplates.ts` (NEW).
- `apps/frontend/src/api/projects.ts` — extend with allowlist PATCH wrapper.
- Tests under `packages/chat-surface/src/destinations/{projects,project-templates}/__tests__/`.

Deliverables: allowlist UI in ProjectEditor; TemplateGallery; TemplateEditor; ForkDialog with member/connector overrides; "Save as template" CTA in Project detail's `[⋯]` menu.

**P6.5-B2 archive-block UI — `worktree-agent-phase6-5-b2-archive`**

Prereqs: P6.5-A1 (liveness wire + 409 contract).

Exclusive files:

- `packages/chat-surface/src/destinations/projects/ArchiveDialog.tsx` — extend to handle 409 response and render `LivenessReport` (item counts + `<ItemLink>` rows + "Refresh status" button).
- `packages/chat-surface/src/destinations/projects/LivenessReportPanel.tsx` (NEW) — reusable component; renders any `LivenessReport`.
- Tests for the 409 render path.

Deliverables: archive dialog renders 409 detail; "Refresh status" re-calls archive with force-refresh; partial-failure yellow banner.

**P6.5-C1 context-aware chat wiring — `worktree-agent-phase6-5-c1-chat-context`**

Prereqs: Phase 1 chat-create flow as-is.

Exclusive files:

- `apps/frontend/src/features/chat/ChatScreen.tsx` — read route's project_id and pass through to `createConversation`.
- `apps/frontend/src/features/chat/components/composer/ComposerFiledUnderChip.tsx` (NEW) — the `[Filed under: ▾]` composer footer chip.
- `apps/frontend/src/features/chat/composerProjectContext.ts` (NEW) — route-to-default-project_id resolver per §4.2 rules.
- Tests for the 5 routes in §10.2.

Deliverables: composer respects route context; chip always visible; chip's override sticks per chat for as long as composer is open (resets on navigation).

**P6.5-C2 template-gallery wiring — `worktree-agent-phase6-5-c2-template-routes`**

Prereqs: P6.5-B1 components.

Exclusive files:

- `apps/frontend/src/app/App.tsx` — extend destination dispatch switch (append `"project-templates"` case).
- `apps/frontend/src/app/routes.ts` — extend route table with `/project-templates`, `/project-templates/:id`, `/project-templates/:id/edit`.
- `apps/frontend/src/features/project-templates/` (NEW) — composition layer wiring TemplateGallery + ForkDialog into App routes.

Deliverables: TemplateGallery accessible at `/project-templates`; fork-from-template navigates to new project on success; deep-link routing handled.

### 11.2 Merge order (strict)

1. Phase 6 (Projects core) → main (prerequisite; not in 6.5 scope).
2. **P6.5-A1** → main. Lands liveness module, allowlist column, archive 409, project_templates, audit + retention.
3. **P6.5-A2** → main. Rebases on A1's wire contracts; adds ai-backend conversation hook.
4. **P6.5-B1** + **P6.5-B2** → main, in either order (different file owners; B1 owns the projects/project-templates surface, B2 owns ArchiveDialog).
5. **P6.5-C1** → main. Rebases on chat-screen mainline; tiny diff.
6. **P6.5-C2** → main. Rebases on B1's components + main App.tsx.

All six can be in flight in parallel. The strict ordering is A1 first (everyone else consumes it), then the rest in their natural order.

### 11.3 Acceptance criteria (gate to closing Phase 6.5)

- ✅ Liveness module ≤ 250 LOC; one public method; no business logic beyond aggregation.
- ✅ Every test in §10 green across backend / ai-backend / frontend.
- ✅ Archive 409 with full `LivenessReport`; UI renders detail with working `<ItemLink>` rows.
- ✅ Save-as-template + fork round-trips with snapshot integrity.
- ✅ Atomic fork: injected fault rolls back every inserted row.
- ✅ Tenant isolation tests pass for liveness, allowlist, templates.
- ✅ Audit chain exports include every new action from §9.
- ✅ Frontend typecheck + chat-surface tests + backend tests + ai-backend tests green.
- ✅ axe-core green on ArchiveDialog, ProjectEditor (extended), TemplateGallery, TemplateEditor, ForkDialog.
- ✅ No god-class — code review confirms LivenessService has only the public `is_project_alive` method (helpers private; no business logic).
- ✅ DRY confirmed — grep for "is project alive" / "count_active_runs" across the repo returns matches only inside the liveness module + the documented call sites.

---

## §12 Open product questions

These need a call before P6.5-A1 / B1 / C2 code the affected branch.

1. **TemplateGallery as a top-level destination, or hidden under Projects?** Recommend **hidden under Projects** — accessible from a sub-route + Project detail `[⋯]` menu, but NOT a 13th rail destination. Reason: most users will never author a template; the gallery is a power-user surface. ProjectsPanel can include a "Templates" link in its footer. Confirm.
2. **Default-connector-allowlist hard-constraint mode.** §5.5 ships only default-mode (allowlist applies at create-time; user can override per-chat). Should Phase 6.5 also ship hard-constraint mode (allowlist enforced at fire-time, per-chat overrides forbidden)? Recommend **no — defer to Phase 7+** when compliance auditors ask for it. Confirm.
3. **Liveness cache TTL.** Recommend 2s (per task spec). For very large tenants (10k+ projects), single-flight may be worth adding to prevent thundering-herd on cache expiry. Recommend punt to a follow-up if metrics show a hot spot; don't pre-optimize. Confirm.
4. **Force-refresh on routine pre-fire-validation.** §3.5 recommends `force_refresh=True` only on operator-triggered routine activation (not on every scheduler tick). Confirm that the scheduler relies on its own `next_fire_at` machinery and does NOT consult liveness per tick.
5. **Inbox "in-flight" definition.** §3.3 says `unread + snoozed`. Should `pending` (a routed-but-not-yet-read auto-item) count? Recommend **yes** (matches the spirit of "is anything pending the user's attention"). Confirm.
6. **Cross-tenant template marketplace.** Recommend **out of scope indefinitely** (security boundary; aligns with routines no-cross-tenant rule). Confirm.
7. **Template versioning.** §7.5 says snapshots are immutable; users duplicate templates to evolve them. Should we ship light versioning (template id immutable, but version_n auto-increments on save-as-template-from-same-source)? Recommend **no — duplicate-only** is simpler and traceable via the audit chain. Confirm.
8. **Forking a template into a project with the same name as the source.** Recommend allowing it — duplicates are common, audit chain disambiguates. Confirm.
9. **Allowlist inheritance for inline chat composer (no project route).** When the user is in a chat at `/chats/<id>` and that chat has `project_id=X`, do we re-apply X's allowlist on each new message? Recommend **no** — allowlist applies at chat create only, per §5.4. Existing chats keep their connector set. Confirm.
10. **Auto-fork suggestion.** "We noticed you're creating projects with similar names — save as template?" Wave 7+. Confirm out-of-scope.
11. **Soft-archive race window.** §3.8 documented the trade-off (orphaned runs land in archived projects on ai-backend outage). Recommend accept; flagging here in case product wants a 5xx fail-closed posture instead. Confirm.
12. **Template seeded-todos relative-due semantics.** §7.2 has `relative_due_days` from fork time. Should fork-time-noon be the anchor, or fork-time exactly? Recommend **fork-time exactly + user-tz-end-of-day for the due display** (matches existing todos UX). Confirm.

---

## §13 Anti-goals

Restated as testable invariants:

- ❌ **NOT a god class.** LivenessService has one public method; helpers private; no business policy. CI-checked by file-LOC + method-count rule.
- ❌ **NOT a new service.** Liveness lives as a module inside `backend`, not as `services/liveness/`. No new venv/Dockerfile/deploy.
- ❌ **NOT a write path.** Liveness reads only. The only write is the optional cache-miss audit (flag-gated).
- ❌ **NOT a polling endpoint for the UI.** No app-facing `/v1/liveness/...` route. Liveness ships only embedded in 409 / template-fork pre-validation contexts.
- ❌ **NOT a fail-closed gate when upstream errors.** Documented trade-off in §3.8.
- ❌ **NO duplicate "is alive" logic anywhere else.** Code review enforces; grep guard in CI is a follow-up if drift recurs.
- ❌ **NO cross-tenant templates.** Security boundary.
- ❌ **NO half-forked projects.** Single DB transaction or rollback.
- ❌ **NO snapshot mutation.** Templates duplicate to evolve; snapshot is immutable.
- ❌ **NO force-archive override.** No `?force=true`, no admin escape hatch. Aligns with cross-audit §9.7 Q12.
- ❌ **NO PII in the LivenessReport.** Only counts + per-source booleans/errors. No usernames, no titles.
- ❌ **NO frontend-only ACL.** Allowlist inheritance is server-validated; template ACL is server-validated; archive 409 is server-decided.
- ❌ **NO webhook/event triggers in templates.** Strip on save-as-template; never restore on fork.

---

## §14 References

- [projects-prd.md](projects-prd.md) — Phase 6 sub-PRD (foundation this PRD extends).
- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas.
- [destinations-master-prd.md](../destinations-master-prd.md) — §5.4 Projects.
- [cross-audit.md](../cross-audit.md) — binding decisions: §1.1 ItemRef, §1.2 ports, §1.3 project-scoped ACL, §1.4 audit context, §1.5 filter OR, §1.6 PageHeader, §2.1 branded IDs, §5.3 cascade default, §9.6 Todos Q6 (context-aware precedent), §9.7 Routines Q12 (no force-override precedent).
- [destinations/routines-prd.md](routines-prd.md) — for cross-destination connector-scope patterns (§3.8) and for the routine status enum consumed by `LivenessService`.
- [destinations/todos-prd.md](todos-prd.md) — §6 / cross-audit §9.6 context-aware project default; the precedent for §4 of this PRD.
- [destinations/inbox-prd.md](inbox-prd.md) — for "in-flight inbox" semantics consumed by `LivenessService`.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — chat-create contract that §4 wires; conversation-source audit shape that §9 extends.
- [implementation-plan.md](../implementation-plan.md) — §2 Phase 6.5 dispatch table (to be amended on merge to enumerate the 6 agents in §11), §4 merge order, §6 anti-conflict file rules.
- `services/backend/src/backend_app/projects/` — Phase 6 module this PRD extends.
- `services/backend/src/backend_app/routines/service.py` — routine status read consumed by `LivenessService`; route also gains the allowlist inheritance hook per §5.4.
- `services/ai-backend/src/runtime_api/http/routes.py` — runs + approvals list endpoints consumed by `LivenessService.ai_backend_client`.
- `services/ai-backend/src/agent_runtime/api/conversation_coordinator.py` (or equivalent) — chat-create hook for allowlist inheritance per §5.4.
- `packages/audit-chain` — audit writer (cross-audit §1.4 `context` field).
- Root [`CLAUDE.md`](../../../CLAUDE.md) — compliance section (audit immutability, tenant isolation, untrusted-input rules).
- [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md) · [`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) · [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md) · [`apps/frontend/CLAUDE.md`](../../../apps/frontend/CLAUDE.md) · [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md).
