# PR 1.4.1 — Two-stage approval forwarding · production hardening

> **Status:** Spec · v1 · Owner: TBD · Target wave: W1‑late (sequenced AFTER PR 1.4 lands at `b931700`)
> **Scope:** `services/ai-backend` (membership resolver + expiry sweeper + notification port + inbox endpoint + chain depth column + metrics) · `services/backend` (members lookup + active-member bus) · `services/backend-facade` (proxy routes) · `apps/frontend` (member picker + assigned approvals hook) · `packages/api-types` (5 small additions)
> **Reads alongside:** [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md), [`00-plan.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md), `services/ai-backend/CLAUDE.md`, `services/backend/CLAUDE.md`, `apps/frontend/CLAUDE.md`.

---

## 1 · PRD

### 1.1 Problem

PR 1.4 landed the architectural anchor of two‑stage approval forwarding — atomic parent→FORWARDED + child INSERT, three SSE events, the `_decide_forwarded` branch, the worker skip‑resume guard, the audit row, and the FE inline transform. The piece that landed is correct in isolation, but **end‑to‑end the feature is dead** for ten distinct reasons, every one of which was knowingly punted in §1.3 / §10 of the original spec or fell out as I implemented:

1. The forward target's existence and active‑membership are never validated. A buggy or malicious client can forward to a non‑existent `user_id` and the chain hangs forever.
2. Pending approvals never expire. A forward to someone on vacation hangs the run indefinitely.
3. If the recipient's membership is revoked between forwarding and resolution, the chain orphans with no listener.
4. The backend allow‑list permits forwarding `mcp_auth` approvals; the FE hides the button. The two contradict and the semantics (whose OAuth identity ends up authenticated?) are confusing.
5. The notification dispatch is a no‑op. The recipient receives no Slack DM, email, or in‑app push — only the Approvals tab (which doesn't exist yet, see #6).
6. There is no recipient inbox endpoint. The FE has no way to discover that an approval has been assigned to the current user.
7. The chain depth read in `_chain_depth` returns `1` for any non‑null parent. With cap = 3 the practical cap is 2; the spec promised 3.
8. The in‑memory adapter's `forward_approval_request` doesn't enforce the parent‑must‑be‑PENDING race guard the postgres path does. Tests pass because tests run serially; production fan‑out across two browser tabs would silently double‑fork.
9. There are no metrics. No `forward_count`, no `chain_resolution_seconds`, no operational dashboard.
10. The FE picker is a free‑text input. A user can type any string, including a non‑existent user_id; today nothing rejects it.

The marquee Sarah → Marcus launch‑announcement flow is therefore **technically wired but operationally a dead end**. This PR closes all ten gaps without forking the harness, without duplicating the audit chain, without inventing a parallel notification bus, and without changing the LangGraph interrupt/resume contract.

### 1.2 Goals

1. **Server validates the forward target before any persistence write.** The first byte of work in `_decide_forwarded` is "is this a real, active member of this org?" — answered through a single port whose default impl is HTTP, with a TTL cache short enough to feel live and long enough to keep the hot path quiet.
2. **Pending approvals expire deterministically.** A periodic sweeper enqueues `RuntimeApprovalResolvedCommand(decision=REJECTED, reason="expired")` for any pending row past `expires_at`. The existing approval handler resumes the graph with a rejection. **No new code path on resolution.**
3. **Membership revocation cascades.** The same sweeper observes assigned approvals against current member status and rejects on stale assignments. **No new event bus** — we poll the resolver the sweeper already has.
4. **Resolve the `mcp_auth` ambiguity.** Drop `MCP_AUTH` from `APPROVAL_FORWARDABLE_KINDS`. The OAuth flow runs on the requester's identity, period. The FE button stays hidden for that kind, contracts agree.
5. **Notification dispatch fires in‑band but off the request thread.** A `NotificationDispatcher` port with a default in‑process adapter (logs only) and a production adapter that emits a per‑user `inbox_event_appended` SSE plus an outbound email through services/backend. Slack DM is W4.1.
6. **Recipient inbox endpoint** — `GET /v1/agent/approvals?assigned_to_me=true&status=pending&...` — returns a paginated list with parent‑chain context. Backed by the existing `idx_runtime_approval_requests_org_user_status_created` index; no schema change.
7. **Chain depth becomes a column.** `runtime_approval_requests.chain_depth` is set on insert (`parent.chain_depth + 1`); the guard reads it as O(1). Migration adds the column + backfill.
8. **In‑memory adapter raises on parent‑status race**, mirroring postgres' `WHERE status = 'pending'` semantic. Service translates to 409.
9. **Metrics flow through the existing OTel pipeline** (set up by C11). One counter, one histogram, three labels — no parallel dashboard.
10. **The FE picker becomes a typeahead** backed by a new minimal `GET /v1/workspace/members` route on `services/backend`. Until the W3.1 `useWorkspaceMembers` hook lands as part of @-mentions, this PR ships the hook + endpoint here so PR 1.4's UI is no longer free‑text.

### 1.3 Non‑goals

- **N‑level UI for chains > 1.** The schema + cap support depth = 3, but the FE only exposes one forward step (recipient cannot re‑forward in the picker). Adding the UI is W6+.
- **External recipient forwarding** (`forward_to.kind = "external_email"`). Still W6 alongside sharing — the email token vault + recipient table from sharing is the right home.
- **Slack DM dispatch.** The notification matrix UI lives in W4.1; this PR ships the `NotificationDispatcher` port and the in‑app SSE + email channels. Slack adds in a follow‑up that wires the same port.
- **Workspace members directory full surface.** `GET /v1/workspace/members?q=&limit=` is the minimum that unblocks the picker. Avatar URL, role decoration, and admin/billing filters belong in W4.2 (Members section).
- **Edit‑on‑forward.** Forwarding preserves `request_payload` byte‑for‑byte. If we ever support "edit when forwarding", that becomes a separate decision type.
- **Org‑scoped event bus for member deactivation.** Phase B uses sweeper polling. A real bus is W7+ and slots into the same dispatcher port without changing this PR's contract.

### 1.4 Success criteria

- A `POST /v1/agent/approvals/{id}/decision` with `forward_to.user_id` pointing at a non‑existent user returns **422** with the safe message `APPROVAL_FORWARD_INVALID_TARGET` and writes nothing to `runtime_approval_requests`. (Was: silently succeeded, hung the run.)
- A pending approval whose `expires_at` is past `now()` is auto‑rejected within `RUNTIME_APPROVAL_EXPIRY_TICK_SECONDS` (default 30s) of the deadline. Audit log records `actor_type=system`, `decision=rejected`, `reason=expired`. Run resumes through the existing path.
- A pending approval whose recipient is no longer an active member of the org is auto‑rejected within one tick after the membership record updates. Audit row records `reason=recipient_membership_revoked`.
- The marquee Sarah → Marcus flow: Sarah forwards → Marcus's frontend receives an `inbox_event_appended` SSE within ≤1s on a connected tab → Marcus's Approvals inbox lights up. Marcus approves → Sarah's chat repaints to the resolved record. Zero polling required when both are connected.
- `GET /v1/agent/approvals?assigned_to_me=true&status=pending` returns rows ordered by `created_at DESC`, each with `chain_parent_approval_id`, `forwarded_by_user_id`, `forwarded_at`, `action_summary`, and the parent's `conversation_id` for "open thread" navigation.
- Chain depth = 3 is honored: forwarding at depth 2 succeeds, depth 3 returns 422 `APPROVAL_FORWARD_CHAIN_TOO_DEEP`. Asserted by a unit test that walks parent → child → grandchild.
- In‑memory adapter raises on a second forward of the same parent in a serialized test. Returns the SAME 409 the postgres path returns. Asserted by `test_in_memory_forward_rejects_already_resolved`.
- Forwarding `mcp_auth` returns 422 `APPROVAL_FORWARD_KIND_NOT_SUPPORTED`. Asserted by a test that seeds an `mcp_auth` approval and tries to forward.
- OTel pipeline emits `approval_forward_total{decision_kind, depth}`, `approval_forward_invalid_total{reason}`, `approval_chain_resolution_seconds` (histogram). Visible on the existing pg_stat_statements/Prometheus scrape.
- The "Approve & forward to…" picker shows a typeahead of active workspace members; selecting one passes the `user_id` to `decideApproval`. Free‑text fallback removed.
- Net new code surface (excluding tests): ~700 LoC across all 10 gaps. No new background services, no new audit chain, no new persistence pattern beyond the chain_depth column.

### 1.5 User stories

| #     | Persona                                                     | Story                                                                                                                                                                                                                                                                                 |
| ----- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1  | Sarah forwards to a typo                                    | I type `marc` and submit. The picker tells me up front "no active member matches"; the API never sees the request. If I bypass the picker (curl, dev tools), I get 422 with a generic safe message.                                                                                   |
| US‑2  | Marcus is on PTO, `expires_at = +24h`                       | Sarah forwards Friday morning. Marcus is offline. By Saturday morning the approval has auto‑rejected; Sarah's chat shows "Rejected · expired by system." Sarah re‑runs with a different decision flow.                                                                                |
| US‑3  | Marcus is offboarded mid‑chain                              | Marcus's account is deactivated 30 minutes after Sarah forwards. The next sweeper tick rejects the chain. Audit log shows `reason=recipient_membership_revoked`; SIEM picks it up.                                                                                                    |
| US‑4  | Marcus has Atlas open in another tab                        | Sarah forwards. Within ≤1 second a "1 pending" badge lights up Marcus's Approvals tab via the per‑user SSE. He clicks, sees Sarah's draft, approves. Sarah's chat repaints. Zero polling.                                                                                             |
| US‑5  | Marcus has Atlas closed                                     | Sarah forwards. The notification adapter sends an email through the backend port. Marcus opens the link the next day, the inbox endpoint serves the row, he approves.                                                                                                                 |
| US‑6  | Sarah tries to forward an MCP‑auth approval                 | The picker is hidden. If she tries via the API directly, 422 `APPROVAL_FORWARD_KIND_NOT_SUPPORTED`.                                                                                                                                                                                   |
| US‑7  | Sarah forwards then closes the tab; Marcus forwards to Devi | Schema permits depth 2. UI in v1 doesn't expose Marcus's "forward again" button (Phase C tightens the FE). API allows it for completeness; depth 3 rejects with 422.                                                                                                                  |
| US‑8  | Auditor reviews                                             | A forwarded chain that auto‑expired produces three append‑only `runtime_audit_log` rows: the forward (`approval.forward`), the system rejection (`approval_decision_recorded`, actor_type=system), and the chain summary on the SIEM exporter side. Chain reconstructable end‑to‑end. |
| US‑9  | Operator                                                    | Grafana shows: forwards/min, mean chain‑resolution time, top reject‑reasons, count of rejected‑by‑expiry. Dashboard built from the OTel scrape; no new pipeline.                                                                                                                      |
| US‑10 | Concurrent UI                                               | Sarah double‑clicks "Approve & forward". Two requests fire. The losing one gets a clean 409 with the safe message `APPROVAL_FORWARD_NOT_PENDING`. UI surfaces a toast; chain is single‑forwarded.                                                                                     |

---

## 2 · Wire contract

This PR adds **one** new endpoint, **one** new event variant, **one** new column, and **five** tiny api‑types additions. Nothing else on the wire moves.

### 2.1 New endpoint — recipient inbox

```ts
// GET /v1/agent/approvals?assigned_to_me=true&status=pending&limit=50&cursor=<base64>
// Returns the caller's pending approvals (where requested_by_user_id == identity.user_id).
export interface AssignedApproval {
  approval_id: string;
  conversation_id: string;
  run_id: string;
  approval_kind: "action" | "mcp_tool" | string;
  status: "pending";
  // Forward chain context — present iff this approval was created via a forward.
  chain_parent_approval_id?: string | null;
  forwarded_by_user_id?: string | null;
  forwarded_at?: string | null;
  action_summary: string;
  risk_class?: "low" | "medium" | "high" | null;
  expires_at?: string | null;
  created_at: string;
}

export interface AssignedApprovalsResponse {
  approvals: AssignedApproval[];
  next_cursor: string | null;
}
```

Backed by the existing index `idx_runtime_approval_requests_org_user_status_created` — no new index needed. Pagination is opaque cursor of `(created_at, approval_id)`. RLS applies normally; `requested_by_user_id` filter narrows to the caller within their tenant.

### 2.2 New endpoint — workspace members directory (minimal)

```ts
// GET /v1/workspace/members?q=<prefix>&limit=20  (services/backend, proxied via facade)
// Returns active members of the caller's org whose display_name or email starts with `q`.
export interface WorkspaceMember {
  user_id: string;
  display_name: string;
  email: string;
  is_self: boolean; // helps the picker hide self-forwards in the UI
}

export interface WorkspaceMembersResponse {
  members: WorkspaceMember[];
}
```

Limited to active members; deactivated members never appear (this matches the post‑validation behavior of `WorkspaceMembershipResolver` for forward targets).

### 2.3 New event variant on the per‑user inbox SSE channel

```ts
// GET /v1/agent/me/inbox/stream?after_sequence=N (SSE)
// Emits when an approval is assigned to the connected user, or when an
// assigned approval transitions to a terminal state. One channel per user
// per session — separate from the run-scoped SSE which is per run.
export interface InboxEventEnvelope {
  sequence_no: number; // monotonic per (user_id, session)
  event_type: "approval_assigned" | "approval_resolved";
  approval_id: string;
  status: "pending" | "approved" | "rejected" | "expired";
  org_id: string;
  conversation_id: string;
  emitted_at: string;
}
```

One new SSE adapter, one new in‑memory bus, one route. The format matches the existing `RuntimeEventEnvelope` discipline (monotonic `sequence_no`, replay via `?after_sequence=N`) so the FE re‑uses the same reconnect helper.

### 2.4 Response shape extension

`ApprovalDecisionResponse` already carries `forwarded_to_user_id` + `child_approval_id` (PR 1.4). No change.

### 2.5 Decision‑request and event payloads — unchanged

`ApprovalDecisionRequest.forward_to`, `RuntimeApprovalForwardedEvent`, `ApprovalResolvedPayload.status="forwarded"` — all already in api‑types.

### 2.6 Schema delta

```sql
-- services/ai-backend/migrations/0018_approval_chain_depth.sql
ALTER TABLE runtime_approval_requests
    ADD COLUMN IF NOT EXISTS chain_depth INTEGER NOT NULL DEFAULT 0
        CHECK (chain_depth >= 0 AND chain_depth <= 3);

-- Backfill: a row whose chain_parent_approval_id is null stays at depth 0;
-- a row pointing at a depth-0 parent becomes depth 1, etc. Limit to small
-- batches to avoid table-rewrite during migration apply.
WITH RECURSIVE chain AS (
    SELECT id, 0 AS depth
      FROM runtime_approval_requests
     WHERE chain_parent_approval_id IS NULL
    UNION ALL
    SELECT child.id, parent.depth + 1
      FROM runtime_approval_requests child
      JOIN chain parent ON child.chain_parent_approval_id = parent.id
)
UPDATE runtime_approval_requests AS r
   SET chain_depth = chain.depth
  FROM chain
 WHERE r.id = chain.id AND r.chain_depth = 0;
```

The CHECK aligns with the runtime cap (`APPROVAL_FORWARD_MAX_CHAIN_DEPTH = 3`); raising the cap will mean changing both. Documented as a coupled invariant.

---

## 3 · Architecture

The ten gaps phase into three independent shippable PRs. Each is small, each is self‑contained, each respects the same DRY anchor: **forwarding stays bookkeeping; the LangGraph harness never learns about any of this.**

### 3.1 Phase A — production blockers (4 gaps · ~1 PR · M)

The minimum required to call the feature usable in production. Lands as a single coherent PR.

#### 3.1.1 Gap #1 — Workspace‑user existence + active‑membership check

**One new port, two adapters, one cache.**

```python
# services/ai-backend/src/agent_runtime/api/membership.py  (new)
class WorkspaceMembershipResolver(Protocol):
    """Resolve org-scoped membership for a user_id.

    The runtime calls this before accepting any cross-user write
    (forward target validation, future reassignments, etc). Implementations
    cache aggressively — a five-minute TTL is acceptable for membership
    state; stricter freshness comes from the deactivation sweeper (Gap #3).
    """

    async def is_active_member(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> bool: ...
```

Two impls:

- `HttpWorkspaceMembershipResolver` — calls `GET /internal/v1/users/{id}` on services/backend over the existing service-token mTLS lane. Returns `True` iff the response is 200 + the row's `org_id` matches AND `status == 'active'` AND `removed_at IS NULL`. Per‑(org, user) TTL cache (5 minutes by default, configurable). On 404 / mismatch / inactive: cache the negative answer for 30 seconds (much shorter — we want fast recovery once a member is added).
- `InMemoryWorkspaceMembershipResolver` — for tests. Initialized with a `dict[(org, user), bool]`.

The HTTP call uses the existing service-token + `x-enterprise-org-id` / `x-enterprise-user-id` headers per `services/backend/CLAUDE.md`. No new auth surface. The endpoint already exists — we just wire it.

In `_decide_forwarded`, the call lands inside `_guard_forwardable` BEFORE any DB write:

```python
# Pseudocode delta inside _guard_forwardable
if not await self._membership_resolver.is_active_member(
    org_id=approval.org_id, user_id=target.user_id
):
    raise RuntimeApiError(
        RuntimeErrorCode.VALIDATION_ERROR,
        Messages.Error.APPROVAL_FORWARD_INVALID_TARGET,
        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        retryable=False,
    )
```

**DRY anchors:** reuses the existing service-token auth, the existing `RuntimeApiError`, the existing 422 message. No new error code, no new audit channel.

#### 3.1.2 Gap #5 — Notification dispatch (production-meaningful, fire-and-forget)

**One new port, one default no‑op adapter, one production adapter.**

```python
# services/ai-backend/src/agent_runtime/api/notifications.py  (new)
class NotificationDispatcher(Protocol):
    async def notify_approval_assigned(
        self,
        *,
        approval: ApprovalRequestRecord,
        forwarded_by_user_id: str,
    ) -> None: ...

    async def notify_approval_resolved(
        self,
        *,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecision,
        decided_by_user_id: str,
    ) -> None: ...
```

Two impls:

- `LoggingNotificationDispatcher` — default. Emits structured logs at INFO level. Used in dev + tests.
- `InboxAndEmailNotificationDispatcher` — production. Calls:
  1. The new in‑process **inbox event bus** (Gap #6 below) to push an `approval_assigned` envelope to the recipient's per‑user SSE channel.
  2. The existing services/backend notification endpoint (`POST /internal/v1/notifications/email`, mirroring how mfa.py already sends emails) for the email channel.

Dispatch fires **after** the persistence transaction commits, **off** the request thread via `asyncio.create_task`. The request handler returns the `ApprovalDecisionResponse` immediately; notification failures log a structured warning but don't roll back the forward.

```python
# Pseudocode delta in _decide_forwarded after the txn block
async def _dispatch_post_commit() -> None:
    try:
        await self._notifications.notify_approval_assigned(
            approval=child, forwarded_by_user_id=request.decided_by_user_id
        )
    except Exception:
        logger.warning(
            "approval.notify_assigned.failed",
            approval_id=child.approval_id,
            target_user_id=child.user_id,
            exc_info=True,
        )

asyncio.create_task(_dispatch_post_commit())
```

Slack DM is W4.1 — the same port adds a third call site there.

**DRY anchors:** reuses the email endpoint already wired by `services/backend/src/backend_app/identity/mfa.py`. Reuses the `asyncio.create_task` post‑commit pattern already used by the polish‑enrichment in `RuntimeEventProducer._spawn_enrichment`. No new transport, no new retry queue (logging dispatcher is fine for v1; production reliability comes from #2 expiry + #3 cascade re‑converging the chain anyway).

#### 3.1.3 Gap #6 — Recipient inbox endpoint + per‑user SSE channel

**One new route, one new persistence query, one new in‑process event bus, one new SSE adapter.**

REST side:

```python
# services/ai-backend/src/runtime_api/http/routes.py  (extension)
router.add_api_route(
    "/approvals",
    RuntimeApiRoutes.list_approvals,
    methods=["GET"],
    response_model=AssignedApprovalsResponse,
    name=Keys.RouteName.LIST_APPROVALS,
)
```

Service:

```python
# services/ai-backend/src/agent_runtime/api/service.py  (extension)
async def list_assigned_approvals(
    self,
    *,
    org_id: str,
    user_id: str,
    status: ApprovalStatus,
    limit: int,
    cursor: str | None,
) -> AssignedApprovalsResponse: ...
```

Persistence:

```python
# new port method
async def list_assigned_approvals(
    self,
    *,
    org_id: str,
    requested_by_user_id: str,
    status: ApprovalStatus,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> Sequence[ApprovalRequestRecord]: ...
```

Hits the existing index `idx_runtime_approval_requests_org_user_status_created`. Cursor is opaque base64 of `(created_at, approval_id)` — stable across retries. RLS scopes by `org_id`; the `requested_by_user_id = $caller_user_id` filter is what enforces "assigned to me".

SSE side:

```python
# services/ai-backend/src/runtime_api/sse/inbox_bus.py  (new)
# services/ai-backend/src/runtime_api/sse/inbox_adapter.py  (new)

router.add_api_route(
    "/me/inbox/stream",
    RuntimeApiRoutes.stream_inbox,
    methods=["GET"],
    name=Keys.RouteName.STREAM_INBOX,
)
```

Adapter mirrors `RuntimeSseAdapter` but keys subscriptions by `user_id` instead of `run_id`. The bus is in‑process for v1; multi‑replica deployment will swap it for a Redis pub/sub or NATS adapter behind the same port (no contract change). Sequence numbers are per‑user, persisted in a tiny `inbox_event_cursors(user_id pk, latest_sequence_no)` table so the FE's `?after_sequence=N` reconnect works — same wire pattern as the run stream.

**Why a new channel and not a slot inside the existing run stream?** The recipient is _not a participant_ in the source run's conversation. Their identity has no business subscribing to the run‑scoped events. A separate per‑user channel keeps the visibility contract clean.

**DRY anchors:** the SSE adapter shape, the cursor pattern, the `?after_sequence=N` reconnect, the bus protocol — all mirror `runtime_api/sse/`. The index is reused, not new. The cursor table is one row per user; no migration RLS changes needed beyond `(org_id, user_id)` standard policy.

#### 3.1.4 Gap #8 — In‑memory race guard

A six‑line addition to `InMemoryRuntimeApiStore.forward_approval_request`:

```python
parent = self.approval_requests.get(parent_approval_id)
if parent is None or parent.org_id != org_id:
    raise KeyError(parent_approval_id)
# PR 1.4.1 — mirror postgres' WHERE status='pending' guard so a concurrent
# forward (or stale retry) deterministically loses the race. Service maps
# RuntimeError("approval_forward_parent_no_longer_pending") to 409.
if parent.status is not ApprovalStatus.PENDING:
    raise RuntimeError("approval_forward_parent_no_longer_pending")
```

**DRY anchor:** reuses the same `RuntimeError` shape `_decide_forwarded` already catches and translates to 409. Symmetrical postgres / in‑memory semantics.

---

### 3.2 Phase B — operational hardening (4 gaps · ~1 PR · M/L)

Robustness for production telemetry + lifecycle. Lands as a separate PR after Phase A.

#### 3.2.1 Gap #2 — Auto‑expiry sweeper

**One new periodic job, zero new resolution paths.**

```python
# services/ai-backend/src/runtime_worker/jobs/approval_expiry_sweeper.py  (new)
class ApprovalExpirySweeper:
    """Reject pending approvals whose expires_at has elapsed.

    Runs every RUNTIME_APPROVAL_EXPIRY_TICK_SECONDS (default 30) inside
    runtime_worker. Each tick:
      1. SELECT approval_id, run_id, org_id FROM runtime_approval_requests
         WHERE status = 'pending' AND expires_at < now() LIMIT 200;
      2. For each: enqueue a synthetic RuntimeApprovalResolvedCommand
         (decision=REJECTED, reason='expired', decided_by_user_id=SYSTEM_USER_ID).
      3. The existing approval handler picks it up and resumes the graph
         with Command(resume={"decisions":[{"type":"reject"}]}).
    """
```

The sweep is the entire mechanism. The runtime worker's existing handler does the resume; the existing audit emitter writes the `actor_type=system` row; the existing graph terminates the action. **Zero new resolution code.**

System actor: a sentinel `SYSTEM_USER_ID = "system:runtime"` constant in `agent_runtime/api/constants.py`. Audit's `actor_type` already supports `SYSTEM` (`AuditActorType.SYSTEM`). The decision record's `decided_by_user_id` carries the sentinel; the audit emitter sees it and records `actor_type=system` instead of `user`.

Idempotency: the sweep's `LIMIT 200 ... FOR UPDATE SKIP LOCKED` semantics mean two replicas can run the sweeper concurrently without double‑rejection. The enqueue + status update happen in one transaction; replays of the synthetic command are deduped by the existing approval handler's status check.

**DRY anchors:** mirrors `runtime_worker/jobs/retention_sweeper.py` exactly (loop cadence, batch size, `SKIP LOCKED`, structured logging). One new job, one new constant; no new audit shape, no new resume protocol.

#### 3.2.2 Gap #3 — Membership revocation cascade

The expiry sweeper's tick has cheap access to `WorkspaceMembershipResolver` (Gap #1). On each tick, after the time‑expiry sweep, it runs a second pass:

```sql
SELECT approval_id, run_id, org_id, requested_by_user_id
  FROM runtime_approval_requests
 WHERE status = 'pending'
 LIMIT 500;
```

For each row, call `is_active_member(org_id, requested_by_user_id)`. On `False`, enqueue the same synthetic rejection command with `reason='recipient_membership_revoked'`. The cache means a deactivated user is detected at most TTL seconds after the deactivation; the negative‑cache TTL (30s) is the bound on cascade latency.

**DRY anchors:** same sweeper, same enqueue path, same audit shape. One new reason code in the audit metadata.

#### 3.2.3 Gap #7 — Chain depth column

Migration 0018 (§2.6) adds `chain_depth` with backfill. Persistence layer:

```python
# in forward_approval_request, set the child's depth from the parent's column
child = child.model_copy(update={"chain_depth": parent.chain_depth + 1})
```

Service `_chain_depth` becomes a single attribute read:

```python
@classmethod
def _chain_depth(cls, *, approval: ApprovalRequestRecord) -> int:
    return approval.chain_depth
```

The cap check stays as `if depth >= APPROVAL_FORWARD_MAX_CHAIN_DEPTH`. With the column populated correctly the cap honors `MAX_CHAIN_DEPTH = 3` exactly.

**DRY anchors:** schema CHECK aligns with the runtime constant; raising the cap in code requires bumping the CHECK, surfaced as a coupled invariant in a unit test (`test_chain_depth_cap_matches_db_check`).

#### 3.2.4 Gap #9 — Metrics

```python
# services/ai-backend/src/agent_runtime/observability/approval_metrics.py  (new)
class ApprovalMetrics:
    """Per-process OTel meters for two-stage approval forwarding."""

    forward_total = Counter("approval_forward_total")          # labels: decision_kind, depth
    forward_invalid_total = Counter("approval_forward_invalid_total")  # labels: reason
    chain_resolution_seconds = Histogram(
        "approval_chain_resolution_seconds",
        explicit_buckets=[30, 60, 300, 1800, 3600, 86400],
    )
```

Three call sites:

1. `_decide_forwarded` success → `forward_total.inc(decision_kind=approval_kind, depth=str(child.chain_depth))`
2. `_guard_forwardable` rejections → `forward_invalid_total.inc(reason=<short-code>)`. Reason codes: `not_pending`, `kind_not_supported`, `target_invalid`, `chain_too_deep`, `self_forward`.
3. Worker resolution path on a child that has `chain_parent_approval_id`: histogram observes `now - parent.created_at`.

**DRY anchors:** reuses the existing OTel pipeline (C11). Metrics are exported through the same `db_statement_metrics.py` adjacent infrastructure. No new exporter, no new dashboard primitive.

---

### 3.3 Phase C — UX polish + contract narrowing (2 gaps · ~1 PR · S)

#### 3.3.1 Gap #4 — Drop `mcp_auth` from the forwardable set

```python
# services/ai-backend/src/agent_runtime/api/service.py
APPROVAL_FORWARDABLE_KINDS = frozenset(
    {
        Values.ApprovalKind.ACTION,
        Values.ApprovalKind.MCP_TOOL,
        # PR 1.4.1 — mcp_auth was previously listed but the OAuth flow
        # binds tokens to whoever completes it. Forwarding would either
        # silently rebind to the recipient's identity (footgun) or require
        # the requester to come back and re-auth (defeats the point). We
        # narrow the contract instead.
    }
)
```

A unit test asserts the API returns 422 `APPROVAL_FORWARD_KIND_NOT_SUPPORTED` for an `mcp_auth` forward request. The FE button gating already excludes `mcp_auth`; contract is now consistent.

**Anti‑pattern avoided:** silently allowing the path "to keep options open." Either we support the semantic with tests, audit, and UI — or we narrow it. Option B is cheaper for the same correctness.

#### 3.3.2 Gap #10 — Workspace member typeahead picker

Two pieces:

**Backend.** `services/backend/src/backend_app/routes/workspace_members.py` (new) exposes `GET /v1/workspace/members?q=&limit=20`. Pulls from the existing `users` + `organization_members` tables (`removed_at IS NULL` + `users.status = 'active'` + name/email LIKE prefix). RLS is the existing tenant policy; the route receives the caller's `org_id` from the trusted identity headers.

**Frontend.** `apps/frontend/src/features/workspace/useWorkspaceMembers.ts` (new) — React Query hook with debounced (200ms) prefix search. Returns `WorkspaceMember[]`.

`ApprovalTool.tsx` swaps the free‑text picker for `<WorkspaceMemberPicker>`:

```tsx
<WorkspaceMemberPicker
  excludeSelf
  onPick={(member) => submitForward(member.user_id)}
  onCancel={() => setForwarding(false)}
/>
```

The picker shows up to 8 results, keyboard navigable, auto‑highlights the first match. Hitting Enter on a non‑matching prefix submits nothing — the user must select a real row, OR we surface a "no matches" empty state.

**Anti‑pattern avoided:** building an autocomplete that _also_ allows free text. Either it's a picker (validated set) or it's free text (server validates). PR 1.4 was free‑text + server validation deferred. This PR is picker + server validation. No middle ground.

**Coordination with W3.1 (@‑mentions):** when @‑mentions land, both surfaces consume `useWorkspaceMembers`. The hook is the single source; the endpoint is the single source. No fork.

---

### 3.4 End‑to‑end sequence — the full Sarah → Marcus flow with all gaps closed

```
Sarah's tab          backend-facade        ai-backend (api)         backend (members)        runtime_worker         Marcus's tab
    │                      │                      │                       │                       │                      │
    │  Forward picker: type "marc" -> debounced GET /v1/workspace/members?q=marc                  │                      │
    │ ────────────────────▶│ ─────────────────────│──────────────────────▶│ list active members  │                      │
    │ ◀──── [ {id:"marcus", display:"Marcus T."} ] ─────────────────────────                       │                      │
    │ Sarah picks Marcus                                                                            │                      │
    │  POST /v1/agent/approvals/{id}/decision  decision=forwarded forward_to={user_id:"marcus"}   │                      │
    │ ────────────────────▶│ ─────────────────────▶│  _guard_forwardable                          │                      │
    │                      │                      │   ├─ kind in forwardable ✓                    │                      │
    │                      │                      │   ├─ not self ✓                               │                      │
    │                      │                      │   ├─ chain_depth < 3 ✓                        │                      │
    │                      │                      │   └─ membership_resolver.is_active_member?    │                      │
    │                      │                      │     ────────── GET /internal/v1/users/marcus ▶│                      │
    │                      │                      │     ◀─────────── 200 active ──────────────────│                      │
    │                      │                      │  forward_approval_request (atomic txn)        │                      │
    │                      │                      │  ├─ UPDATE parent → forwarded                 │                      │
    │                      │                      │  ├─ INSERT child (chain_parent, chain_depth+1)│                      │
    │                      │                      │  ├─ APPEND approval_resolved (forwarded)      │ ──── seq+1 ─────────│
    │                      │                      │  ├─ APPEND approval_forwarded                 │ ──── seq+2 ─────────│
    │                      │                      │  └─ APPEND approval_requested (child)         │ ──── seq+3 ─────────│
    │                      │                      │  audit: approval.forward                       │                      │
    │                      │                      │  metrics: approval_forward_total{depth=1} +1   │                      │
    │                      │                      │  asyncio.create_task(notify_approval_assigned)│                      │
    │                      │                      │     ├─ inbox_bus.publish(approval_assigned)   │ ───── push ─────────▶│
    │                      │                      │     └─ POST backend/notifications/email       │                      │
    │                      │  200 OK ◀────────────│                                                │                      │
    │ ◀─ inline card flips to "Waiting on @Marcus T. · forwarded by you · 10:41"                  │                      │
    │                                                                              ┌───────────────────────────────────────────│
    │                                                                              │ Marcus's Approvals tab badge → 1         │
    │                                                                              │ click → loads conversation read-only      │
    │                                                                              │ ApprovalTool renders "Forwarded by Sarah" │
    │                                                                              │ Marcus clicks Approve                     │
    │                                                                              └───────────────────────────────────────────┘
    │                      ◀── POST /v1/agent/approvals/{child}/decision  decision=approved ─────────────────────────────────│
    │                      │ ─────────────────────▶│  _decide_terminal (existing path)             │                      │
    │                      │                      │  audit: approval_decision_recorded            │                      │
    │                      │                      │  enqueue RuntimeApprovalResolvedCommand        │                      │
    │                      │                      │                       │                       │ worker handles:      │
    │                      │                      │                       │                       │ resume graph with    │
    │                      │                      │                       │                       │ Command(resume=approve)
    │                      │                      │                       │                       │ tool runs:           │
    │                      │                      │                       │                       │ post_to_slack(...)   │
    │                      │                      │                       │                       │ final_response       │
    │                      │                      │                       │                       │ metrics:             │
    │                      │                      │                       │                       │ chain_resolution_secs.observe(elapsed)
    │                      │                      │                       │                       │ inbox_bus.publish    │
    │                      │                      │                       │                       │ (approval_resolved)  │
    │ ◀─ inline card → "Approved by Marcus T. at 10:45 · Posted to #announcements" ─ run-stream SSE                       │
    │                                                                              ◀─ inbox SSE: approval_resolved ────────│
    │                                                                              │ Marcus's badge clears                 │
```

**The graph experiences exactly one resume.** The audit chain logs every transition. The metrics histogram captures the full Sarah‑clicks‑Forward to Marcus‑clicks‑Approve duration. Nothing in this flow is new infrastructure — it's the existing run stream + the existing approval handler + a tiny inbox channel.

---

## 4 · DRY / re‑use audit

| Need                       | Re‑used                                                                                                            | Why this beats a fork                                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| Forward target validation  | `services/backend`'s existing `users` + `organization_members` tables; existing `/internal/v1/users/{id}` route    | Backend already owns identity. ai-backend asks; doesn't duplicate. TTL cache absorbs hot-path cost.                  |
| Auto-expiry resolution     | The existing `RuntimeApprovalHandler` reject path                                                                  | Sweeper enqueues, handler resolves. Zero new graph code.                                                             |
| Membership cascade         | The same sweeper as expiry; the same membership resolver as Gap #1                                                 | One periodic loop, two checks per row.                                                                               |
| `mcp_auth` semantics       | Narrowing the allow-list rather than building OAuth-rebinding                                                      | Avoids a footgun the design never wanted to ship.                                                                    |
| Notification dispatch      | services/backend's existing email path (used by MFA); the existing per-user SSE pattern (mirrored, not forked)     | One channel per audience: notification port has email + in-app dispatcher; Slack adds in W4.1 against the same port. |
| Recipient inbox endpoint   | The existing `idx_runtime_approval_requests_org_user_status_created` index; the existing `RuntimeApiError` + RLS   | No new index, no new persistence pattern beyond the new query method.                                                |
| Per-user SSE channel       | The existing `RuntimeSseAdapter` shape, the existing `?after_sequence=N` reconnect, the existing structured logger | One new bus + one new adapter that LOOK like the run-scoped pair.                                                    |
| Chain depth                | One column + one CHECK                                                                                             | No recursive CTE on the hot path.                                                                                    |
| In-memory race guard       | The same `RuntimeError("…not_pending")` postgres raises                                                            | Identical 409 surface; tests parametrize over both adapters.                                                         |
| Metrics                    | The existing OTel pipeline (C11)                                                                                   | One file, three meters, three call sites.                                                                            |
| FE workspace member picker | `useWorkspaceMembers` is the same hook W3.1's @-mentions will consume; new endpoint serves both                    | One source, two consumers. No fork.                                                                                  |
| Audit                      | The existing append-only `runtime_audit_log` chain                                                                 | New reasons (`expired`, `recipient_membership_revoked`) are metadata strings, not new tables.                        |
| Authn                      | The existing service-token + identity headers                                                                      | No new auth surface introduced for the membership lookup.                                                            |

**Things we explicitly do not introduce:**

- A second audit chain.
- A second SSE protocol.
- An event bus for member deactivation (the sweeper polls the resolver; a real bus is W7+ and slots into the same dispatcher port without changing this PR's contract).
- An "approval expiry handler" — we use the existing reject path with a synthetic command.
- A resume‑side hook for expiry — the existing handler is the resume side.
- A separate retry queue for failed notifications (logging is fine for v1; production reliability comes from the chain re‑converging at the next sweeper tick anyway).
- A fork of `RuntimeSseAdapter` — the inbox adapter is a copy of the shape, not a subclass that pollutes the run-stream contract.
- A new exporter for metrics (we publish through the OTel pipeline already wired by C11).

---

## 5 · Code surface inventory

Approximate sizes are upper bounds.

### 5.1 `packages/api-types`

| File           | Change                                                                                                                               | Est. LoC |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| `src/index.ts` | `AssignedApproval`, `AssignedApprovalsResponse`, `WorkspaceMember`, `WorkspaceMembersResponse`, `InboxEventEnvelope` + 2 type guards | +75      |

### 5.2 `services/ai-backend`

| File                                                                 | Change                                                                                                        | Est. LoC |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | -------- |
| `migrations/0018_approval_chain_depth.sql` (+ rollback)              | new column, CHECK, recursive CTE backfill                                                                     | +35      |
| `src/agent_runtime/api/membership.py` (new)                          | `WorkspaceMembershipResolver` port + 2 impls + TTL cache                                                      | +150     |
| `src/agent_runtime/api/notifications.py` (new)                       | `NotificationDispatcher` port + 2 impls (logging + production composite)                                      | +180     |
| `src/agent_runtime/observability/approval_metrics.py` (new)          | OTel meters + helpers                                                                                         | +80      |
| `src/agent_runtime/api/service.py`                                   | wire resolver + dispatcher + `list_assigned_approvals`; tighten `_chain_depth`; drop MCP_AUTH from allow-list | +120     |
| `src/agent_runtime/api/constants.py`                                 | `Keys.RouteName.LIST_APPROVALS`, `STREAM_INBOX`, `SYSTEM_USER_ID`, audit metadata reasons                     | +20      |
| `src/agent_runtime/persistence/records/approvals.py`                 | `chain_depth: int = 0` field                                                                                  | +2       |
| `src/agent_runtime/api/ports.py` + `async_ports.py`                  | `list_assigned_approvals` port method                                                                         | +40      |
| `src/runtime_adapters/in_memory/runtime_api_store.py`                | race guard + `list_assigned_approvals` impl + `chain_depth` propagation                                       | +60      |
| `src/runtime_adapters/in_memory/async_runtime_api_store.py`          | wrapper for new method                                                                                        | +15      |
| `src/runtime_adapters/postgres/runtime_api_store.py`                 | `chain_depth` column wire-up; new SELECT for inbox                                                            | +120     |
| `src/runtime_adapters/async_wrappers.py`                             | wrap `list_assigned_approvals`                                                                                | +15      |
| `src/runtime_api/sse/inbox_bus.py` (new)                             | per-user in-process bus + cursor table                                                                        | +100     |
| `src/runtime_api/sse/inbox_adapter.py` (new)                         | SSE adapter                                                                                                   | +90      |
| `src/runtime_api/http/routes.py`                                     | `LIST_APPROVALS` + `STREAM_INBOX` + `list_approvals` + `stream_inbox` handlers                                | +90      |
| `src/runtime_api/schemas/approvals.py`                               | `AssignedApproval`, `AssignedApprovalsResponse`                                                               | +50      |
| `src/runtime_api/schemas/inbox.py` (new)                             | `InboxEventEnvelope` schema                                                                                   | +40      |
| `src/runtime_worker/jobs/approval_expiry_sweeper.py` (new)           | the sweeper itself                                                                                            | +200     |
| `src/runtime_worker/loop.py`                                         | register sweeper alongside retention_sweeper                                                                  | +10      |
| `src/runtime_worker/handlers/approval.py`                            | accept `decided_by_user_id == SYSTEM_USER_ID` from sweeper-enqueued commands; tag audit metadata with reason  | +20      |
| `tests/unit/runtime_api/test_approval_forwarding_hardening.py` (new) | full suite for §8                                                                                             | +600     |
| `tests/unit/runtime_worker/test_approval_expiry_sweeper.py` (new)    | sweeper unit tests                                                                                            | +250     |
| `tests/unit/agent_runtime/api/test_membership_resolver.py` (new)     | resolver + TTL cache tests                                                                                    | +180     |
| `docs/use-cases/16-approval-forwarding-expiry.md` (new)              | use case for expiry path                                                                                      | +120     |

### 5.3 `services/backend`

| File                                                                            | Change                                                                                                                        | Est. LoC |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------- |
| `src/backend_app/routes/workspace_members.py` (new)                             | `GET /v1/workspace/members?q=&limit=` + `GET /internal/v1/users/{id}` (if not already exposed for the resolver call — verify) | +120     |
| `src/backend_app/identity/store.py`                                             | active-member prefix-search query (uses existing index on `users.display_name` if present, else add index in migration)       | +50      |
| `src/backend_app/identity/notifications.py` (extend or new)                     | `/internal/v1/notifications/email` consumed by ai-backend's dispatcher (verify: MFA email path may already expose it)         | +0–60    |
| `migrations/0018_users_display_name_idx.sql` (only if no existing prefix index) | trigram or btree on `lower(display_name)` for prefix search                                                                   | +15      |
| `tests/test_workspace_members.py` (new)                                         | members route tests                                                                                                           | +180     |

### 5.4 `services/backend-facade`

| File                                                   | Change                                                                                 | Est. LoC |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------- | -------- |
| `src/backend_facade/routes/workspace_members.py` (new) | proxy `/v1/workspace/members` to backend                                               | +40      |
| `src/backend_facade/routes/agent.py`                   | proxy `/v1/agent/approvals` (list) and `/v1/agent/me/inbox/stream` (SSE) to ai-backend | +50      |

### 5.5 `apps/frontend`

| File                                                                                   | Change                                       | Est. LoC |
| -------------------------------------------------------------------------------------- | -------------------------------------------- | -------- |
| `src/api/agentApi.ts`                                                                  | `listAssignedApprovals`, `streamInboxEvents` | +80      |
| `src/api/workspaceApi.ts` (new)                                                        | `listWorkspaceMembers`                       | +35      |
| `src/features/workspace/useWorkspaceMembers.ts` (new)                                  | hook with debounce                           | +60      |
| `src/features/workspace/useAssignedApprovals.ts` (new)                                 | hook with SSE + polling fallback             | +90      |
| `src/features/workspace/WorkspaceMemberPicker.tsx` (new)                               | typeahead picker                             | +120     |
| `src/features/chat/components/tools/ApprovalTool.tsx`                                  | swap free-text for picker                    | +30      |
| `src/features/workspace/ApprovalsInboxPanel.tsx` (or extension to W3.2 `ApprovalsTab`) | "Pending on you" group                       | +80      |
| `__tests__/...`                                                                        | hook + picker + reducer tests                | +250     |

**Totals:** ai-backend ~2.4k LoC (incl. tests + use case); backend + facade ~600 LoC; frontend ~750 LoC; api-types ~75 LoC. Core net code (excluding tests + use cases) is **~1.9k LoC** across ten gaps.

---

## 6 · Edge cases & their resolutions

| Case                                                                       | Resolution                                                                                                                                                                                                                                                       |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Membership resolver TTL cache stale after admin removes Marcus             | Negative cache TTL is 30s; positive cache TTL is 5min. Worst case Sarah forwards and Marcus is already inactive but cached‑active: the row gets created, then the next sweeper tick (30s) catches the stale assignment via Gap #3 and rejects.                   |
| Membership resolver returns 5xx from backend                               | `_guard_forwardable` raises `RuntimeApiError(VALIDATION_ERROR, …, 503)` with `retryable=True`. Sarah retries; the cache means a flapping backend doesn't lock out forwards.                                                                                      |
| Sweeper runs on N replicas                                                 | `LIMIT 200 FOR UPDATE SKIP LOCKED` claims rows exclusively; replicas process disjoint batches. The synthetic command's `approval_id` is the dedup key inside the existing approval handler.                                                                      |
| Sweeper enqueues for a row that was approved between SELECT and UPDATE     | The handler's first read sees `status != PENDING` and returns 200 idempotent (already wired by PR 1.4).                                                                                                                                                          |
| Inbox SSE replay after a long disconnect                                   | Per‑user `inbox_event_cursors` table holds the latest_sequence_no; the FE reconnects with `?after_sequence=N` and the bus replays from that cursor. Bounded by retention (we cap at 7 days; older events drop).                                                  |
| Recipient subscribes to inbox SSE but their tab loses focus                | The hook (`useAssignedApprovals`) downgrades to a 60s poll on `visibilitychange=hidden` and re‑opens the SSE on `visible`. Battery‑friendly without giving up freshness.                                                                                         |
| Member picker query injection                                              | The backend route parameter `q` is sanitized to alphanumeric + space + dash + dot + `@`; SQL uses parameterized queries against an indexed `lower(display_name)`. No string concatenation.                                                                       |
| Picker shows deactivated member who was just reactivated                   | The negative‑cache TTL on the resolver bounds latency; the picker's own cache is React Query's default (60s) so a refetch on the next dropdown opens picks them up.                                                                                              |
| `mcp_auth` forward attempted via direct API after FE button is hidden      | Server returns 422 with `APPROVAL_FORWARD_KIND_NOT_SUPPORTED`. Audit logs the rejection. Metrics `forward_invalid_total{reason=kind_not_supported}` increments.                                                                                                  |
| Chain depth backfill fails partway                                         | Migration is wrapped in a transaction; partial backfill is rolled back. Operators re‑run; it's idempotent (the WHERE clause `r.chain_depth = 0` skips already‑backfilled rows on rerun).                                                                         |
| Forwarder tries depth 4                                                    | Cap check rejects with 422 `APPROVAL_FORWARD_CHAIN_TOO_DEEP`. CHECK constraint on the column would also reject; service-side check fires first.                                                                                                                  |
| Two pages in two tabs both forward simultaneously                          | One wins the postgres `WHERE status='pending'` race; the loser sees rowcount=0 and we raise `RuntimeError("approval_forward_parent_no_longer_pending")` → 409. The successful forward's notification fires once.                                                 |
| Email send fails inside notification dispatcher                            | Exception logged; the inbox SSE push has already happened (in‑memory, synchronous). On the next sweeper tick, if the recipient is still inactive (e.g. they don't have email at all), nothing fires; if they're active their next page load lands the inbox row. |
| Inbox SSE bus loses an event mid‑broadcast                                 | Cursor doesn't advance until the persistence write commits; the next reconnect catches up via replay. The bus is at‑least‑once; the FE reducer is idempotent on `(approval_id, sequence_no)`.                                                                    |
| FE shows a recipient picker while the recipient is deactivated mid‑session | Picker re‑queries when the user reopens the dropdown. The latest dropdown will exclude them; if the user already submitted with a stale entry, server‑side resolver rejects with 422. UX surfaces a toast.                                                       |

---

## 7 · Test plan

### 7.1 Unit (ai‑backend, ~35 cases)

Membership resolver:

- `resolver_returns_true_for_active_member`
- `resolver_returns_false_for_unknown_user`
- `resolver_returns_false_for_inactive_member`
- `resolver_returns_false_for_cross_org_member`
- `resolver_caches_positive_for_ttl_seconds`
- `resolver_caches_negative_for_short_ttl`
- `resolver_propagates_5xx_as_retryable_error`

Service:

- `decide_forwarded_calls_resolver_before_persist`
- `decide_forwarded_skips_resolver_when_cached`
- `decide_forwarded_rejects_inactive_target_with_safe_message`
- `chain_depth_read_returns_column_value`
- `chain_depth_cap_rejects_at_depth_three`
- `chain_depth_cap_matches_db_check_constant`
- `decide_forwarded_drops_mcp_auth_kind_with_422`
- `decide_forwarded_dispatches_notify_assigned_post_commit`
- `notify_assigned_failures_log_but_do_not_roll_back`
- `list_assigned_approvals_filters_to_caller`
- `list_assigned_approvals_pagination_round_trips`
- `list_assigned_approvals_includes_chain_context`
- `metrics_forward_total_increments_on_success`
- `metrics_forward_invalid_total_increments_on_each_reason`

In‑memory persistence:

- `forward_rejects_already_resolved_parent`
- `forward_rejects_with_runtime_error_signature_for_409_translation`
- `chain_depth_propagates_on_insert`

Postgres persistence:

- `migration_0018_round_trips`
- `migration_0018_backfill_correct_for_existing_chains`
- `chain_depth_column_check_rejects_value_4`

Sweeper:

- `sweeper_picks_up_expired_pending_rows`
- `sweeper_skips_resolved_rows`
- `sweeper_enqueues_synthetic_rejection_command`
- `sweeper_emits_audit_with_actor_type_system`
- `sweeper_idempotent_when_run_twice_concurrently`
- `sweeper_membership_pass_rejects_inactive_recipients`
- `sweeper_does_not_double_reject_already_rejected_rows`
- `sweeper_handles_500_resolver_with_backoff`

Worker:

- `approval_handler_accepts_system_actor`
- `approval_handler_records_reason_in_audit_metadata`

### 7.2 Unit (backend, ~12 cases)

- `members_route_filters_by_active_status`
- `members_route_excludes_removed_members`
- `members_route_search_by_display_name_prefix`
- `members_route_search_by_email_prefix`
- `members_route_caps_limit_at_50`
- `members_route_returns_is_self_correctly`
- `members_route_rls_denies_cross_org`
- `members_route_sanitizes_query_input`
- `internal_users_route_returns_404_for_unknown`
- `internal_users_route_returns_active_status_truthy`
- `internal_users_route_returns_inactive_for_removed_member`
- `internal_users_route_includes_org_id`

### 7.3 Frontend (~18 cases)

- `member_picker_typeahead_debounces_at_200ms`
- `member_picker_filters_self_when_excludeSelf`
- `member_picker_keyboard_navigation_works`
- `member_picker_empty_state_renders_no_matches`
- `member_picker_handles_5xx_gracefully`
- `assigned_approvals_hook_subscribes_to_inbox_sse_when_visible`
- `assigned_approvals_hook_falls_back_to_polling_when_hidden`
- `assigned_approvals_hook_idempotent_on_replayed_assigned_event`
- `approval_tool_swaps_free_text_for_picker`
- `approval_tool_submits_with_user_id_from_picker`
- `approvals_inbox_panel_renders_pending_on_you_group`
- `approvals_inbox_panel_click_navigates_to_source_thread`
- `inbox_event_envelope_idempotent_on_replay`
- `decide_approval_handles_422_invalid_target_with_user_message`
- `decide_approval_handles_409_already_resolved_with_user_message`
- `chain_pill_renders_resolved_state_when_child_resolves`
- `forward_picker_hides_for_mcp_auth_approvals`
- `forward_picker_hides_for_ask_a_question_approvals`

### 7.4 Integration

- `docs/use-cases/16-approval-forwarding-expiry.md` (new) covering Sarah forwards → 24h passes → auto-rejection.
- `docs/use-cases/17-approval-forwarding-membership-revoked.md` (new) covering Marcus offboarded mid-chain.
- Extend `docs/use-cases/15-two-stage-approval-forwarding.md` with the "concurrent double-click" race.
- E2E (Playwright, gated): full Sarah → picker → forward → Marcus tab inbox SSE → approve → Sarah's chat repaints. Asserts no polling fires while both tabs are visible.

### 7.5 Compliance check

- `runtime_audit_log` rows for `approval.forward`, `approval_decision_recorded` (system actor), and `inbox_event_appended` (if we audit inbox publishes — TBD; default is no, they're projection only) flow through the SIEM exporter.
- RLS denies cross-org inbox reads in a unit test.
- Notification dispatcher's email PII redaction matches the existing `ObservabilityRedactor` rules.
- Sweeper's batch SELECT respects RLS (the sweeper runs as `runtime_worker` role with cross-org read scope; verify against the existing C8 retention sweeper config).

---

## 8 · Rollout

Each phase ships behind one flag and turns default-on after one release of clean telemetry:

- **Phase A** behind `RUNTIME_APPROVAL_FORWARDING_HARDENING_ENABLED` (default off). Gates: membership resolver, notification dispatcher, inbox endpoint, in-memory race guard. With flag off the system behaves exactly like PR 1.4 (which is correct in isolation but unsafe operationally).
- **Phase B** behind `RUNTIME_APPROVAL_EXPIRY_SWEEPER_ENABLED` (default off). Gates: sweeper, chain depth column read (column itself is always populated; we just bypass the read until flag is on, falling back to the conservative `_chain_depth` from PR 1.4), metrics emission.
- **Phase C** is contract-narrowing (`mcp_auth` removal) and a frontend swap; no flag, ships as a normal feature change. The narrowing is backward-compatible for any caller that was honoring the FE's gate (which was every caller, since the API ignored the kind in practice given the FE never sent it).

Migration 0018 (chain_depth) is non-destructive and can land before Phase B turns on without operational impact (column is nullable-with-default, backfilled in the same transaction as the migration apply).

Backfill verification: the recursive CTE in 0018 should leave every existing row with depth = 0 since PR 1.4 has only just landed; a follow-up validation script asserts `SELECT MAX(chain_depth) FROM runtime_approval_requests` is at most 1 in production at the time of apply.

---

## 9 · Open questions (non-blocking)

- **Inbox SSE bus persistence vs. ephemeral.** v1 is in-process; multi-replica deployment will need Redis pub/sub or NATS behind the same port. The cursor table is durable so replays survive replica restarts; only live broadcasts depend on the bus. Worth a follow-up note in the operations runbook when we move past single-replica.
- **Notification email template ownership.** Today MFA's email goes through services/backend with a hard-coded body. We may want a small templating system for "approval assigned" / "approval resolved" emails. Defer until we have three notification kinds and stop duplicating bodies.
- **Picker recent-collaborators list.** Future polish: show "Marcus T. — collaborated on 4 docs this week" at the top of the dropdown. Requires a new analytics rollup; out of scope.
- **Reassign vs. forward.** Some workflows want "I should not be the requester anymore; reassign without me staying in the loop." That's a different decision (the original requester's context is preserved on forward; a reassign drops it). Not designed; follow-up if user feedback demands it.

---

## 10 · References

- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — the parent spec; this PR closes its open questions §10 plus the gaps surfaced during implementation.
- [`docs/new-design/00-plan.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md) — wave plan; this PR slots into W1‑late.
- [`services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`](../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py) — pattern for the new expiry sweeper.
- [`services/ai-backend/src/runtime_api/sse/`](../../services/ai-backend/src/runtime_api/sse/) — pattern for the new inbox SSE adapter.
- [`services/ai-backend/src/agent_runtime/observability/`](../../services/ai-backend/src/agent_runtime/observability/) — pattern for the new metrics module (C11).
- LangChain Human-in-the-Loop docs — the prebuilt middleware we already use, untouched: <https://docs.langchain.com/oss/python/langchain/human-in-the-loop>
- LangGraph interrupt + `Command(resume=...)` concepts: <https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/>
- `services/ai-backend/CLAUDE.md` — module boundaries; `_decide_forwarded` keeps to the api/ layer; sweeper keeps to the runtime_worker/jobs/ layer.
- `services/backend/CLAUDE.md` — backend owns identity; ai-backend asks via HTTP, never imports.
- `apps/frontend/CLAUDE.md` — Streamdown markdown rendering rule; activity_kind/display_title/summary/status projection rule.

---

## Appendix · gap → phase → file map (one-page reference)

| #   | Gap                            | Phase | New files                                                                                | Modified files                                         | Estimate |
| --- | ------------------------------ | ----- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------ | -------- |
| 1   | Workspace-user existence check | A     | `agent_runtime/api/membership.py`                                                        | `agent_runtime/api/service.py`                         | M        |
| 5   | Notification dispatch          | A     | `agent_runtime/api/notifications.py`, `runtime_api/sse/inbox_bus.py`, `inbox_adapter.py` | `service.py`                                           | M        |
| 6   | Recipient inbox endpoint       | A     | `runtime_api/schemas/inbox.py`, persistence query in adapters                            | `runtime_api/http/routes.py`, `service.py`, `ports.py` | M        |
| 8   | In-memory race guard           | A     | —                                                                                        | `in_memory/runtime_api_store.py`                       | XS       |
| 2   | Auto-expiry sweeper            | B     | `runtime_worker/jobs/approval_expiry_sweeper.py`                                         | `runtime_worker/loop.py`, `handlers/approval.py`       | M        |
| 3   | Membership revocation cascade  | B     | — (extends sweeper)                                                                      | `approval_expiry_sweeper.py`                           | S        |
| 7   | Chain depth column             | B     | `migrations/0018_*.sql`                                                                  | `service.py`, persistence records, adapters            | S        |
| 9   | Metrics                        | B     | `observability/approval_metrics.py`                                                      | `service.py`, sweeper, worker handler                  | S        |
| 4   | mcp_auth allow-list            | C     | —                                                                                        | `service.py`, tests                                    | XS       |
| 10  | FE workspace member picker     | C     | `WorkspaceMemberPicker.tsx`, `useWorkspaceMembers.ts`, `workspace_members.py` (backend)  | `ApprovalTool.tsx`, facade proxy                       | M        |
