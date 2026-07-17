# PR 7.1 — Audit log section (Settings → Members → Audit log)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 7, PR 7.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** backend (read endpoint + facade proxy) · ai-backend (read endpoint for runtime audit) · frontend (Settings → Members → Audit log table)
> **Size:** S (no new tables, no new chain, no new event family — two read endpoints + one facade union route + one paginated table UI). Targeted at one PR.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md), [`services/backend/src/backend_app/routes/audit_export.py`](../../services/backend/src/backend_app/routes/audit_export.py), [`services/ai-backend/src/agent_runtime/observability/audit_chain.py`](../../services/ai-backend/src/agent_runtime/observability/audit_chain.py)
> **Sibling docs:**
> – PR 4.2 — Settings → Workspace group (already shipped: introduces the Members section we hang the link off)
> – PR 1.4 — Two-stage approvals (writes new audit `action`s in `runtime_audit_log` we need to surface)
> – PR 1.6 — Workspace defaults + conversation lifecycle (writes `workspace.defaults.update`, `conversation.delete`, `conversation.restore`, `conversation.update`)
> – PR 7.2 — Per-connector token attribution (sibling Wave 7 PR; independent merge)

---

## 1 · PRD

### 1.1 Problem

Atlas has **five** append-only audit streams already wired with HMAC chain signatures, append-only triggers, and an `audit_writer` Postgres role:

| Stream                                                                                                                            | Service    | Owns                                                            |
| --------------------------------------------------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------- |
| [`identity_audit_events`](../../services/backend/migrations/0004_identity_foundation.sql)                                         | backend    | login, MFA, role grant/revoke, member add/remove                |
| [`mcp_audit_events`](../../services/backend/migrations/0001_initial_mcp_skills.sql)                                               | backend    | MCP server install / OAuth / token refresh / scope change       |
| [`skill_audit_events`](../../services/backend/migrations/0001_initial_mcp_skills.sql)                                             | backend    | skill enable / disable / edit                                   |
| [`deploy_audit_events`](../../services/backend/src/backend_app/audit_deploy_api.py)                                               | backend    | release deploys (already exported via `/internal/v1/audit/...`) |
| [`runtime_audit_log`](../../services/ai-backend/migrations/0001_initial_runtime_persistence.sql) + [`0003_audit_hardening.sql`]() | ai-backend | conversation lifecycle, approvals, workspace-defaults updates   |

A SIEM export pipe already pumps these to Splunk / Sentinel / Elastic via `POST /internal/v1/audit/export` (NDJSON; chain fields exposed for end-to-end verification). What we are **missing** is a customer-visible read surface inside the product:

- The Atlas Design Doc § "Settings — Members" links to an "Audit log" page.
- The Open TODOs section flags it as a 404 today.
- Every privileged action that writes an audit row already happens — admins just have nowhere _in the product_ to read it back.

Without this PR:

