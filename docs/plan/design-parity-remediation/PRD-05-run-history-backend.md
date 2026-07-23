# PRD-05 — Run history: an org-scoped run list so Activity can show finished runs

> **Wave 0** (concurrent with PRD-01 and PRD-06; disjoint file sets — see the program
> [README](README.md) "Parallelisation plan"). No PRD must land before this one.
> This PRD **must land before PRD-09** (README C19): PRD-09 D4 makes `update_run_status`
> bump the conversation's `updated_at`, which reorders every `updated_at`-sorted list
> including today's Activity spine. Moving Activity off that spine first makes the reorder
> land once.

## Problem

Open Activity. The design promises "every action the agent has taken, most recent first" — three day-groups, eight runs, one of them live. What you actually get is: the runs that are _in flight right now_, and nothing else. Finish a run and its row does not move to "done" — it **disappears**. Come back tomorrow and Activity says **"No activity yet"** to a user who has run the agent fifty times.

The empty state is not a bug in the empty state. It is the truth about the data path: Activity has no run history to read. It reads the conversation list, and the conversation list only ever carries a run id + status when the run is **non-terminal**. Every one of the design's seven finished rows is structurally unreachable — not styled wrong, not missing a field, _unreachable_.

Two second-order lies follow. The live-run dot stops discriminating, because with this spine nearly every row that renders is running. And a frontend test asserts a server response shape (`latest_run_status: "completed"`) that **no store adapter in the repo can emit** — a green test encoding a contract that does not exist.

This PRD builds the missing capability: a real, paginated, newest-first run list, keyed on runs rather than conversations, that serves all eight run statuses.

## Evidence

Every row opened and verified in this worktree at `claude/design-parity-audit-7ec82a`.

| Claim                                                                          | File:line                                                                                                                            | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Web binder drops rows lacking `latest_run_id` **and** `latest_run_status`      | `apps/frontend/src/features/activity/api/activityApi.ts:151-161`                                                                     | CONFIRMED. `if (runId === null \|\| undefined \|\| "" \|\| status === null \|\| undefined) continue;`. Audit cited 150-162; actual span is 151-161.                                                                                                                                                                                                                                                                                                                     |
| Desktop binder does the same, verbatim                                         | `apps/desktop/renderer/destinationBinders.tsx:299-308`                                                                               | CONFIRMED. Byte-identical skip rule with the comment `// never-ran conversation is a chat, not a run`. Audit cited 298-306; actual 299-308.                                                                                                                                                                                                                                                                                                                             |
| The server populates those two fields only from the **active**-run query       | `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:434,441-444`                                                | CONFIRMED. `_with_latest_run` calls `self._persistence.get_active_run_for_conversation(...)` and returns the response unchanged when it is `None`. Audit cited `:441` for the call; the method opens at `:434`.                                                                                                                                                                                                                                                         |
| Postgres adapter filters to non-terminal statuses                              | `services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py:1347-1370`                                                   | CONFIRMED. `AND status IN ('queued','running','waiting_for_approval','cancelling') ORDER BY created_at DESC LIMIT 1`. Audit's `~1347-1369` is right.                                                                                                                                                                                                                                                                                                                    |
| File adapter filters identically                                               | `services/ai-backend/src/runtime_adapters/file/runtime_api_store.py:1480-1498`                                                       | CONFIRMED. `non_terminal = {QUEUED, RUNNING, WAITING_FOR_APPROVAL, CANCELLING}` over `self.runs.values()`.                                                                                                                                                                                                                                                                                                                                                              |
| In-memory adapter filters identically                                          | `services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py:658-681`                                                    | CONFIRMED. Same four-status set. All three adapters agree; there is no adapter through which a finished run reaches Activity.                                                                                                                                                                                                                                                                                                                                           |
| `packages/api-types` states it in prose                                        | `packages/api-types/src/index.ts:519-536`                                                                                            | CONFIRMED. `latest_run_status?: AgentRunStatus \| null` (:526) documented as "most recent run's status"; `latest_run_id_any_status?` (:536) documented as surviving completion "Unlike `latest_run_id` (a non-terminal active run only; `null` once the run completes)". The asymmetry is declared, not accidental.                                                                                                                                                     |
| `latest_run_id_any_status` has no status twin and IS consumed                  | `packages/chat-surface/src/destinations/run/useRunSession.ts:253`                                                                    | CONFIRMED. `conv.latest_run_id ?? conv.latest_run_id_any_status ?? null` — the Run cockpit rebinds a finished conversation by id, with no status available.                                                                                                                                                                                                                                                                                                             |
| Rows are one-per-CONVERSATION, not one-per-RUN                                 | `activityApi.ts:150-176`; `destinationBinders.tsx:292-321`                                                                           | CONFIRMED. Both loop `for (const conversation of conversations)` and push at most one `ActivityRunRow` each. A conversation with 5 runs yields 1 row.                                                                                                                                                                                                                                                                                                                   |
| An all-status, newest-first run list already exists, per-conversation          | `services/backend-facade/src/backend_facade/app.py:468-482`                                                                          | CONFIRMED. `GET /v1/agent/conversations/{conversation_id}/runs`, `limit` clamped `ge=1, le=200`, forwarded to ai-backend.                                                                                                                                                                                                                                                                                                                                               |
| …and its service + adapter                                                     | `conversation_query_service.py:262-301`; `postgres/runtime_api_store.py:1399-1420`                                                   | CONFIRMED. `list_runs_for_conversation` → `SELECT * FROM agent_runs WHERE org_id=%s AND conversation_id=%s ORDER BY created_at DESC LIMIT %s`. No status filter. This is the proven query shape to generalize.                                                                                                                                                                                                                                                          |
| Nothing in Activity calls it                                                   | grep `list_runs_for_conversation` / `/runs` in `apps/frontend/src/features/activity`, `apps/desktop/renderer/destinationBinders.tsx` | CONFIRMED. Zero hits. Both Activity binders call only `listConversations` + `listAuditEvents` (`activityApi.ts:197-212`).                                                                                                                                                                                                                                                                                                                                               |
| The false-contract test                                                        | `apps/frontend/src/features/activity/ActivityRoute.test.tsx:93-94`                                                                   | CONFIRMED. `latest_run_id: "run_default", latest_run_status: "completed"`. Also at `:222-223`, `:306-307`, `:313-314`. Desktop twin at `apps/desktop/renderer/destinationBinders.test.tsx:311`. No adapter can emit `completed` in that field.                                                                                                                                                                                                                          |
| **NEW — runs carry no title**                                                  | `services/ai-backend/src/runtime_api/schemas/runs.py:341-361`                                                                        | `RunRecord` has `run_id, conversation_id, org_id, user_id, user_message_id, trace_id, status, model_provider, model_name, created_at, started_at, completed_at, cancelled_at, safe_error, latest_sequence_no` — **no title**. Today's row title comes from `conversation.title` (`activityApi.ts:170-172`). A run list must join conversations. Audit did not mention this.                                                                                             |
| **NEW — no index serves an org+user run keyset**                               | `services/ai-backend/migrations/0001_runtime_baseline.sql:925-929`                                                                   | Existing: `idx_agent_runs_idempotency`, `idx_agent_runs_org_conversation_created (org_id, conversation_id, created_at)`, `idx_agent_runs_org_status_started (org_id, status, started_at)`. Neither leads with `(org_id, user_id, created_at)`. A migration is required.                                                                                                                                                                                                 |
| **NEW — RLS already isolates the table**                                       | `migrations/0001_runtime_baseline.sql:1242`                                                                                          | `CREATE POLICY tenant_isolation ON agent_runs USING (org_id = current_setting('app.current_org_id', true))`. The adapter's `_tenant_connection(org_id=...)` binds it. Tenant isolation is defence-in-depth already present.                                                                                                                                                                                                                                             |
| **NEW — conversation soft-delete does not touch runs**                         | `postgres/runtime_api_store.py:996-1020`                                                                                             | `soft_delete_conversation` stamps `deleted_at` on `agent_conversations` only. A run list keyed on `agent_runs` would resurrect deleted conversations' runs unless it filters on the joined conversation.                                                                                                                                                                                                                                                                |
| **NEW — `DELETE /v1/agent/history` never tombstones conversations**            | `postgres/runtime_api_store.py:2308-2340`                                                                                            | It sets `status='archived'`, tombstones messages, cancels non-terminal runs — and leaves `deleted_at` NULL and run rows intact. Today's Activity passes `includeArchived: true` (`activityApi.ts:198-200`), so after "delete my history" a run list would still show every run + its conversation title.                                                                                                                                                                |
| **NEW — runs are not a retention kind**                                        | `services/ai-backend/src/agent_runtime/persistence/records/retention.py:36-43`                                                       | `RetentionKind` = messages, events, context_payloads, checkpoints, memory_items (+ 3 `*_TOMBSTONED`). No `RUNS`. Run rows are never swept by TTL.                                                                                                                                                                                                                                                                                                                       |
| **NEW — `DELETE /v1/agent/history` has no shipped client**                     | grep `agent/history` in `apps/frontend/src`, `apps/desktop`, `packages/`                                                             | Zero hits. Facade route exists (`app.py:1148-1161`), ai-backend handler exists (`runtime_api/http/routes.py:499-509`). The deletion gap above is real but currently unreachable from the UI.                                                                                                                                                                                                                                                                            |
| **NEW — the collection URL is POST-only, which is the known 405**              | `backend-facade/app.py:929`; `runtime_api/http/routes.py:630-636`                                                                    | `@app.post("/v1/agent/runs")` / `router.add_api_route("/runs", create_run, methods=["POST"])`. There is no GET. This is exactly the 405 the desktop client previously hit.                                                                                                                                                                                                                                                                                              |
| **NEW — `MessageCursor` is file-local and generalizable**                      | `conversation_query_service.py:40-80`, used at `:238,:251`                                                                           | grep across `src/` + `tests/` returns hits only inside `conversation_query_service.py`. Base64url over `f"{created_at.isoformat()}                                                                                                                                                                                                                                                                                                                                      | {id}"`, tolerant decode returning `None` on garbage. Safe to rename/generalize. |
| **NEW — the port-conformance harness already parametrizes all three adapters** | `services/ai-backend/tests/unit/runtime_adapters/test_store_conformance.py:38-56`                                                    | `@pytest.fixture(params=["in_memory","file", pytest.param("postgres", marks=pytest.mark.postgres)])`. This is where "all three stay in sync" is mechanically enforced.                                                                                                                                                                                                                                                                                                  |
| **DISPUTED (partially) — `paused` is "absent from `AgentRunStatus`"**          | `services/ai-backend/src/runtime_api/schemas/common.py:34-44`                                                                        | The audit is right that there is no `paused` member (`queued, running, waiting_for_approval, cancelling, cancelled, completed, failed, timed_out`). But the audit implies Activity therefore never shows `paused`; the stronger truth is that **no client maps to `"paused"` either** — `mapRunStatus` (`activityApi.ts:56-71`, `destinationBinders.tsx:242-258`) folds `waiting_for_approval → needs_input`, never `paused`. `paused` is unreachable at _both_ layers. |

Design-parity measurement for context: `tools/design-parity/surfaces/activity/out/report-default.md:8` currently reports **HIGH 19 · MEDIUM 52 · LOW 68 · INFO 11** (re-read on disk; an earlier draft of this PRD said 20 HIGH, from a report generated before `lib/compare.mjs` stopped emitting phantom `borderColor` rows on borderless elements). Treat this as a moving snapshot, not a frozen number — **no DoD item in this PRD gates on it**. **None of those rows is this defect** — computed-style diffing cannot see an unreachable row. The evidence for this PRD is the trace above plus `tools/design-parity/surfaces/activity/out/AUDIT.md` Part 2, ACT-04/07/08/09/18.

## Design intent

The design's Activity surface is a **history**, not a live monitor. From `tools/design-parity/design-kit/app-v3/`:

**Lead copy** (`copilot-app.jsx:26-28`) — the promise this PRD makes true:

> "Everything the agent has done, most recent first. This is the record the old build buried in an 'audit log' — here it's a place you visit."

**The fixture** (`copilot-data.jsx:600-645`) is the numeric spec:

| Property                       | Design value                                                       |
| ------------------------------ | ------------------------------------------------------------------ |
| Rows                           | **8**                                                              |
| Day groups                     | **3** — `"Today"` (3 rows), `"Yesterday"` (3), `"Mon, Jul 14"` (2) |
| `status: "running"`            | **1** row (`:608`)                                                 |
| `status: "done"`               | **5** rows (`:614, :620, :637, :644, …`)                           |
| `status: "paused"`             | **1** row (`:631`)                                                 |
| `status: "stopped"`            | **1** row                                                          |
| Rows the server can emit today | **1 of 8** — the running one                                       |