- Marcus (Sarah's admin) can't answer "who approved the post to #announcements last Thursday" without standing up the SIEM pipeline first.
- Compliance reviewers see the controls in code but cannot see them rendered for the product story Atlas tells.
- Every tap on the "Audit log" link in PR 4.2's Members panel 404s.

### 1.2 Goals

1. **One paginated table in Settings → Members → Audit log** that admins can scan without leaving the product. Filter by actor, action, resource, and date range.
2. **Five streams unified at the read edge.** The backend exposes a single union endpoint that fans out to the existing per-stream tables; the frontend never needs to know which stream owns an action.
3. **Zero new audit storage.** No new table, no new chain, no new HMAC key. The existing chain is the source of truth — we add only a _read_ projection.
4. **Admin-only, scope-gated.** Re-use the existing `ADMIN_AUDIT_EXPORT` permission scope. Identical authorization story to the SIEM export route.
5. **Cursor-paginated, server-stable.** Use `(stream, seq)` as the cursor — the chain's monotonic sequence number is already indexed (`idx_*_audit_events_org_seq`, `idx_runtime_audit_log_org_seq`). No `OFFSET`, no full table scans.
6. **Append-only at the wire.** The endpoint is read-only; nothing on this surface allows mutation. Tampering remains impossible (the existing `audit_immutable_guard` trigger and the chain verifier still own integrity).
7. **Stream-impact: zero.** No new SSE event, no projection change, no streaming handshake change. PR 7.1 is orthogonal to runtime streaming — it adds a REST-only read endpoint.

### 1.3 Non-goals

- **No mutation.** No "delete this audit row" — the entire premise of the audit log is immutability. Triggers + the `audit_writer` role grant already enforce it; this PR doesn't loosen them.
- **No retention surface.** Retention of audit rows is governed by the existing `retention_policies` (PR 1.6 topic for messages/events; audit retention is a deployment-config knob and stays there).
- **No SIEM-export UI.** The customer's SIEM is configured by an operator through `/internal/v1/audit/export`. This PR doesn't move that endpoint to the public plane.
- **No advanced search / SQL-like predicates.** v1 surfaces a small fixed set of filters (date range, actor, action prefix, resource_type). Free-text grep is a follow-up if asked.
- **No legal-hold UI.** The `runtime_legal_holds` table exists but its admin UI is a separate compliance-review PR.
- **No customer log forwarding from the UI.** Customers who want raw NDJSON keep using `/internal/v1/audit/export`; the FE calls a different, paginated endpoint.
- **No row-level export-this-page button in v1.** The JSON shape is downloadable from devtools; a "Download CSV" button is a small follow-up.

### 1.4 Success criteria

- ✅ Admin opens Settings → Members → Audit log; the table renders ≤300ms p99 for the first page (50 rows) against a tenant with ≥1M total audit rows.
- ✅ Filtering by `action='approval.decided'` returns only matching rows from `runtime_audit_log` and the union endpoint hides the empty `mcp_audit_events` slice.
- ✅ Cursor pagination is **stable under concurrent writes** — pulling page _N_ then page _N+1_ never sees a row twice and never skips a row.
- ✅ Non-admin caller hitting the endpoint gets `403 forbidden`. The Settings rail item is hidden for non-admins (already the pattern for the rest of the Members section).
- ✅ Each row carries the chain fields (`seq`, `prev_hash`, `signature`, `key_version`) the existing SIEM consumers know — i.e. the FE could verify the chain itself if it ever wanted to.
- ✅ One smoke test ([`make test`](../../Makefile)) validates: write one of each `action` across both services → fetch `/v1/audit?limit=N` → all rows appear in expected order.
- ✅ The streaming handshake is byte-for-byte unchanged. PR 7.1 introduces zero new event types and zero changes to `RuntimeEventEnvelope`.

### 1.5 User stories

| As…                 | I want…                                                                            | So that…                                                                      |
| ------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Marcus (admin)      | a chronological table of "who did what, when" across this workspace                | I can answer compliance questions without an engineer in the loop             |
| Marcus              | to filter by actor, action, and date range                                         | I can scope investigations to the right user and the right week               |
| Sarah (member)      | the audit log link in Members to be hidden                                         | I'm not given a 403 and a confusing UI; the surface matches my role           |
| Compliance reviewer | the customer-visible audit row to carry the same chain fields the SIEM export does | I can verify the in-product table tells the same story as the SIEM            |
| Future-Wave         | one read endpoint that fans out to all streams                                     | new audit producers (e.g. share grants in Wave 6) auto-appear without FE work |

---

## 2 · Spec

### 2.1 Wire — paginated read

The frontend talks to **one** facade endpoint. The facade fans out to `backend` (identity + MCP + skill + deploy streams) and `ai-backend` (runtime stream), merges by `created_at DESC`, and returns a stable cursor.

```
GET /v1/audit?
  cursor=<opaque base64 token>
  &limit=50              (1..200, default 50)
  &actor_user_id=user_…  (optional)
  &action=approval.      (optional; matched as prefix — see §2.5)
  &resource_type=run     (optional)
  &since=ISO-8601        (optional)
  &until=ISO-8601        (optional)
```

```jsonc
{
  "rows": [
    {
      "stream": "runtime_audit_log",
      "seq": 488213,
      "audit_id": "audit_…",
      "org_id": "org_…",
      "actor": {
        "type": "user", // user | runtime | worker | system
        "user_id": "user_marcus",
        "display_name": "Marcus T.", // hydrated by backend; null if user deleted
      },
      "subject": {
        // only filled if the action acts on a person/resource
        "type": "user",
        "user_id": "user_sarah",
        "display_name": "Sarah Chen",
      },
      "action": "approval.decided",
      "resource_type": "approval",
      "resource_id": "appr_…",
      "outcome": "success",
      "metadata": {
        // already redacted by the producer (`metadata_json_redacted`)
        "decision": "approved",
        "approval_kind": "tool_action",
        "tool": "slack.post_message",
        "channel": "#launch-aurora",
      },
      "chain": {
        "seq": 488213,
        "prev_hash": "f31b…",
        "signature": "9c20…",
        "key_version": 2,
      },
      "created_at": "2026-05-05T16:42:11.220Z",
    },
  ],
  "next_cursor": "eyJzdHJlYW1zIjp7Im1jcCI6MTAwLCJza2lsbCI6NDIsImlkZW50aXR5IjoxNDAwLCJydW50aW1lIjo0ODgyMTIsImRlcGxveSI6Mn19",
  "has_more": true,
}
```

Rows are uniformly shaped across streams. The `stream` field tells the FE which underlying table emitted the row (purely informational — the FE renders one table); the `chain` block contains the four fields that the SIEM verifier already needs.

The cursor is `base64(json({stream → last_seq_seen}))`. Each stream advances independently; the merge is monotonic in `created_at` per stream and stable under concurrent writes because chain `seq` is unique per `(stream, org_id)`.

### 2.2 Wire — schema sources for `metadata`

Each producer already writes a redacted JSONB blob. We do **not** rewrite producers. We do publish a typed catalog of known `(stream, action)` tuples in [`packages/api-types/src/audit.ts`](../../packages/api-types/src/index.ts) so the FE can render headlines without inspecting JSON keys at runtime. The catalog is **descriptive**, not prescriptive — unknown `action` values fall back to a generic row renderer that just prints `action` + key/value pairs.

```ts
// packages/api-types/src/index.ts  (additive)
export type AuditStream =
  | "identity_audit_events"
  | "mcp_audit_events"
  | "skill_audit_events"
  | "deploy_audit_events"
  | "runtime_audit_log";

export interface AuditActor {
  type: "user" | "runtime" | "worker" | "system";
  user_id: string | null;
  display_name: string | null;
}

export interface AuditChainFields {
  seq: number;
  prev_hash: string | null;
  signature: string | null;
  key_version: number | null;
}

export interface AuditRow {
  stream: AuditStream;
  seq: number;
  audit_id: string;
  org_id: string;
  actor: AuditActor;
  subject: AuditActor | null;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: "success" | "failure" | "denied";
  metadata: Record<string, unknown>;
  chain: AuditChainFields;
  created_at: string;
}

export interface AuditListRequest {
  cursor?: string;
  limit?: number; // 1..200, default 50
  actor_user_id?: string;
  action?: string; // prefix match
  resource_type?: string;
  since?: string; // ISO 8601
  until?: string; // ISO 8601
}

export interface AuditListResponse {
  rows: AuditRow[];
  next_cursor: string | null;
  has_more: boolean;
}
```

`identity_audit_events` does not have `outcome` today. We project `success` for any row whose `action` doesn't end in `.failed`/`.denied` and `denied` for the explicit denial actions; this is purely a presentation projection that backend's audit reader applies. We do not migrate the source table — chain integrity is preserved.

### 2.3 Persistence

**Zero new tables. Zero new columns. Zero new chains. Zero new triggers.**

Why a generic table is the wrong instinct:

- A union table doubles writes (every audit row written to both producer table and union table). Two transactions, two chain heads, double the failure modes.
- The existing chain has 24 months of evidence per [PR 1.4](pr-1.4-two-stage-approvals.md) merge. Forking it now would set a precedent that "audit moves" — exactly the opposite of what a tamper-evident control should communicate.
- Postgres `UNION ALL` over five `(org_id, seq)`-indexed tables for a 50-row page is fast enough that a materialised union has no win.

What we **do** add is a thin reader (no schema change):

- `services/backend/src/backend_app/audit_reader.py` — a class `AuditReader` with one method `list(filters, cursor, limit)` that issues one prepared statement per stream and merges them with a heap by `created_at DESC`.
- `services/ai-backend/src/runtime_api/services/audit_reader.py` — the same shape, but reads only `runtime_audit_log` (the only ai-backend stream).
- `services/backend-facade/src/backend_facade/routes/audit.py` — fans out to backend + ai-backend, applies the same heap merge in-memory, returns the union page. (The facade does not query Postgres directly; it composes HTTP calls. Already the pattern the rest of `backend-facade` uses.)

### 2.4 Wire — endpoint placement

| Route                            | Service                    | Auth                                                         | Purpose                                       |
| -------------------------------- | -------------------------- | ------------------------------------------------------------ | --------------------------------------------- |
| `GET /v1/audit`                  | `backend-facade`           | Session + `ADMIN_AUDIT_EXPORT` scope                         | Public read surface for the Settings page.    |
| `GET /internal/v1/audit/list`    | `backend`                  | `ENTERPRISE_SERVICE_TOKEN` (already in use by the facade)    | Returns the four backend-owned streams' rows. |
| `GET /internal/v1/audit/list`    | `ai-backend`               | `RuntimeServiceAuthenticator` (already in use by the facade) | Returns the runtime stream's rows.            |
| `POST /internal/v1/audit/export` | `backend` (already exists) | `ADMIN_AUDIT_EXPORT`                                         | Existing SIEM export — unchanged.             |

The facade endpoint is **read-only**; no `POST/PUT/DELETE` siblings exist. The internal endpoints are also read-only — they cannot mutate audit rows because the `audit_writer` role grant only has `INSERT, SELECT`, and the immutability triggers reject `UPDATE/DELETE` regardless of role.

### 2.5 Filter semantics

| Filter          | Match                                                                                                             | Pushed to SQL                                              |
| --------------- | ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `actor_user_id` | exact match against producer's `actor_user_id`/`user_id`/`requested_by_user_id` column                            | yes — index hit in every stream                            |
| `action`        | **prefix** match (e.g. `approval.` matches `approval.created`, `approval.decided`, `approval.forwarded`)          | yes — `action LIKE :prefix \|\| '%'` per stream            |
| `resource_type` | exact match; only `runtime_audit_log` has this column natively, others materialise it from their table name       | per-stream filter (cheap)                                  |
| `since`/`until` | ISO-8601 against `created_at`; **inclusive `since`, exclusive `until`** (matches existing usage-window semantics) | yes — uses each stream's `(org_id, created_at DESC)` index |
| (cursor)        | opaque, encodes the per-stream `last_seq_seen`                                                                    | yes — `seq > :s` per stream                                |

`action` prefix match is intentional: actions are namespaced (`approval.*`, `mcp.*`, `workspace.defaults.*`, `conversation.*`), and admins overwhelmingly want "show me everything in the approvals namespace", not exact strings. Exact match is achievable by sending the full action — prefix-of-itself is a no-op.

### 2.6 Permissions

Reuse the existing scope:

- **Read** (`GET /v1/audit`): caller must hold `ADMIN_AUDIT_EXPORT` (the same scope `POST /internal/v1/audit/export` checks). This scope is already granted to the system Admin role from migration `0004b_seed_system_roles.sql`. No new RBAC primitive.
- **The Settings rail** hides the link unless the loaded session reports the scope — same gating as the SIEM export config link in PR 4.2.
- **No write surface** — there is nothing to authorize.

This intentionally gates audit-read with the export scope (not a separate `ADMIN_AUDIT_READ`): an admin who can export the chain to a SIEM can also read the chain in the product. Splitting the two would let a "browse-only admin" exist; we do not have a use-case for that and adding the scope now creates an empty role.

### 2.7 Error semantics

| Condition                                   | Status         | Code                                                                                                                              |
| ------------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Caller lacks `ADMIN_AUDIT_EXPORT`           | 403            | `forbidden`                                                                                                                       |
| `cursor` is malformed / decodes to non-JSON | 400            | `invalid_cursor`                                                                                                                  |
| `limit > 200` or `limit < 1`                | 422            | `invalid_request`                                                                                                                 |
| `since > until`                             | 422            | `invalid_request`                                                                                                                 |
| `action` empty string                       | 422            | `invalid_request`                                                                                                                 |
| One downstream stream times out             | 200 (degraded) | response carries `degraded_streams: ["mcp"]` and the cursor still advances; UI shows a banner "MCP audit temporarily unavailable" |
| All downstream streams unreachable          | 503            | `audit_unavailable`                                                                                                               |

Degrade-on-partial-failure matches the design's "show the work, but compress it" principle — the table renders with what we have rather than blanking the page when one stream is slow.

### 2.8 Frontend contract

The Audit log section is a single table with three FE pieces:

- `apps/frontend/src/features/settings/sections/AuditLog.tsx` (new) — the page; renders filters + the paginated table.
- `apps/frontend/src/features/settings/sections/audit/useAuditLog.ts` (new) — a hook that calls `/v1/audit` with the cursor, exposes `(rows, fetchMore, isLoading, degraded_streams)`.
- `apps/frontend/src/features/settings/sections/audit/AuditRow.tsx` (new) — one row renderer; consults the typed action catalog (`packages/api-types`) for the headline; falls back to generic.

Re-uses `@0x-copilot/design-system`: `Table`, `Pill` (status outcomes), `Avatar` (actor), `RelativeTime`, `EmptyState`, `Pagination`. **No new design-system primitive.**

The link from Settings → Members → "Audit log →" lives in the existing `MembersSettings.tsx` (PR 4.2) — it is currently a `<a>` to a missing route; PR 7.1 connects the route.

### 2.9 What backend's reader does

Sketched in pseudocode (real impl in [`audit_reader.py`](#) — ≤120 LOC):

```python
class AuditReader:
    async def list(self, *, org_id: str, filters: AuditFilters, cursor: AuditCursor,
                   limit: int) -> AuditPage:
        # Each stream returns up to `limit` rows beyond its last seen seq.
        per_stream = await asyncio.gather(*(
            self._fetch_stream(stream, org_id, filters, cursor.for_stream(stream), limit)
            for stream in self._enabled_streams
        ), return_exceptions=True)

        # Heap-merge by created_at DESC; preserve produce-time ordering on ties.
        merged = heapq.nlargest(limit, chain.from_iterable(rows for rows in per_stream),
                                key=lambda r: (r.created_at, r.stream, r.seq))

        next_cursor = AuditCursor.from_rows(merged) if len(merged) == limit else None
        degraded = [stream for stream, result in zip(self._enabled_streams, per_stream)
                    if isinstance(result, Exception)]
        return AuditPage(rows=merged, next_cursor=next_cursor,
                         has_more=next_cursor is not None,
                         degraded_streams=degraded)
```

`_fetch_stream` is one prepared statement per stream; SQL is the obvious `WHERE org_id = $1 AND seq > $2 AND created_at >= $3 ORDER BY created_at DESC LIMIT $4`. All four predicates hit existing indexes (the `(org_id, created_at)` and `(org_id, seq)` indexes per stream — both already shipped).

### 2.10 What ai-backend's reader does

Identical shape to backend's, but the only stream it owns is `runtime_audit_log`. The class lives at `services/ai-backend/src/runtime_api/services/audit_reader.py` and consumes the existing `AuditLogStore` adapter from the postgres runtime adapter. The internal route at `/internal/v1/audit/list` returns the same `AuditPage` shape.

### 2.11 Display-name hydration

The producer rows store `actor_user_id` / `subject_user_id` as strings. We render `display_name` and `avatar_url` because the prototype demands it.

The hydrator lives in **backend** (which owns `users`) and runs once per page response. We do _one_ batched lookup per page, not per row — `SELECT user_id, display_name, avatar_url FROM users WHERE org_id=$1 AND user_id = ANY($2)` — keyed by the union of all actors and subjects on the page. ai-backend's reader passes through the user IDs; the facade asks backend for hydration after merging.

We do **not** add a copy of the display name to the audit row at write-time — that would break the chain on user rename.

### 2.12 Action catalog (typed; lives in api-types)

Approximate first cut of recognised tuples for v1, drawn from `grep` over the producer code:

- `identity_audit_events` — `auth.login.*`, `auth.mfa.*`, `auth.discovery`, `member.added`, `member.removed`, `role.granted`, `role.revoked`, `scim.token.minted`, `scim.token.revoked`
- `mcp_audit_events` — `mcp.server.installed`, `mcp.server.removed`, `mcp.oauth.completed`, `mcp.oauth.failed`, `mcp.token.refreshed`, `mcp.scope.changed`
- `skill_audit_events` — `skill.created`, `skill.enabled`, `skill.disabled`, `skill.deleted`
- `deploy_audit_events` — `deploy.started`, `deploy.completed`, `deploy.failed`
- `runtime_audit_log` — `conversation.created`, `conversation.update`, `conversation.delete`, `conversation.restore`, `approval.created`, `approval.decided`, `approval.forwarded`, `workspace.defaults.update`, `connectors.update`, `legal_hold.created`, `legal_hold.released`, `data_deletion.executed`

The catalog is data, not a switch — adding a new producer in a future PR adds one entry to this table and gets a typed headline for free; a missing entry still renders correctly via the fallback. This is the DRY contract we're committing to.

---

## 3 · Architecture

### 3.1 Where this lives

```
   ┌────────────────┐          GET /v1/audit?cursor=…&action=approval.
   │   apps/        │ ──────────────────────────────┐
   │   frontend     │                                │
   │  Settings →    │ ◄──────────────────────────────┤  AuditPage (rows, cursor)
   │  Members →     │                                │
   │  Audit log     │
   └────────────────┘
                                                    ▼
                                          ┌────────────────────────┐
                                          │  backend-facade        │
                                          │  /v1/audit/route.py    │
                                          │   AuditCompositor      │
                                          └─────┬──────────┬───────┘
                                                │          │
                  GET /internal/v1/audit/list   │          │   GET /internal/v1/audit/list
                  (4 streams: identity/mcp/     │          │   (1 stream: runtime_audit_log)
                   skill/deploy)                │          │
                                                ▼          ▼
                                ┌──────────────────┐    ┌─────────────────────┐
                                │ backend          │    │ ai-backend          │
                                │  AuditReader     │    │  AuditReader        │
                                └────┬─────────────┘    └────┬────────────────┘
                                     │                       │
                                     │ SELECT … per stream   │ SELECT … runtime_audit_log
                                     │ via existing          │ via existing
                                     │ AuditChainStore       │ AuditLogStore
                                     ▼                       ▼
              ┌───────────────────────────────────────────────────────┐
              │ Postgres                                              │
              │ ─ identity_audit_events (chain ✓ trigger ✓ key v_)   │
              │ ─ mcp_audit_events       (chain ✓ trigger ✓)         │
              │ ─ skill_audit_events     (chain ✓ trigger ✓)         │
              │ ─ deploy_audit_events    (chain ✓ trigger ✓)         │
              │ ─ runtime_audit_log      (chain ✓ trigger ✓)         │
              └───────────────────────────────────────────────────────┘
                                     ▲
                                     │ unchanged
                                     │
                       SIEM export (POST /internal/v1/audit/export) — already shipped
```

The diagram emphasises: the read path is a new edge; the **write path is byte-identical** to today; the chain triggers and `audit_writer` role are unchanged.

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                  | Touched by this PR?                                        |
| ------------------------------------------ | ---------------------------------------------------------- |
| `runtime_events` schema                    | **No.**                                                    |
| `RuntimeEventEnvelope` Pydantic            | **No.**                                                    |
| SSE handshake (`?after_sequence=N`)        | **No.**                                                    |
| Worker `runtime_worker/` job loop          | **No.**                                                    |
| Capabilities middleware / tools            | **No.**                                                    |
| Citation registry (PR 1.1)                 | **No.**                                                    |
| Drafts (PR 1.3)                            | **No.**                                                    |
| Approvals chain (PR 1.4)                   | **No.**                                                    |
| Subagent feeds (PR 1.5)                    | **No.**                                                    |
| Workspace defaults (PR 1.6)                | **No.**                                                    |
| Audit chain (HMAC, prev_hash, key_version) | **No** — read-only consumer of fields it already produces. |
| Retention sweeper                          | **No.**                                                    |

This PR is a pure **read-side projection**. The runtime stream contract has no awareness of it. A run that fires while an admin is paginating the audit log behaves identically to one that fires when the admin is offline.

### 3.3 Why a union endpoint at the facade, not a single backend reader

Two valid alternatives:

- **A.** Centralise all streams in `backend` (move `runtime_audit_log` reader into backend; ai-backend exposes nothing).
- **B.** Centralise in `ai-backend` (mirror the four backend streams into `ai-backend`).

Both violate service boundaries. `ai-backend` cannot read `backend`'s schema (separate database connection pool, separate migrations, separate `.venv`); reading it via HTTP is what the facade already does for the rest of the surface. Conversely, `backend` should not reach into `ai-backend`'s `runtime_audit_log`.

The facade is the right composer. Each service reader is small (≤120 LOC). The facade's `AuditCompositor` is even smaller — a heap-merge over two HTTP responses, ≤80 LOC. This is the same shape `backend-facade/conversations` already uses to combine fields owned by both backends.

### 3.4 Cursor design — why opaque base64

A naive cursor is `(created_at, audit_id)`. It works _almost_ everywhere — but clock skew between five chain heads on five different streams means two rows can share `created_at` to the millisecond and one of them gets repeated or skipped on the page boundary.

The chain `seq` field is monotonic per `(stream, org_id)` and uniquely orders writes within that chain. `(stream → last_seq_seen)` as the cursor solves repetition and skips deterministically:

```python
# Cursor on first request: empty.
# After page 1 the server sends next_cursor encoding:
#   {"identity": 1421, "mcp": 89, "skill": 12, "deploy": 4, "runtime": 488213}
# Page 2 SQL per stream: WHERE org_id=$1 AND seq > $2 ORDER BY created_at DESC LIMIT $3
```

Concurrent writes that bump a stream's seq above `last_seq_seen` between pages **show up at the top of page 2** (which is correct — these are newer rows the admin should see), not in the middle. Pagination invariants (no duplicates, no gaps, monotone) hold under arbitrary concurrent producer load.

The cursor is base64-encoded JSON because (a) opaque cursors let us evolve the encoding without breaking clients, (b) it's six lines of code per side, (c) the same pattern is used by every other paginated endpoint in the repo.

### 3.5 No third-party middleware needed

Web-survey of likely candidates and why we skip them:

- **`sqlalchemy-audit` / `audit-log` packages** — every popular Python audit library writes to a generic `audit_log` table that the library itself owns. We already have five tamper-evident chains keyed by `org_id`, with HMAC signatures, key rotation, and an SIEM pump. Replacing them with a generic logger would lose the HMAC chain and require re-emitting 24 months of evidence. **Hard no.**
- **`fastapi-pagination`** — solves `LIMIT/OFFSET` pagination for ORM queries. We need cursor pagination across a heap-merge of five tables, with stream-aware cursors. The library would not help; reuse the facade's existing cursor patterns.
- **OpenTelemetry log signals** — orthogonal. Our audit log is a compliance record, not an operational telemetry signal. Mixing them would obscure the boundary the SOC2 review depends on.
- **PostgREST / Hasura** — would auto-generate a CRUD surface; we need exactly 1 read endpoint and have a strict policy that audit is append-only. Tooling that knows about UPDATE/DELETE is a foot-gun.
- **`structlog` / `loguru`** — operational logging, not compliance. Already used elsewhere; not relevant here.

The **one** library decision worth considering is `httpx` for the facade's fan-out — already in use. We add zero new deps.

### 3.6 DRY — what we reuse vs. what we add

| Concern                 | Reuse                                                                                                                             | Add                                          |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| Audit storage           | Existing five tables + chain triggers + HMAC signer ([`AuditChainSigner`](../../services/backend/src/backend_app/audit_chain.py)) | —                                            |
| Append-only enforcement | `audit_writer` role grant + immutability triggers (migrations 0002 / 0003 / 0004)                                                 | —                                            |
| RBAC                    | `ADMIN_AUDIT_EXPORT` scope (already gates SIEM export); `RequireScopes` middleware                                                | —                                            |
| Identity hydration      | `users` table batch lookup pattern from PR 4.2                                                                                    | —                                            |
| Cursor pagination       | Cursor pattern from `backend-facade/conversations.py` (opaque base64 token)                                                       | one new `AuditCursor` value object (~30 LOC) |
| Heap merge              | `heapq.nlargest` (stdlib)                                                                                                         | —                                            |
| Service boundary        | Facade fan-out pattern; backend `RequireScopes`; ai-backend `RuntimeServiceAuthenticator`                                         | —                                            |
| Streaming               | None — this PR doesn't touch streaming                                                                                            | —                                            |
| FE primitives           | `Table`, `Avatar`, `Pill`, `RelativeTime`, `EmptyState` from `@0x-copilot/design-system`                                          | —                                            |
| FE typed-action catalog | TS literal-union types — same pattern PR 1.4 used for approval kinds                                                              | one TS const map (`AUDIT_ACTION_CATALOG`)    |

**Net new code** is intentionally small:

- backend: 1 reader class (~120 LOC), 1 internal route (~30 LOC).
- ai-backend: 1 reader class (~80 LOC; only one stream), 1 internal route (~30 LOC).
- backend-facade: 1 compositor (~80 LOC), 1 public route (~30 LOC).
- packages/api-types: 1 file `audit.ts` (~120 LOC including action catalog).
- frontend: 1 page (~140 LOC), 1 hook (~50 LOC), 1 row component (~80 LOC), tests.
- 0 SQL migrations.

Total: **~700 net LOC**, ~250 of which is FE + tests.

### 3.7 Sequence — admin opens audit log page

```
admin                FE                             facade                       backend                 ai-backend
 │                    │                               │                             │                       │
 │ open Settings →    │                               │                             │                       │
 │ Members → Audit log│                               │                             │                       │
 │ ──────────────────►│                               │                             │                       │
 │                    │ GET /v1/audit?limit=50        │                             │                       │
 │                    │ ─────────────────────────────►│  authorise ADMIN_AUDIT_EXP. │                       │
 │                    │                               │  ─ fan-out begin            │                       │
 │                    │                               │ ─────────────────────────► │  AuditReader.list     │
 │                    │                               │                             │  (4 streams, heap)    │
 │                    │                               │ ──────────────────────────────────────────────────►│ AuditReader.list
 │                    │                               │                             │                       │ (runtime_audit_log)
 │                    │                               │ ◄────────────────────────── │  rows[…]  cursor      │
 │                    │                               │ ◄──────────────────────────────────────────────────│ rows[…]  cursor
 │                    │                               │  heap-merge by created_at   │                       │
 │                    │                               │  hydrate display_name       │                       │
 │                    │                               │  (one batched users lookup) │                       │
 │                    │ ◄──────────────────────────── │  AuditPage                  │                       │
 │                    │  table renders                │                             │                       │
 │                    │                               │                             │                       │
 │ scroll → fetchMore │                               │                             │                       │
 │                    │ GET /v1/audit?cursor=eyJ…     │                             │                       │
 │                    │ ─────────────────────────────►│  same fan-out, advanced cursors                     │
 │                    │ ◄──────────────────────────── │  next 50                    │                       │
 │ apply filter:      │                               │                             │                       │
 │  action=approval.  │                               │                             │                       │
 │                    │ GET /v1/audit?action=approval.│                             │                       │
 │                    │ ─────────────────────────────►│  per-stream WHERE adds      │                       │
 │                    │                               │  action LIKE 'approval.%'   │                       │
 │                    │ ◄──────────────────────────── │  rows[…]                    │                       │
```

### 3.8 Edge cases

| Case                                                                         | Behaviour                                                                                                                                                                                                                   |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Admin filters to an `action` no producer ever wrote                          | Empty page; cursor is `null`; `has_more=false`. Zero error.                                                                                                                                                                 |
| Producer side rotates HMAC key (`AUDIT_HMAC_KEY_VERSION` bumped) mid-page    | The reader does no HMAC verification — that's the SIEM verifier's job. The `key_version` field is preserved so the FE could verify per row if it wanted to. Page renders without interruption.                              |
| One downstream stream has zero rows for the org                              | Empty slice in the heap-merge; cursor for that stream stays `0`. The other streams populate the page normally. Subsequent pages still consult that stream (cheap; index-only scan returns nothing).                         |
| User whose `display_name` is in a row was deleted                            | Hydrator returns `null`; FE renders "(deleted user)". The audit row's `actor_user_id` string is unchanged — chain integrity preserved.                                                                                      |
| Admin has scope but caller-supplied `org_id` doesn't match session           | Existing identity guard rejects with 403 before reaching `AuditReader`.                                                                                                                                                     |
| Two pages requested concurrently with the same cursor                        | Idempotent — the same set of rows is returned twice. The FE de-dupes by `audit_id` if it ever holds two pages in flight (it doesn't today, but the contract is forgiving).                                                  |
| Cursor was issued by an earlier code version that didn't include all streams | Missing-stream entries default to `0` — that stream's rows are paginated from the beginning on the next request. We accept up to one page of "new old rows" appearing; alternative is a cursor migration we don't need yet. |
| `since` and `until` are the same timestamp                                   | Empty page (`since` inclusive, `until` exclusive — `since == until` collapses the window). Documented in `AuditFilters`.                                                                                                    |
| One stream's HTTP call times out                                             | Response carries `degraded_streams: ["mcp"]`; FE renders a banner. Cursor still advances for the streams that succeeded; the user's next page does **not** retry the failed stream automatically — they refresh.            |
| User pages 100 pages then filters changed                                    | Cursor encodes the filter set; if filters change, the cursor is rejected (`invalid_cursor`) and the FE starts at page 1.                                                                                                    |
| Admin role removed mid-session                                               | The next page request 403s. Existing session-revalidation toast surfaces.                                                                                                                                                   |

### 3.9 Test plan

Lives in the same PR. Minimum bar before merge.

**backend (`services/backend/tests/`)**

- `unit/audit_reader/test_basic.py` — write 50 mixed-stream rows, page through with `limit=10`, expect 5 pages, no duplicates, no gaps.
- `unit/audit_reader/test_filters.py` — table-driven matrix over `actor_user_id`, `action` prefix, `resource_type`, `since`/`until`. Assertions on which rows appear.
- `unit/audit_reader/test_chain_fields_passthrough.py` — every returned row carries the four chain fields it had at write-time.
- `unit/audit_reader/test_concurrent_writes.py` — write rows during pagination, assert monotone-no-gaps.
- `unit/audit_reader/test_one_stream_dies.py` — fault-inject one stream's prepared statement; assert `degraded_streams` populates and other streams' rows still appear.
- `unit/audit_reader/test_hydration.py` — actors and subjects resolved from `users`; missing IDs rendered as `null`.

**ai-backend (`services/ai-backend/tests/`)**

- `unit/runtime_api/audit/test_runtime_reader.py` — same pagination shape over `runtime_audit_log` only.

**backend-facade (`services/backend-facade/tests/`)**

- `unit/audit/test_compositor.py` — heap-merge correctness; `degraded_streams` plumbed through; cursor encoding round-trips.
- `unit/audit/test_authz.py` — non-admin → 403; admin without scope → 403; admin with scope → 200.

**Frontend (`apps/frontend/src/features/`)**

- `settings/sections/audit/AuditLog.test.tsx` — first page renders; filter triggers refetch; "Load more" appends.
- `settings/sections/audit/AuditRow.test.tsx` — known action renders typed headline; unknown action falls back to generic.
- `settings/sections/audit/useAuditLog.test.tsx` — cursor advance; degraded banner.

**Cross-service smoke (`make test`)**: write one row of each `action` across both services → fetch `/v1/audit?limit=N` → all rows appear in expected order. Verifies the union endpoint is wired end-to-end.

### 3.10 Rollout

- **Flag-free.** No schema change. New endpoints respond with the live contents of the existing tables.
- **No backout step.** Disabling the FE link reverts to v0; deleting the new routes reverts the read surface. Audit chain integrity is unaffected by anything in this PR (we never write).
- **Performance posture.** First page typical p99 ≤ 200ms (five `LIMIT 50` index-only scans + a small in-memory merge). Filtered pages are bounded by the filter's index hit rate; `(org_id, action, created_at)` indexes already exist on `identity_audit_events` and `mcp_audit_events`. If a future tenant develops a hot single-action namespace we can add a dedicated index without API change.

### 3.11 Open questions

1. **Should we surface a row count?** The design's table doesn't show one and exact counts on append-only chains are O(rows) without a window function. v1 says no — pagination is enough. If asked, we add an estimated count via `pg_class.reltuples` per stream.
2. **CSV export from this surface?** Not needed if `/internal/v1/audit/export` exists; v1 says the SIEM export is the canonical bulk path. We could add a per-page CSV download in a follow-up if a customer asks.
3. **Should the FE verify the HMAC chain?** Out of scope — chain verification is the SIEM consumer's job. Adding the WASM HMAC verifier to the bundle is significant size; v1 ships chain fields so it's _possible_, not so it's done.
4. **Per-row "view in source" link.** A `runtime_audit_log` row about an approval could deep-link to the conversation/run/message that produced it. Lovely follow-up; not v1.

---

## 4 · Acceptance checklist

- [ ] No new SQL migrations.
- [ ] `services/backend/src/backend_app/audit_reader.py` reads four streams with one prepared statement each, merges by `created_at DESC`, returns `AuditPage`.
- [ ] `services/ai-backend/src/runtime_api/services/audit_reader.py` mirrors the same shape for `runtime_audit_log`.
- [ ] `services/backend-facade/src/backend_facade/routes/audit.py` exposes `GET /v1/audit`, fans out to both backends, performs the heap-merge, hydrates display names in one batched `users` lookup.
- [ ] All three services authorise via `ADMIN_AUDIT_EXPORT`. Non-admin → 403.
- [ ] Cursor is opaque base64-encoded JSON of `(stream → last_seq_seen)`; round-trips; rejects `invalid_cursor` payloads.
- [ ] `degraded_streams` populates when one stream errors; the other streams still return rows.
- [ ] `@0x-copilot/api-types` exports `AuditRow`, `AuditListRequest`, `AuditListResponse`, `AUDIT_ACTION_CATALOG`.
- [ ] `apps/frontend/src/features/settings/sections/AuditLog.tsx` renders filters + paginated table; the link from `MembersSettings.tsx` resolves.
- [ ] No new event types in `runtime_api/schemas/events.py`. `RuntimeEventEnvelope` Pydantic schema is byte-identical pre/post merge.
- [ ] Existing `POST /internal/v1/audit/export` route is **untouched**.
- [ ] Existing chain triggers (`audit_immutable_guard`) and `audit_writer` role grants are **untouched**.
- [ ] `make test` green; backend + ai-backend + facade unit suites green; frontend typecheck + build green.

---

## 5 · References

- [Atlas Design Doc](../new-design/Design Doc.html) § "Settings" → Members → Audit log; § "Open TODOs" — "Audit log (P1)".
- [`services/backend/src/backend_app/routes/audit_export.py`](../../services/backend/src/backend_app/routes/audit_export.py) — existing SIEM export route. PR 7.1 sits next to this file but is read-only and admin-paginated.
- [`services/backend/src/backend_app/audit_chain.py`](../../services/backend/src/backend_app/audit_chain.py) — HMAC chain signer; PR 7.1 is a read-only consumer of fields it produces.
- [`services/backend/migrations/0002_audit_hardening.sql`](../../services/backend/migrations/0002_audit_hardening.sql) — append-only role + immutability triggers for the four backend streams.
- [`services/ai-backend/migrations/0003_audit_hardening.sql`](../../services/ai-backend/migrations/0003_audit_hardening.sql) — same for `runtime_audit_log`.
- [`services/backend/migrations/0004_identity_foundation.sql`](../../services/backend/migrations/0004_identity_foundation.sql) — `identity_audit_events` schema + indexes used as-is.
- [`services/ai-backend/migrations/0001_initial_runtime_persistence.sql`](../../services/ai-backend/migrations/0001_initial_runtime_persistence.sql) — `runtime_audit_log` schema + indexes used as-is.
- [`copilot_service_contracts.scopes.ADMIN_AUDIT_EXPORT`](../../packages/service-contracts/src/copilot_service_contracts/scopes.py) — RBAC scope reused by this PR.
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — stays unchanged; this PR is a non-event.
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md) — facade-only ingress; backend owns 4 streams, ai-backend owns 1, facade composes.
- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — adds `approval.forwarded` action this PR surfaces.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — adds `workspace.defaults.update`, `conversation.delete/restore/update` actions this PR surfaces.
- [`docs/new-design/pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) — landed the Members section; this PR connects the Audit log link.
- [`docs/new-design/pr-7.2-per-connector-token-attribution.md`](pr-7.2-per-connector-token-attribution.md) — sibling Wave 7 PR; independent merge.