**Status chips** (`copilot.css:575-605`) — the design ships four tones, all four populated in the fixture: `.chip` = `font-family: var(--mono); font-size: 10.5px; border: 1px solid var(--line2); background: transparent; border-radius: 999px; padding: 2px 8px`; `.chip--ok { color: var(--jade); border-color: rgba(87,199,133,.25) }` (`:591-594`), `.chip--warn { color: var(--amber); border-color: rgba(232,180,94,.25) }` (`:599-602`), `.chip--off { color: var(--mut2) }` (`:603-605`). Three of those four tones are currently dead code for lack of data, not for lack of CSS.

**Row time** (`copilot.css:1655-1660`): `.lrow__time { font-family: var(--mono); font-size: 10.5px; color: var(--mut2); flex: none }`, fed wall-clock strings `"11:44"`, `"09:02"`, `"18:30"` (`copilot-data.jsx:607, 613, 630`). Whatever the client renders, the wire must carry a **per-run** instant; today it carries `conversation.updated_at` (`activityApi.ts:174`).

**Day dividers** (`copilot.css:1683-1691`): `.act-day { font-family: var(--mono); font-size: 10px; color: var(--mut2); margin: 18px 0 8px }`. Three groups spanning three calendar days is only possible with multi-day history on the wire.

**Live affordance** (`copilot-app.jsx:79`): `{isLive ? <Icon.chevR /> : <span style={{width:16}} />}` — the chevron exists **because most rows are not live**. With the current spine the ratio inverts and the affordance stops meaning anything.

This PRD delivers the data. The type scale is **PRD-01**, the chip recipe **PRD-02**, and the
`Row` chevron/trailing slot + day-divider styling **PRD-08** (which owns
`packages/chat-surface/src/destinations/_shared/Row.tsx`, README C9). See Dependencies.

## Architectural decision

### The seam: a run-keyed read path, not a richer conversation projection

**`GET /v1/agent/runs` becomes a real collection read.** The list is keyed on `agent_runs`, ordered by `(created_at DESC, id DESC)`, keyset-paginated, joined to `agent_conversations` for the title. It is a new method on `PersistencePort`, implemented in all three adapters, exposed by `ConversationQueryService`, routed by `runtime_api`, proxied by the facade, and typed in `packages/api-types`.

Why this seam and not the four alternatives:

| Rejected                                                                                                         | Why                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Add `latest_run_status_any_status` next to `latest_run_id_any_status`** (`index.ts:536`)                       | Cheapest, and wrong. It leaves the row cardinality at one-per-conversation, so a conversation with five runs still shows one Activity row and the design's per-run titles/times/statuses stay unreachable. It also grows a second parallel "latest run" projection on a hot list endpoint that already does N+1 per-conversation lookups (`conversation_query_service.py:203-204`). Adding a flag to a wrong abstraction.                                                  |
| **Have the client fan out `GET /v1/agent/conversations/{id}/runs` per conversation**                             | 50 requests per Activity load, no global ordering, no cursor, and the client would re-derive newest-first across pages. Pushes a server join into two host binders.                                                                                                                                                                                                                                                                                                        |
| **Build `GET /v1/activity` in the facade** (the composite the binder comments name, `activityApi.ts:7,15,80-81`) | The facade must not own product projection (`services/backend-facade/CLAUDE.md`; "don't put AI orchestration in backend-facade"). A composite endpoint that fuses runs + audit counters is a _later_ layer — and it needs this run list underneath it either way. Building the composite first would bake the meta-counter blockers (**PRD-08**, which owns run meta counters and the `runtime_tool_invocations` writer) into the critical path of showing a finished run. |
| **Denormalize `title` onto `agent_runs`**                                                                        | Write amplification on every run insert, and stale titles after a conversation rename. The join is a primary-key nested loop bounded by `LIMIT n+1`.                                                                                                                                                                                                                                                                                                                       |

### Contract

**ai-backend route.** `runtime_api/http/routes.py` — add to `RuntimeApiRouter.create_router()` next to the existing `/runs` POST (`:630-636`).

**Registration order is load-bearing.** FastAPI matches in registration order and `run_id` is an unconstrained `str`. Verified on disk: `"/runs"` is registered at `routes.py:634` and `"/runs/{run_id}"` at `:641`. The new literal `GET /runs` must be registered **before** `:641`, in ai-backend _and_ in the facade (`backend-facade/app.py:929` POST `/v1/agent/runs`; `:1054` GET `/v1/agent/runs/{run_id}`). PRD-12 adds a second literal (`/runs/active_count`) under the same rule — whichever lands second must insert above the `{run_id}` block, not append.

```
GET /v1/agent/runs
  query: limit    int   = 50, ge=1, le=200        (clamped again in the service)
         cursor   str?  = None                     (opaque; malformed → treated as no cursor)
         org_id   str?  / user_id str?             (non-service path only; see authorization)
  200  RunHistoryResponse
  400  org_id and user_id are required             (scoped_identity, routes.py:555-558)
  403  missing runtime:use scope                   (router-level RequireScopes, routes.py:572-576)
```

```python
class RunHistoryEntry(RuntimeContract):
    run_id: str
    conversation_id: str
    conversation_title: str | None      # joined; None when the conversation has no title
    status: AgentRunStatus              # the raw 8-value runtime enum
    model_name: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None

class RunHistoryResponse(RuntimeContract):
    runs: tuple[RunHistoryEntry, ...]
    next_cursor: str | None
    has_more: bool
```

Declared in `services/ai-backend/src/runtime_api/schemas/runs.py` beside `RunSummaryResponse` (`:432-452`). Route name constant `LIST_RUN_HISTORY = "list_run_history"` in `agent_runtime/api/constants.py` (`Keys.RouteName`, alongside `GET_CONVERSATION_RUNS` at `:133`).

**The status projection stays raw on the wire.** `RunHistoryEntry.status` is `AgentRunStatus`, not the UI's `ActivityRunStatus`. `"done"`, `"needs_input"`, and `"stopped"` are _product vocabulary_; the runtime must not encode them. The 8→5 fold (`activityApi.ts:56-71`) stays client-side and is de-duplicated into `packages/chat-surface/src/destinations/activity/activityProjection.ts` under **PRD-04** (README C7 — PRD-04 owns the shared Activity projection; earlier drafts of this PRD said "PRD-06", which is connector access mode). This PRD's obligation is only that the fold be **total**: a test enumerates the exported `AGENT_RUN_STATUSES` tuple (`packages/api-types/src/index.ts:219-228`, verified 8 members) and asserts each maps to a member of `ACTIVITY_RUN_STATUSES` (`packages/api-types/src/activity.ts:45-52`).

**Cursor.** Generalize the existing codec rather than adding a second one. Rename `MessageCursor` → `KeysetCursor` in `conversation_query_service.py:40-80` (file-local; four internal references at `:59, :76, :238, :251`), keeping the exact `base64url("{iso8601}|{id}")` encoding and the tolerant `decode` that returns `None` for garbage. The run list encodes `(created_at, run_id)` of the **oldest row in the returned page**; the next request returns strictly-older rows. `next_cursor` is `None` when `has_more` is false. `has_more` is computed by fetching `limit + 1` and truncating — not by `len(records) == limit`, which is the ambiguous form `list_conversations` uses (`conversation_query_service.py:208`) and which reports a spurious extra page on an exact-multiple boundary.

**Pagination is ordered by `created_at`, not `started_at`.** `started_at` is nullable (`runs.py:357`) — a queued run has none — so it cannot be a keyset key. `created_at` is NOT NULL (`migrations/0001:96`). Clients render row time as `started_at ?? created_at`.

**Migration — id `0002`, assigned by the program README (C18).** Verified on disk in this
worktree: `services/ai-backend/migrations/` contains only `0001_runtime_baseline.sql`
(+ rollback) and `MANIFEST.lock`, so `0002` is the next free id; `services/backend/`'s
high-water mark is `0045_provider_api_keys_custom_endpoint.sql` and is untouched by this
PRD. PRD-07 takes ai-backend `0003`, PRD-09 takes `0004` — do not renumber.

`services/ai-backend/migrations/0002_run_history_index.sql` (+ `.rollback.sql`). Regenerate
the manifest **in the same commit** with `python3 tools/check_migration_manifest.py --write`
run from the **repo root** — the tool lives at the repo root, not under `services/ai-backend/`
(verified: `tools/check_migration_manifest.py:26-32` resolves `REPO_ROOT` from its own path
and walks both service migration dirs). CI refuses on checksum drift.

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_runs_org_user_created
    ON agent_runs USING btree (org_id, user_id, created_at DESC, id DESC);
```

Required because neither existing index leads with `(org_id, user_id)` (`0001:925-929`). `CONCURRENTLY` means the migration must not run inside a transaction block — follow the existing runner's convention or split the file.

**Postgres query** (`runtime_adapters/postgres/runtime_api_store.py`, beside `list_runs_for_conversation` at `:1399`):

```sql
SELECT r.*, c.title AS conversation_title
  FROM agent_runs r
  JOIN agent_conversations c ON c.id = r.conversation_id AND c.org_id = r.org_id
 WHERE r.org_id  = %(org_id)s
   AND r.user_id = %(user_id)s
   AND c.deleted_at IS NULL
   AND (%(before_created_at)s IS NULL
        OR (r.created_at, r.id) < (%(before_created_at)s, %(before_run_id)s))
 ORDER BY r.created_at DESC, r.id DESC
 LIMIT %(limit_plus_one)s
```

Run under `_tenant_connection(org_id=...)` so the `tenant_isolation` RLS policy (`0001:1242`) also binds.

**File and in-memory adapters** scan `self.runs.values()` filtered on `org_id` + `user_id`, join `self.conversations` for `title` / `deleted_at`, sort by `(created_at, run_id)` descending, apply the keyset, and slice `limit + 1`. This matches how those adapters already answer every other run query (`file:1354-1361, 1372-1378`; `in_memory:527-534, 545-551`) — both hydrate all runs into a process dict, so an in-memory scan changes nothing asymptotically. Deliberately **no** change to the file store's SQLite catalog index (`file/_catalog_index.py:63-72`, whose `runs` table has no `user_id` column): adding a column to a disposable index that is wiped and repopulated by `rebuild` (`:204-218`) buys nothing at desktop-scale run counts. If a future profile needs it, that is an adapter-local optimization behind an unchanged port.

**Authorization rule.** The endpoint returns **the caller's own runs**: `WHERE org_id = ? AND user_id = ?`. Precisely:

1. Router-level `Depends(RequireScopes(RUNTIME_USE))` covers every `/v1/agent/*` route (`routes.py:572-576`).
2. `scoped_identity` (`routes.py:543-559`) takes the trusted service-token headers when present and **ignores query params**; otherwise it requires explicit `org_id` + `user_id` or 400s.
3. The facade never forwards a client-supplied tenant: `identity.scoped_params({...})` overrides `org_id`/`user_id` from the verified session, the same idiom `list_conversations` uses (`backend-facade/app.py:410-431`).
4. Postgres RLS `tenant_isolation` is defence-in-depth beneath all of the above.

The brief calls this "org-scoped". It is org-scoped in the sense that `org_id` is the leading predicate, the RLS key, and the index's leading column — but the **authorization** predicate is `(org_id, user_id)`, matching `list_conversations` (`postgres:707`). A genuinely org-wide "everyone's runs" view would leak other users' conversation titles in a team deployment and is a separate, admin-scoped feature (non-goal).

### Deletion, retention, and audit

Not assumed — three concrete obligations, each with a conformance test:

1. **Conversation soft-delete must hide its runs.** `soft_delete_conversation` (`postgres:996-1020`) stamps only the conversation, so the `c.deleted_at IS NULL` join predicate above is load-bearing, not defensive. Archived conversations **remain visible** — Activity is a history, archiving is an organizational act, and today's binder already passes `include_archived: true` (`activityApi.ts:198-200`).

2. **`DELETE /v1/agent/history` must actually clear the history.** Today it archives conversations and cancels in-flight runs but leaves `deleted_at` NULL (`postgres:2308-2340`), so this new endpoint would keep serving every run title after a user asked for deletion. Fix at the source in all three adapters: `delete_user_history` stamps `deleted_at = COALESCE(deleted_at, now)` alongside `status = 'archived'`, which both hides the runs and lets the existing C8 tombstone sweeper reap the rows. Rejected alternative — "exclude runs of _archived_ conversations from the run list" — because it would also hide history the user archived deliberately and still wants to browse. The existing legal-hold 409 (`postgres:2301-2307`) is unchanged and still gates the whole operation.

3. **Retention: runs are metadata, and this PRD does not make them TTL-swept.** `RetentionKind` has no `RUNS` member (`records/retention.py:36-43`). A run row can therefore outlive its messages and events, which _are_ swept. That is acceptable and must be stated rather than glossed: the run list carries id, status, model, timestamps, and a joined title — the run's _content_ lives in messages/events and is already governed. Adding `RetentionKind.RUNS` is a deliberate non-goal (it needs a policy UI, a chunked sweeper, and deletion evidence rows).

4. **Audit.** Listing is a read. `GET /v1/agent/conversations` writes no audit row today (`conversation_query_service.py:178-209`), and this route matches that posture — a read-access audit trail for run metadata is a deployment-wide decision, not one this endpoint should make unilaterally. Stated explicitly so the omission is a recorded decision, not an oversight. The route reads only rows already scoped to the caller, so it opens no new cross-principal read surface.

### Killing the false-contract test architecturally

`ActivityRoute.test.tsx:94` asserts `latest_run_status: "completed"`. Editing that string is a bandaid — the next fixture will re-introduce it. The fix makes the lie **not compile**: narrow the field's type to exactly what the server can emit.

In `packages/api-types/src/index.ts`, beside the `Conversation` declaration:

```ts
/**
 * The only statuses `latest_run_status` can carry. The field is projected
 * from `get_active_run_for_conversation`, which filters to non-terminal
 * runs in all three store adapters — a terminal value here is a fiction.
 * Finished runs come from `GET /v1/agent/runs` (RunHistoryEntry.status).
 */
export const ACTIVE_AGENT_RUN_STATUSES = [
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
] as const;
export type ActiveAgentRunStatus = (typeof ACTIVE_AGENT_RUN_STATUSES)[number];
```

and change `latest_run_status?: AgentRunStatus | null` (`index.ts:526`, verified) to `latest_run_status?: ActiveAgentRunStatus | null`. `RunHistoryEntry.status` keeps the full `AgentRunStatus` union.

**CODE-WINS correction (this PRD's earlier draft was wrong).** The narrowing does _not_ break both fixtures by itself:

- **Web** — `apps/frontend/src/features/activity/ActivityRoute.test.tsx:94` is a plain object literal assigned to a `Conversation`-typed factory, and `apps/frontend/tsconfig.json` includes `"src"`, so the test file **is** typechecked and the narrowing does break it. ✅
- **Desktop** — `apps/desktop/renderer/destinationBinders.test.tsx:306-312` builds the fixture and closes it with `} as unknown as Conversation;` (verified at `:312`). A double assertion defeats the narrowing entirely; `tsc` stays green with `"completed"` in place. **Deleting that `as unknown as` cast is therefore in scope**, not optional — without it the desktop half of this guard is a no-op. `apps/desktop/tsconfig.json` includes `renderer/**/*.tsx`, so once the cast is gone the file is typechecked by `npm run typecheck --workspace @0x-copilot/desktop`.

The guard is then made permanent by a committed type test rather than by a manual mutation (DoD 13).

The corresponding _positive_ guard lives on the server: a conformance test asserting a **completed** run is returned by the new list method on all three adapters — the exact scenario the frontend fixture was pretending about.

## Scope

### `services/ai-backend`

| File                                                        | Reason                                                                                                                                                  |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `migrations/0002_run_history_index.sql` (+ `.rollback.sql`) | `(org_id, user_id, created_at DESC, id DESC)` index on `agent_runs`.                                                                                    |
| `migrations/MANIFEST.lock`                                  | Regenerated checksum; CI refuses on drift.                                                                                                              |
| `src/runtime_api/schemas/runs.py`                           | `RunHistoryEntry` + `RunHistoryResponse` beside `RunSummaryResponse` (`:432-452`).                                                                      |
| `src/agent_runtime/api/ports.py`                            | `list_runs_for_org` on `PersistencePort`, beside `list_runs_for_conversation` (`:510-521`).                                                             |
| `src/agent_runtime/api/conversation_query_service.py`       | `MessageCursor` → `KeysetCursor`; new `list_run_history` service method.                                                                                |
| `src/agent_runtime/api/constants.py`                        | `Keys.RouteName.LIST_RUN_HISTORY`; default run-history limit.                                                                                           |
| `src/runtime_api/http/routes.py`                            | `RuntimeApiRoutes.list_run_history` + `GET /runs` registration.                                                                                         |
| `src/runtime_adapters/postgres/runtime_api_store.py`        | `list_runs_for_org` (join + keyset); `delete_user_history` also stamps `deleted_at`.                                                                    |
| `src/runtime_adapters/file/runtime_api_store.py`            | Same two changes, in-memory scan form.                                                                                                                  |
| `src/runtime_adapters/in_memory/runtime_api_store.py`       | Same two changes.                                                                                                                                       |
| `tests/unit/runtime_adapters/test_store_conformance.py`     | New `TestRunHistory` class — all-status, ordering, keyset, tenant/user isolation, deleted-conversation exclusion, post-`delete_user_history` emptiness. |
| `tests/unit/runtime_api/test_fastapi_runtime_api.py`        | Route-level: 200 shape, limit clamp, cursor round-trip, 400 without scope.                                                                              |
| `tests/unit/runtime_api/test_api_type_contracts.py`         | `RunHistoryEntry` field set matches the `packages/api-types` declaration.                                                                               |
| `tests/unit/runtime_adapters/postgres/`                     | DB-gated: new index is used (`EXPLAIN`), join excludes soft-deleted conversations.                                                                      |

### `services/backend-facade`

| File                                    | Reason                                                                                                                                                                                                    |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/backend_facade/app.py`             | `@app.get("/v1/agent/runs")` proxy with `identity.scoped_params({limit, cursor})`, registered **above** the `/v1/agent/runs/{run_id}` block (`:1054`).                                                    |
| `tests/test_public_route_contract.py`   | `"/v1/agent/runs"` is **already** in the required tuple (verified at `:22`) because `POST` exists. Strengthen it: assert the OpenAPI path object for `/v1/agent/runs` has **both** `get` and `post` keys. |
| `tests/test_tenant_isolation_facade.py` | Client-supplied `org_id` / `user_id` on the new route are overridden by the session.                                                                                                                      |

### `packages/api-types`

| File              | Reason                                                                                                                                                                                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/index.ts`    | `RunHistoryEntry`, `RunHistoryResponse`, `ACTIVE_AGENT_RUN_STATUSES` / `ActiveAgentRunStatus`; narrow `latest_run_status` (`:526`).                                                                                                                          |
| `src/activity.ts` | Comment-only: update the "There is no dedicated run-list endpoint yet" header (verified at `:10-13`) to point at `GET /v1/agent/runs`. Leave `ACTIVITY_RUN_STATUSES` (`:45-52`) and `ActivityRunRow` unchanged — **PRD-04** owns the projection (README C7). |

> **Hot-file note.** README's hot-file table orders `packages/api-types/src/activity.ts` as
> `04 → 05 → 08`, but the wave plan puts this PRD in Wave 0 and PRD-04 in Wave 1. The
> discrepancy is harmless here because PRD-05's only edit to that file is a header comment
> touching no declaration; PRD-04 and PRD-08 stack on top in wave order. `src/index.ts` is
> unambiguous: `05 → 07 → 09 → 12`, this PRD first.

### `apps/frontend` / `apps/desktop` (test-only in this PRD)

| File                                                                     | Reason                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/frontend/src/features/activity/ActivityRoute.test.tsx`             | Fixtures at `:93-94, :222-223, :306-307, :313-314` no longer typecheck; replace with emittable active statuses and move finished-run coverage to the new contract. Extend the existing `describe("mapRunStatus")` block (`:197-213`) with the totality assertion (DoD 16). |
| `apps/frontend/src/features/activity/latestRunStatus.type-test.ts` (new) | Committed `@ts-expect-error` type test that pins the narrowing permanently (DoD 13). Typechecked because `apps/frontend/tsconfig.json` includes `"src"`.                                                                                                                   |
| `apps/desktop/renderer/destinationBinders.test.tsx`                      | Fixture at `:306-312`: replace `latest_run_status: "completed"` **and delete the `} as unknown as Conversation;` double assertion at `:312`** — without that deletion the narrowing is unenforced on desktop.                                                              |
| `apps/desktop/renderer/latestRunStatus.type-test.ts` (new)               | Desktop twin of the type test. Typechecked because `apps/desktop/tsconfig.json` includes `renderer/**/*.ts`.                                                                                                                                                               |

No production client file changes here. Cutting both hosts over to the new endpoint, and de-duplicating `mapRunStatus` / `projectActivityRows` into a shared projection, is **PRD-04**.

## Non-goals

- **Per-run meta counters.** `"4 apps · 7 steps · awaiting 1 approval"` (`copilot-data.jsx:606`) needs the audit `run_id` on the wire, a live `tool_name` emitter, and step/approval aggregates that exist nowhere. **PRD-08** owns run meta counters and the `runtime_tool_invocations` writer (README C21 — earlier drafts said "PRD-07", which is project data).
- **`GET /v1/activity`.** The runs+audit composite. It sits on top of this list; it is not this list.
- **Moving the host projection into `packages/chat-surface`** and cutting the binders over to the new endpoint. **PRD-04** (`destinations/activity/activityProjection.ts`, README C7).
- **The rail's in-flight run count.** `GET /v1/agent/runs/active_count`, `useActiveRunCount`, and the `railBadges` deletion are **PRD-12**'s in full (README C1). This PRD adds no count endpoint and no count field.
- **Any UI change.** `--font-size-2xs` split (PRD-01), chip recipe (PRD-02), `Row` trailing/chevron slot + day-divider casing + empty-state copy + row padding (PRD-08). Activity will still look wrong after this PRD; it will just have data.
- **A `paused` run status.** No `AgentRunStatus` member and no client mapping. Adding a real pause capability is a product feature, not a read-model change.
- **An org-wide / admin run view.** Requires a new scope, a new authorization rule, and a privacy decision about cross-user titles.
- **`RetentionKind.RUNS`.** Needs policy UI, chunked sweeper, and deletion-evidence rows.
- **Read-access audit logging** for list endpoints. Deployment-wide posture decision.
- **Pagination UI.** The wire carries `next_cursor` + `has_more`; `ActivityDestinationProps` (`packages/chat-surface/src/destinations/activity/ActivityDestination.tsx:217-247`) gains no "load more" prop here.

## Risks & rollback

| Risk                                                                                                                                                                                                       | Guard                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Rollback                                                                                                                                                                      |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Narrowing `latest_run_status` breaks an unrelated consumer.** `destinationBinders.tsx:151-159` switches on it with a `default`; `activityApi.ts:67` passes it to `mapRunStatus(status: AgentRunStatus)`. | `npm run typecheck` in `@0x-copilot/api-types`, `@0x-copilot/frontend`, `@0x-copilot/desktop`, `@0x-copilot/chat-surface` must all pass. A repo-wide grep for `latest_run_status` must show every consumer either narrowed or explicitly widened. **Note:** `as unknown as Conversation` double assertions silently opt out of the narrowing (one exists at `destinationBinders.test.tsx:312`); DoD 14's greps are the guard against re-introducing them. | Revert the one-line type change in `index.ts:526`; the endpoint is unaffected.                                                                                                |
| **`CREATE INDEX CONCURRENTLY` in a transactional migration runner fails.**                                                                                                                                 | `services/ai-backend/tests/` migration-application test; the DB-gated postgres suite.                                                                                                                                                                                                                                                                                                                                                                     | Ship the index non-concurrently for small deployments, or as a separate operational step.                                                                                     |
| **The join degrades on a user with many soft-deleted conversations** (keyset scan discards rows post-filter).                                                                                              | DB-gated `EXPLAIN` assertion that the new index is the driving scan.                                                                                                                                                                                                                                                                                                                                                                                      | Endpoint is additive; no existing path regresses. Cap `limit` (already `le=200`).                                                                                             |
| **`delete_user_history` now tombstones conversations**, changing Chats/list behaviour and making rows sweeper-eligible.                                                                                    | `test_store_conformance.py::test_soft_delete_hides_and_is_idempotent` (`:383`) and the existing `delete_user_history` tests in each adapter suite.                                                                                                                                                                                                                                                                                                        | Revert the `deleted_at` stamp; the run list then falls back to hiding nothing extra. **Note:** the route has no shipped client (grep: 0 hits), so blast radius today is zero. |
| **Three adapters drift.**                                                                                                                                                                                  | `test_store_conformance.py` runs the identical suite against `in_memory` + `file`; the `postgres` param is present-but-marked so the contract _names_ it (`:38-56`).                                                                                                                                                                                                                                                                                      | n/a — drift is caught at test time.                                                                                                                                           |
| **`MessageCursor` rename touches message pagination.**                                                                                                                                                     | `test_store_conformance.py::test_before_keyset_returns_strictly_older_page_ascending` (`:240`) and `test_sequence_is_contiguous_and_cursor_replayable` (`:267`).                                                                                                                                                                                                                                                                                          | Mechanical rename; revert is a rename back. grep confirms zero external importers.                                                                                            |
| **Facade route collides with `POST /v1/agent/runs`, or is shadowed by `/runs/{run_id}`.**                                                                                                                  | `test_public_route_contract.py` asserts both `get` and `post` on the path; `test_forwarder.py` covers proxy shape; DoD 8 asserts `GET /v1/agent/runs` returns 200 (not the `{run_id}` handler's 404) — `run_id` is an unconstrained `str`, so registration order decides.                                                                                                                                                                                 | Remove the GET registration; POST is untouched.                                                                                                                               |

**Clean revert:** drop the facade GET registration, the ai-backend route registration, and the `index.ts:526` narrowing. The port method, adapter implementations, schemas, and index can stay — they are inert without a route. Reverting the migration is `0002_run_history_index.rollback.sql` (`DROP INDEX CONCURRENTLY IF EXISTS idx_agent_runs_org_user_created`).

## Definition of Done

1. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py -k RunHistory` passes, and the new `TestRunHistory` class runs under the existing `params=["in_memory","file", postgres(marked)]` fixture at `test_store_conformance.py:38-56` — i.e. the same assertions execute against **all three** adapters.
2. **Regression guard for this PRD's bug:** `test_store_conformance.py::TestRunHistory::test_completed_run_is_returned` creates a run, drives it to `AgentRunStatus.COMPLETED`, and asserts `list_runs_for_org(...)` returns exactly one entry with `status is AgentRunStatus.COMPLETED`. This test fails against `main` because no adapter can surface a terminal run to a list caller.
3. `test_store_conformance.py::TestRunHistory::test_all_eight_statuses_are_reachable` seeds one run per member of `AgentRunStatus` (`runtime_api/schemas/common.py:34-44`) and asserts the returned status multiset equals the full eight-member set.
4. `test_store_conformance.py::TestRunHistory::test_ordering_and_keyset` asserts entries are strictly descending on `(created_at, run_id)`, that page 2 fetched with `next_cursor` is disjoint from page 1, and that concatenating pages of size 3 reproduces the single-page ordering for 10 seeded runs.
5. `test_store_conformance.py::TestRunHistory::test_is_scoped_by_org_and_user` asserts a run belonging to `(org_b, user_a)` and one belonging to `(org_a, user_b)` are both absent from `list_runs_for_org(org_id="org_a", user_id="user_a")`.
6. `test_store_conformance.py::TestRunHistory::test_soft_deleted_conversation_runs_are_hidden` asserts a completed run is returned, then `soft_delete_conversation` is called, then the same query returns `()`.
7. `test_store_conformance.py::TestRunHistory::test_history_deletion_clears_run_history` asserts `list_runs_for_org` returns `()` after `delete_user_history(org_id=…, user_id=…)`, on all three adapters.
8. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_fastapi_runtime_api.py -k run_history` passes, covering: `GET /v1/agent/runs` 200 with the `{runs, next_cursor, has_more}` shape; `limit=500` clamped to 200; a malformed `cursor` returning the newest page rather than 4xx/5xx; and 400 when neither service headers nor `org_id`+`user_id` are supplied.
9. `cd services/backend-facade && .venv/bin/python -m pytest tests/test_public_route_contract.py tests/test_tenant_isolation_facade.py` exits 0. `"/v1/agent/runs"` is already in the `required` tuple (`test_public_route_contract.py:22`, verified — it is there for the existing `POST`), so the new assertion is stronger: `test_public_route_contract.py` asserts `set(app.openapi()["paths"]["/v1/agent/runs"]) >= {"get", "post"}`. `test_tenant_isolation_facade.py` asserts a request carrying `?org_id=other_org&user_id=other_user` is forwarded with the **session's** org/user.
10. From the **repo root**, `python3 tools/check_migration_manifest.py` exits 0 (the tool resolves `REPO_ROOT` itself and checks both services — `tools/check_migration_manifest.py:26-32`), and `grep -c "^0002_run_history_index sha256=" services/ai-backend/migrations/MANIFEST.lock` prints `1`.
11. `grep -c "ON agent_runs USING btree (org_id, user_id, created_at DESC, id DESC)" services/ai-backend/migrations/0002_run_history_index.sql` prints `1`, and `grep -c "DROP INDEX CONCURRENTLY IF EXISTS idx_agent_runs_org_user_created" services/ai-backend/migrations/0002_run_history_index.rollback.sql` prints `1`. DB-gated: `tests/unit/runtime_adapters/postgres/test_run_history_index.py::test_explain_uses_org_user_created_index` asserts the `EXPLAIN` plan text for the run-history query contains `idx_agent_runs_org_user_created`.
12. `npm run typecheck --workspace @0x-copilot/api-types` exits 0; `grep -c "latest_run_status?: ActiveAgentRunStatus | null" packages/api-types/src/index.ts` prints `1`; and a new case in `packages/api-types/src/activity.test.ts` (the package runs vitest — `packages/api-types/package.json` `"test": "vitest run"`, verified) asserts `ACTIVE_AGENT_RUN_STATUSES` has length `4` and equals `["queued","running","waiting_for_approval","cancelling"]`, and that every member is also in `AGENT_RUN_STATUSES` (`index.ts:219-228`). `npm test --workspace @0x-copilot/api-types` exits 0.
13. **The false-contract test can no longer be written — pinned by a committed type test, not by a manual mutation.** Two new files are committed, each containing a `Conversation`-typed literal whose `latest_run_status` is `"completed"` preceded by `// @ts-expect-error terminal status is not emittable in latest_run_status`:
    - `apps/frontend/src/features/activity/latestRunStatus.type-test.ts`
    - `apps/desktop/renderer/latestRunStatus.type-test.ts`

    `npm run typecheck --workspace @0x-copilot/frontend` and `npm run typecheck --workspace @0x-copilot/desktop` both exit 0. Because `@ts-expect-error` is itself an error when the next line compiles cleanly, either workspace's typecheck exits **non-zero** if the narrowing is reverted — the guard is self-inverting and needs no tree edit. (Both files are in the typecheck graph: `apps/frontend/tsconfig.json` includes `"src"`; `apps/desktop/tsconfig.json` includes `renderer/**/*.ts`.)

14. `npm test --workspace @0x-copilot/frontend -- ActivityRoute` and `npm test --workspace @0x-copilot/desktop -- destinationBinders` both exit 0; `grep -rn 'latest_run_status: "completed"' apps/ --include=*.tsx --include=*.ts | grep -v type-test` returns zero hits; and `grep -c "as unknown as Conversation" apps/desktop/renderer/destinationBinders.test.tsx` prints `0` (the double assertion at `:312` that currently defeats the narrowing is deleted).
15. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_api_type_contracts.py::test_run_history_entry_fields_match_api_types` exits 0; the test asserts `set(RunHistoryEntry.model_fields) == {"run_id","conversation_id","conversation_title","status","model_name","created_at","started_at","completed_at","cancelled_at"}`.
16. **The 8→5 status fold is total.** `apps/frontend/src/features/activity/ActivityRoute.test.tsx::mapRunStatus > is total over AGENT_RUN_STATUSES` iterates the exported `AGENT_RUN_STATUSES` tuple (`packages/api-types/src/index.ts:219-228`, verified 8 members) and asserts for every member `s` that `ACTIVITY_RUN_STATUSES.includes(mapRunStatus(s))` is `true` and `mapRunStatus(s) !== undefined`. Run by `npm test --workspace @0x-copilot/frontend -- ActivityRoute` (DoD 14).
17. **Design value pinned numerically:** `tests/unit/runtime_api/test_fastapi_runtime_api.py::test_run_history_matches_design_census` seeds the design's fixture census from `tools/design-parity/design-kit/app-v3/copilot-data.jsx:600-645` — **8 runs across 3 calendar days, 1 non-terminal and 7 terminal** — and asserts `GET /v1/agent/runs?limit=50` returns `len(runs) == 8`, `len({e.created_at.date() for e in runs}) == 3`, and `[e.created_at for e in runs]` is non-increasing.

    _Note (not a check):_ on `main` the equivalent conversation-list path returns at most 1 of those 8, which is the defect this PRD exists to fix. The mechanical regression guard is DoD 2.

18. `grep -rn "MessageCursor" services/ai-backend/src services/ai-backend/tests` returns zero hits (renamed to `KeysetCursor`), and `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py -k "keyset or cursor"` passes.
19. `cd services/ai-backend && .venv/bin/python -m pytest` and `cd services/backend-facade && .venv/bin/python -m pytest` both exit 0. The "no new skips" intent is made mechanical rather than judged: `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py -k RunHistory -rs` reports **0 skipped** for the `in_memory` and `file` params (only the `postgres` param may skip, per the marked fixture at `test_store_conformance.py:38-56`).
20. `grep -c "There is no dedicated run-list endpoint yet" packages/api-types/src/activity.ts` prints `0` and `grep -c "GET /v1/agent/runs" packages/api-types/src/activity.ts` prints at least `1` (the stale claim is at `:10-13`, verified).

## Dependencies

**Must land first:** none — Wave 0. This PRD is the root of the Activity remediation chain: it touches only ai-backend, the facade, `packages/api-types`, and host test/type-test files, and it conflicts with no UI PRD. It is safe to run concurrently with PRD-01 (`design-system` only) and PRD-06 (`backend` + `api-types/connectors.ts`); the only shared surface with PRD-06 is `packages/api-types`, in different files.

**Must land before:**

- **PRD-09** (README C19) — PRD-09 D4 bumps the conversation's `updated_at` in `update_run_status`. That reorders every `updated_at`-sorted list, today's Activity spine included. Landing this PRD first moves Activity onto the run-keyed spine so the reorder lands once.
- **PRD-12** (README wave plan) — PRD-12's `GET /v1/agent/runs/active_count` is the second literal route under `/v1/agent/runs`; it must be registered above `/runs/{run_id}` in both ai-backend and the facade, alongside this PRD's collection GET.

**Unblocks:**

- **PRD-04 (shared Activity projection at `packages/chat-surface/src/destinations/activity/activityProjection.ts`)** — hard-blocked. It cannot project per-run rows until per-run rows exist on the wire, and it is the PRD that cuts both host binders over to `GET /v1/agent/runs`, deletes the duplicated `mapRunStatus` / `buildMetaIndex` / `projectActivityRows` pair (`activityApi.ts:56-224` vs `destinationBinders.tsx:242-349`), and fixes `ActivityRunRow.started_at` to carry the run's own instant instead of `conversation.updated_at` (`activityApi.ts:174`). _(Earlier drafts of this PRD called this "PRD-06"; PRD-06 is connector access mode — README C7/C21.)_
- **PRD-08 (Activity surface: `_shared/Row.tsx`, `.ui-list-row`, run meta counters, the `runtime_tool_invocations` writer, and any `GET /v1/activity` composite)** — builds on this list rather than replacing it. _(Earlier drafts called this "PRD-07"; PRD-07 is project data — README C21.)_
- **PRD-01 (type scale) / PRD-02 (chip recipe) / PRD-08 (`Row` trailing slot, day dividers)** — not blocked, but only _observable_ once finished rows render: three of the design's four chip tones (`copilot.css:591-605`) and the chevron/spacer split (`copilot-app.jsx:79`) have no reachable data today.
- **PRD-12 (rail run-count badge, `AUDIT.md` ACT-15)** — **PRD-12 owns it in full** (README C1: the `railBadges` prop deletion, `useActiveRunCount`, and `GET /v1/agent/runs/active_count`). This PRD ships no count endpoint; it merely establishes that a non-terminal count is derivable server-side from `agent_runs`, replacing the client-side derivation over a 100-conversation page in `apps/frontend/src/features/activity/useActiveRunCount.ts:38`.
