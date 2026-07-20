---
id: findings-risks
kind: report
title: Risks & inconsistencies — correctness, security, compliance, wiring gaps
audit_date: 2026-07-20
---

# Risks & Inconsistencies

Behavioral defects and mismatches: security/access-control gaps, compliance controls that aren't durable, data that doesn't survive restart, wiring that shipped UI-first with the write path stubbed, and docs that misdescribe reality. Type = risk + inconsistency + correctness + test-coverage + dead-wiring.

Grouped by subsystem. Ordering within each: severity.

---

## Access control & auth

### RISK-rbac. RBAC ships default-permissive; enforcement is enabled in no deployment (both services)
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-identity, ai-runtime-api
**Merged sources:** backend-identity F1, ai-runtime-api F4.
`RBAC_MODE` defaults to `audit` (log-and-pass) in both `services/backend` and `ai-backend`, and a repo-wide search finds `RBAC_MODE=enforce` in **no** deploy config, Makefile, compose, or the desktop supervisor. So every `RequireScopes`/`RequireRoles` annotation (admin budgets, audit export, membership audit) is advisory in every shipped configuration — only `mfa:pending` and facade-level authentication actually block. The ai-backend "best-effort audit row on deny" reads `app.state.runtime_audit_appender`, which is assigned nowhere, so denies reach only structured logs.
**Evidence:** services/backend/src/backend_app/identity/rbac.py:42-50; services/ai-backend/src/runtime_api/rbac.py:43,223; deploy/self-host/docker-compose.prod.yml.
**Remediation:** Drive RBAC mode from the deployment profile (enforce for all production-class profiles) or flip the default to `enforce` with explicit opt-out; wire `runtime_audit_appender` or delete the dead branch; expose the active mode at `/v1/health`.
**Payoff:** turns the entire authorization layer from advisory to enforced.

### RISK-bearer-exp. Bearer `exp` is never verified on the HMAC path; core facade surface skips session touch
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** flow-auth, backend-facade
**Merged sources:** flow-auth F1, backend-facade F1, backend-facade F4.
`FacadeAuthenticator.verify_identity_token` checks signature + claim shape only — no expiry logic exists anywhere in the facade (grep `exp`: zero hits) though every bearer carries `exp`. Expiry is enforced *only* when a `sid` touch hits the session store. And the highest-value surface — all ~65 `app.py` handlers (`/v1/agent/*`, MCP, skills, usage, budgets) — call the *static* `authenticate_request` (0 `verify_with_touch`), so revoked/logged-out/expired bearers keep working there until the HMAC secret rotates. `atlas_pk_*` API keys (the CI-bot surface) are also 401'd on that entire surface because only `verify_with_touch` handles them. Sid-less dev bearers (365-day, DUP-11) are thus effectively eternal on every route.
**Evidence:** services/backend-facade/src/backend_facade/{auth.py:187-212,382-412,app.py:394,890,1032}; packages/service-contracts/.../auth_claims.py:17 (`CLAIM_EXPIRES_AT`).
**Remediation:** Route all `app.py` handlers through `verify_with_touch` (one-line once REF-2/DUP-9 land) and enforce `exp` in `verify_identity_token` — or adopt PyJWT which does both by construction (LIB-2). Add a regression test that a revoked session 401s on `/v1/agent/runs` and that an API key can create a run.
**Payoff:** closes token-revocation + expiry + API-key holes on the product's most sensitive routes.

### RISK-pepper. Personal-API-key HMAC pepper fails **open** in production
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** backend-core (F3)
When neither `api_key_pepper` nor `BACKEND_API_KEY_PEPPER` (≥16 bytes) is provided, `create_app` silently substitutes the hardcoded public constant `b"dev-only-pepper-NOT-FOR-PROD!"` with **no `BACKEND_ENVIRONMENT` guard** — inconsistent with the vault + email dispatcher, which fail closed. A prod operator who forgets the env var gets a defense-in-depth control silently disabled.
**Evidence:** services/backend/src/backend_app/app.py:1489-1508.
**Remediation:** Raise under `BACKEND_ENVIRONMENT=production` when the resolved pepper is < 16 bytes.

### RISK-amnesiac-identity. Backend image default CMD wires in-memory identity/session stores
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-identity (F6)
`Dockerfile:31` CMD is `backend_app.app:app`, whose defaults are `InMemoryIdentityStore`/`InMemorySessionStore`; only the self-host compose overrides to `desktop_app:app`. An operator running the published image as-is gets users/sessions that evaporate on restart with no boot-time refusal (unlike the token vault). Also: `PostgresSiweStore` is wired by *no* composition root (desktop_app.py never references siwe), so wallet→user links vanish on every restart and returning wallets re-provision into fresh orgs — migration `0035` tables are dead (backend-identity F2).
**Evidence:** services/backend/Dockerfile:31; app.py:493; desktop_app.py:175-235; siwe_store.py:145.
**Remediation:** Production-profile guard refusing in-memory identity/session stores under `BACKEND_ENVIRONMENT=production` (mirror the vault); wire `siwe_store=PostgresSiweStore(pool)` + a test asserting `desktop_app` passes a Postgres adapter for every store that has one.

### RISK-vault-swallow. `_default_token_vault` swallows the factory's fail-closed error → silent auth-route outage
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-platform, backend-core
**Merged sources:** backend-platform F5, backend-core F8.
`TokenVaultFactory.create` deliberately raises under KMS-required profiles with a `local` backend or missing KMS env, but `create_app` wraps it in `except Exception: return None`; a `None` vault silently skips the entire MFA/OIDC/Google block — a fail-closed control degraded to a silent feature outage. Same broad-except pattern hides misconfig for `_default_saml_verifier`, the connector catalog, and the desktop profile catalog.
**Evidence:** services/backend/src/backend_app/app.py:324-327,377-380,1622-1625,1678-1681; token_vault.py:461-482.
**Remediation:** Narrow the except to the expected missing-secret/dep errors; re-raise under production/managed profiles.

### RISK-ssrf. SSRF residual on MCP discovery + RPC proxy (DNS-rebind TOCTOU)
**Severity/confidence:** medium/high · **Verification:** confirmed (upgraded from plausible) · **Cluster:** backend-core (F7)
`validate_public_mcp_url` blocks only IP-literal private/reserved hosts; a hostname that *resolves* to 169.254.169.254 / 10.x passes the registration guard, and `mcp_oauth._fetch_first_json` + `_post_remote_mcp_rpc` re-resolve DNS via `urlopen` at fetch time — a TOCTOU / DNS-rebind window where the backend fetches from its own network position.
**Evidence:** services/backend/src/backend_app/{contracts.py:104-139,mcp_oauth.py:448-455,service.py:761-786}.
**Remediation:** Resolve+pin the IP and re-check it against the blocklist at fetch time; refuse redirects into private ranges (a natural byproduct of adopting `httpx`/`authlib` — LIB-1).

### RISK-tmp-storage. Hardcoded world-traversable `/tmp` storage defaults for tier-2 sources + library blobs
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-core (F9)
Adapter-registry source bytes default to `/tmp/atlas-adapter-registry`, library blobs to `/tmp/atlas-library-blobs`; a prod composer that omits the storage injection lands user uploads in a shared temp dir.
**Evidence:** services/backend/src/backend_app/app.py:1958-1995.
**Remediation:** Require an explicit data dir under `BACKEND_ENVIRONMENT=production`; do not default to `/tmp`.

### RISK-inbox-sse. Per-user inbox SSE cannot deliver across processes in the shipped topology
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-api (F3)
`InboxEventBus` is the in-memory singleton only (docstring admits the Postgres bus is "planned"), while the Dockerfile runs gunicorn with `WEB_CONCURRENCY=4` uvicorn workers plus a separate runtime-worker process. An approval decision handled by worker A never wakes a `/v1/agent/me/inbox/stream` subscriber on worker B.
**Evidence:** services/ai-backend/src/runtime_api/sse/inbox_bus.py:195; Dockerfile:7,34.
**Remediation:** Back the inbox channel with LISTEN/NOTIFY (`runtime_inbox_v1`) like the run bus, or set `WEB_CONCURRENCY=1` until it lands and document the constraint.

### RISK-auth-inconsistency. One deployment fact keyed off three independent env vars (+ dev fallbacks trust caller identity)
**Severity/confidence:** low-medium/high · **Verification:** confirmed · **Cluster:** flow-auth (F7, F8)
`BACKEND_ENVIRONMENT`/`RUNTIME_ENVIRONMENT`/`FACADE_ENVIRONMENT` each default to `development` and are read independently; omitting one silently flips that service into the dev trust posture (query-param / bare-header identity accepted) while the rest look production. Both prod artifacts set all three, but nothing cross-checks.
**Evidence:** services/backend/.../auth.py:99; services/ai-backend/.../auth.py:127; services/backend-facade/.../auth.py:486.
**Remediation:** A single `DEPLOYMENT_ENVIRONMENT` constant in `service-contracts` (or a boot-time cross-check that refuses a mixed posture); log a prominent warning when running in identity-open mode.

### RISK-session-binding. `REQUIRE_SESSION_BINDING` kill-switch implemented but enabled nowhere
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-auth (F2)
The facade rejects sid-less bearers when this is set, but no deployment artifact sets it — leaving the "externally-minted-token back-door" (the code's own words) open in every production-posture deployment. (Compounds RISK-bearer-exp and DUP-11.)
**Remediation:** Set it in the desktop supervisor + self-host compose once dev-mint mints `sid` bearers (DUP-11).

---

## Audit, retention & compliance

### RISK-audit-egress. Audit egress is façade-only: SIEM pump unwired + schema-broken; list/export empty under Postgres; deploy audit in-memory
**Severity/confidence:** high/high · **Verification:** confirmed (3 auditors) · **Clusters:** backend-platform, backend-core
**Merged sources:** backend-platform F1 + F2, backend-core F2.
- `SiemExportPump` is instantiated nowhere (no composition root/script/compose). If started, `_fetch_local` would fail — it queries `id > %(after_id)s` against `mcp_audit_events`/`identity_audit_events` whose PK is `audit_id` (column is `action`, not `event_type`); it also never reads the `siem_exporter_controls` rows its own docstring says it consults. Three deployment profiles set `siem_export_required=True`.
- `AuditReader` guards each stream with `hasattr(store, "list_audit_events")`; only the **in-memory** stores implement those readers — under `DATABASE_URL` (production, desktop) the mcp/skill/deploy streams silently vanish from `/internal/v1/audit/list` (not even reported in `degraded_streams`). `/internal/v1/audit/export` reads an in-memory attribute and streams nothing.
- Deploy audit defaults to `InMemoryDeployAuditStore` with **no table and no Postgres adapter** anywhere.
Per the repo's own compliance rules, audit export/deploy-audit must be marked **not implemented** for production-style deployments.
**Evidence:** services/backend/src/backend_app/siem_export/pump.py:158-320; audit_reader.py:220-276; routes/audit_export.py:101-123; store.py:405,1315-1322; migrations/ (no deploy_audit table).
**Remediation:** Wire the pump into a composition root/worker + fix its cursor SQL to `audit_id`/`created_at` + honor `siem_exporter_controls` + one-tick integration test against the real schema; implement `list_audit_events`/`list_skill_audit_events` on the Postgres stores + a `PostgresDeployAuditStore` + migration; make a missing reader a `degraded_streams` entry, not silence.
**Payoff:** makes the compliance-marketed audit-export control actually functional/durable.

### RISK-audit-pagination. Audit-list backward pagination is broken for real-world data (masked by an inverted-timestamp test)
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** backend-core (F1) — **type: correctness**
The unified feed sorts newest-first but pages each stream with a *forward* cursor (`seq > cursor`, cursor → `max(seq)` seen). When `seq` is positively correlated with `created_at` (production — `created_at` defaults to `now()`), page 1 returns the newest rows, the cursor jumps to max seq, and page 2 (`seq > max`) is **empty** — older audit rows are unreachable. The passing test only works because it seeds inverted timestamps (`when = now - index`).
**Evidence:** services/backend/src/backend_app/audit_reader.py:185-200,436-450; store.py:322-334; test_audit_list.py:200-207.
**Remediation:** Page by `created_at`/seq **descending** with a `before`-style cursor per stream (as identity already does); cover with a positively-correlated-timestamp test.

### RISK-identity-audit. `identity_audit_events` is the only unchained, untriggered audit stream
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-data (F6)
mcp/skill/runtime audit get seq/prev_hash/signature + `audit_writer` role + BEFORE UPDATE/DELETE triggers; `identity_audit_events` (logins, role grants, SCIM) has none — "append-only at the repo layer" only. The most security-sensitive stream has the weakest tamper evidence.
**Evidence:** services/backend/migrations/0002_audit_hardening.sql:8-67 vs 0004_identity_foundation.sql:116-136.
**Remediation:** Extend the 0002 chain+trigger pattern to identity (and deploy) events.

### RISK-legal-hold. Legal hold is checked everywhere and settable nowhere
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** flow-data (F5)
`runtime_legal_holds` is SELECTed by `delete_user_history` + sweep paths and the file store honors `metadata["legal_hold"]`, but no route/service/job/script inserts a hold row or sets the flag (repo-wide grep). For a compliance-marketed control (CLAUDE.md requires legal-hold tests), the enforcement gate is unreachable except by manual SQL.
**Evidence:** services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py:2096-2122; file/_deletion.py:47-63.
**Remediation:** Add an admin hold API + audit event, or stop advertising the control.

### RISK-retention-days. Privacy `retention_days` is a dead end (resolver hook has zero callers)
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** flow-data (F2)
`RetentionPolicyResolver.privacy_user_retention_days` is never passed at any of the seven construction sites; three docstrings claim the wiring exists. A user setting "auto-delete after N days" gets a stored row + a rendered UI value but no deletion behavior.
**Evidence:** services/ai-backend/src/agent_runtime/retention/policy_resolver.py:59,89.
**Remediation:** Fetch per-user privacy overrides in `sweep_once` (the internal policy route already serves them) or remove the knob.

### RISK-retention-gaps. No pruning path for outbox / approvals / tool invocations; retention semantics diverge per backend; desktop purge misses checkpoints
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** flow-data, ai-runtime-persistence
**Merged sources:** flow-data F7 + F8, ai-runtime-persistence F5.
`RetentionKind` covers only messages/events/context_payloads/checkpoints/memory_items; `runtime_outbox_events`/`runtime_tool_invocations`/`runtime_approval_batches` have no DELETE anywhere; approvals are status-flipped, never deleted. Retention/deletion behavior also diverges materially per backend with no capability signal: Postgres does chunked per-kind sweeps for 8 kinds; the file store collapses everything to whole-conversation purge on `MESSAGES` (with `backfill/recompute_retention_until` as `return 0` stubs); in-memory is a no-op — and `delete_user_history` tombstones on Postgres but erases bytes on file. Desktop physical purge does **not** cascade to `index/checkpoints.sqlite3` (no `delete_thread` call anywhere), so "delete my data literally" leaves message content in checkpoint channel values.
**Evidence:** services/ai-backend/src/agent_runtime/persistence/records/retention.py:36-43; runtime_adapters/{postgres:3649-4303,file:2656-2722,in_memory:1027-1053}; deep_agent_builder.py:339-349.
**Remediation:** Add outbox pruning after consumer-cursor advance + approval kinds to the sweeper; call `adelete_thread(conversation_id)` inside `_purge_conversations`; document the per-backend retention/encryption matrix (couples to REF-god-port capability descriptor) and/or expose a capabilities descriptor so the sweeper + compliance tooling assert what a deployment enforces.

---

## Data durability

### RISK-product-inmemory. All backend product destination stores are in-memory in every shipped composition
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-product, flow-data
**Merged sources:** backend-product F2, flow-data F1.
No `Postgres*Store` exists for todos/inbox/routines/connectors/webhooks/projects/library/memory/agents/tools/palette; the SaaS image default boots all-in-memory, and desktop/self-host boot `desktop_app` which lists every destination store as an "accepted desktop-v1 gap". Ten `schema.sql` files have no migration; `0032_todos.sql` creates a table nothing reads. Product data (and each module's audit records) does not survive restart anywhere — including the packaged desktop.
**Evidence:** services/backend/src/backend_app/desktop_app.py:153-172; Dockerfile:31; deploy/self-host/docker-compose.prod.yml:103-108.
**Remediation:** Write Postgres adapters for the destinations that matter for launch (todos/projects/memory at minimum) against the existing schema.sql, generate migrations, delete/quarantine the rest.

### RISK-postgres-approval. PostgresRuntimeApiStore is missing 3 `PersistencePort` approval methods → HTTP 500 on the assigned-approvals inbox
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence (F1)
`list_assigned_approvals`, `list_pending_expired_approvals`, `list_pending_approvals_for_membership_audit` are declared on the port and implemented by in-memory + file but **absent** from the entire postgres package. `list_assigned_approvals` is live (`routes.py:354` → `approval_coordinator.py:140`), so on `RUNTIME_STORE_BACKEND=postgres` the endpoint raises `AttributeError` (500); the other two are masked only because their caller (the expiry sweeper) is itself never wired (DEAD-3). Structural-Protocol typing let the incomplete impl boot cleanly — the mechanism REF-god-port fixes.
**Evidence:** services/ai-backend/src/agent_runtime/api/ports.py:342,357,365; runtime_adapters/postgres/runtime_api_store.py (grep: zero hits).
**Remediation:** Implement the three methods in the Postgres adapter; add a static per-adapter Protocol-conformance test (catches this without a DB).

### RISK-citation-split-brain. Citation store wiring is split-brain on non-Postgres backends (Sources feed always empty)
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence (F2)
`RuntimePorts` has no `citation_store` field, so `RuntimeWorker` improvises `self.persistence if isinstance(..., CitationStorePort) else InMemoryCitationStore()`. Postgres implements `insert_many_or_get`; File + in-memory stores do not, so the worker writes citations to a **private throwaway** store while the API reads a different one — Workspace "Sources" is always empty on desktop/file and dev/in-memory, `FileCitationStore` durability is never exercised, and citation rebuild-on-resume is impossible off-Postgres.
**Evidence:** services/ai-backend/src/runtime_worker/loop.py:74-80; runtime_adapters/factory.py:93,105,191.
**Remediation:** Add `citation_store` to `RuntimePorts`, wire the correct impl per backend in the factory, pass it explicitly into the worker, delete the isinstance fallback.

### RISK-checkpoints. On Postgres, LangGraph checkpoints live in `InMemorySaver` — approval/graph continuation dies on worker restart
**Severity/confidence:** high/medium · **Verification:** confirmed · **Cluster:** flow-data (F4)
Only `RUNTIME_STORE_BACKEND=file` gets a durable `AsyncSqliteSaver`; postgres/web keep a process-local `InMemorySaver` and `runtime_checkpoints` is never written (DEAD-7). The shared-store production path has durable events but volatile graph state. Fix is a maintained library (LIB-4).
**Evidence:** services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:301-349.
**Remediation:** Adopt `PostgresSaver` (LIB-4) or document the restart-behaviour gap loudly.

---

## Run lifecycle & streaming

### RISK-worker-serial. Worker runs commands serially; cancel cannot preempt; terminal states overwrite each other
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** ai-runtime-worker, flow-run-streaming
**Merged sources:** ai-runtime-worker F3, flow-run-streaming F2 + F3.
`run_forever` → `run_once` awaits the entire run inside a single claim, so both production and the in-process desktop worker process commands serially — `max_parallel_runs`/semaphore machinery is exercised only by tests. Consequences: throughput is one run at a time; a `RuntimeCancelCommand` for the running run cannot be claimed until it finishes; and `RuntimeRunHandler` never checks `CANCELLING` mid-stream (a cancelled run keeps burning tokens to completion). `update_run_status` enforces row-version CAS but not transition legality, and the completion write goes through `with_optimistic_retry` (refetch-and-retry) which *defeats* the CAS — so `CANCELLED` → `COMPLETED` overwrites succeed and clients can observe both terminal events.
**Evidence:** services/ai-backend/src/runtime_worker/loop.py:118-170; streaming_executor.py:269-426; handlers/{run.py:334,545-549,cancel.py:46-51}.
**Remediation:** Dispatch claims as tasks bounded by the existing semaphore; add a legal-transition guard in `update_run_status` (terminal states immutable) + a cooperative cancellation flag the streaming loop polls.

### RISK-approval-resume. Approval-resume completion skips usage recording, budget charge, audit, tool reconciliation, and the timeout
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-worker (F2)
`_complete_run_with_result` (approval path) only appends the message + `FINAL_RESPONSE` + terminate, whereas the run path additionally records usage (which charges budgets), emits `run_completed` audit, reconciles in-flight tool calls, and wraps streaming in `asyncio.timeout`. Runs that pause for approval and then finish are **unmetered, unbilled, unaudited, and can hang the worker forever**.
**Evidence:** services/ai-backend/src/runtime_worker/handlers/approval.py:552-628 vs run.py:456-572.
**Remediation:** Extract a shared `RunCompletionFinalizer` (usage + audit + budget + reconciliation + termination) used by both handlers (couples to DUP-16 / REF-11); wrap resume streaming in the same timeout.

### RISK-tcswimlanes. Run cockpit opens a second SSE subscription that receives zero events (and violates single-projection)
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** chat-surface-core, flow-run-streaming
**Merged sources:** chat-surface-core F1, flow-run-streaming F1.
`TcSwimlanes` opens its own uncursored SSE to `/v1/agent/runs/{id}/stream` (violating the FR-3.3 single-projection invariant + doubling backend stream load) *and* omits `eventName`, which the transports default to `"message"` while the backend frames `event: runtime_event` — so against the real stack this subscription **receives zero events** and the swimlane timeline never populates from it.
**Evidence:** packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx:157-186; destinations/run/useRunSession.ts:20-23,229; runtime_api/sse/adapter.py:112.
**Remediation:** Delete the second subscription and feed TcSwimlanes from the projector's `beads` slice (the projector already computes beads-with-lanes — this also un-deads part of DEAD-11).

### RISK-consent-labels. Consent-card risk labels come from tool-name substring heuristics (understate destructive actions)
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-worker (F7)
`_connector_action_is_read_only` marks any tool lacking create/post/send/update/delete/write substrings as read-only → `risk_level="low"`, `reversible=NOT_APPLICABLE` — so `remove_member`, `merge_pr`, `drop_table`, `execute_query` render as low-risk reads. The `_APPROVAL_IRREVERSIBLE_TOKENS` constant is defined but never referenced. Approval is still required, but the card actively understates risk to the human deciding.
**Evidence:** services/ai-backend/src/runtime_worker/stream_events.py:894-955.
**Remediation:** Prefer MCP `readOnlyHint`/`destructiveHint` annotations; fail toward "write/medium" for unknown verbs; wire or delete the irreversible-token list.

### RISK-worker-leak. Per-run in-memory state leaks in the long-lived worker
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-worker (F8)
`StreamMessageProcessor._tool_call_states/_tool_call_ids` and `StreamUpdateProcessor`'s four `_subagent_*` dicts are never purged per-run (`discard_ledger`/`discard_metrics` clear only ledgers/buffers/metrics); `RuntimeApprovalHandler._resumed_task_ids` grows forever. Unbounded growth keyed by run_id/call_id in a process meant to run for weeks.
**Evidence:** services/ai-backend/src/runtime_worker/{stream_tools.py:85-88,stream_subagents.py:52-59,handlers/approval.py:177}.
**Remediation:** Add `discard_run(run_id)` on the orchestrator sweeping all per-run keys, called from the three terminal sites.

### RISK-approval-fireforget. Approval decisions are fire-and-forget → can show a permanent false receipt
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** chat-surface-destinations (F8)
`resolveApproval` overlays the optimistic decision then POSTs with `.catch(() => {})`; if the POST fails *and* the stream is down (the exact co-failure case), the user sees a permanent "approved/rejected" receipt for an approval the server still considers pending — a false audit impression on a consent surface.
**Evidence:** packages/chat-surface/src/destinations/run/RunDestination.tsx:450-471.
**Remediation:** On POST rejection, roll back the optimistic overlay + surface a toast via `NotificationCenterProvider`.

### RISK-approval-projection. Latent `reject`/`rejected` decision-value bug in the (currently dead) projector approval path
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** chat-surface-core (F11)
`eventProjector.nextApprovalState` branches on `decision === "reject"`, but the wire emits past-tense `"rejected"`, so a rejected approval would project as `state: "accepted"`. Harmless *today only because that slice is dead* (DEAD-11) — a booby trap for whoever wires it. The live `approvalProjection.decisionFromResolve` checks the correct values.
**Evidence:** packages/chat-surface/src/thread-canvas/eventProjector.ts:288-297,498-507; services/ai-backend/src/runtime_worker/handlers/approval.py:652-653.
**Remediation:** Fix to `"rejected"`/`"suggest_edit"` (or delete per DEAD-11); source the decision union from api-types' `ApprovalDecision`.

### RISK-sse-keepalive. No keepalive on the run SSE stream + no clean-EOF/auto-reconnect in the cockpit
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-run-streaming (F7)
Idle runs (waiting for approval) write zero bytes — `RuntimeSseAdapter` has no keepalive in follow mode while `InboxSseAdapter` sends `: keepalive` every 25s (and its docstring *falsely* claims runtime parity). Client-side, `runSseStream` fires no callback on clean EOF and `useRunSession` never auto-reconnects (manual `retry()` only) — a proxy dropping the idle stream freezes the cockpit at "streaming". (SSE poll-backstop also ignores the per-backend `fallback_poll_seconds`, so every client re-runs the replay query every 2s even on Postgres — ai-runtime-api F6.)
**Evidence:** services/ai-backend/src/runtime_api/sse/{adapter.py:41-70,inbox_adapter.py:26-30}; packages/chat-transport/src/web/sse.ts:58-61; useRunSession.ts:251-296.
**Remediation:** Add the keepalive comment frame to `RuntimeSseAdapter`; add an `onClose` to the SSE runner + auto-resubscribe from the cursor (the legacy `ChatScreen` already implements reconnect); read `event_bus.fallback_poll_seconds`.

---

## Connectors / MCP

### RISK-connectors-stub. Web Connectors/Tools destination connect flow is a stub end-to-end; read model never populated
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-product, flow-mcp
**Merged sources:** backend-product F1, flow-mcp F1.
`POST /v1/connectors/{slug}/start-oauth` returns a hardcoded `https://auth.example/{slug}/authorize?state=stub` (nothing sets `app.state.connector_oauth_start`); `POST /v1/connectors/oauth-callback` always 503s; `ConnectorsService.write_through_from_mcp` (the only writer) has no caller; only `InMemoryConnectorsStore` exists. So the live Tools destination always renders an empty Connected tab, web "Connect" opens a fake URL, and even a *successful* desktop AC9 connect never appears in `/v1/connectors` — while the real connector state is fully functional one screen away via `/v1/mcp/servers`. This is the debt driving the connector-representation sprawl (SSOT-5).
**Evidence:** services/backend/src/backend_app/connectors/routes.py:281-346,557-568; service.py:269; app.py:1618-1660.
**Remediation:** Wire `start-oauth`/`oauth-callback` to `McpRegistryService.start_auth`/`complete_auth` + call `write_through_from_mcp` from the callback (the desktop coordinator shows the shape), **or** delete the read model and project `/v1/connectors` directly off the MCP store; until then hide the web Connect button behind capability detection.

### RISK-rpc-sse. Internal MCP RPC proxy flattens SSE to the first `data:` event with no id matching (+ blocking urllib)
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** flow-mcp (F5)
`_decode_remote_mcp_response` returns the first non-`[DONE]` `data:` line of a `text/event-stream` response with no JSON-RPC id check — a `notifications/progress` emitted first is returned as the tool result and the real response dropped. The call is synchronous `urlopen(timeout=30)` inside the request path (blocking double hop serialized through the threadpool).
**Evidence:** services/backend/src/backend_app/service.py:766-803.
**Remediation:** Match on JSON-RPC id; use async `httpx` (natural byproduct of LIB-1).

---

## Desktop & distribution

### RISK-wallet-assets. Packaged DMG/NSIS builds never ship the staged web assets → wallet sign-in 404s there
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** flow-desktop-boot (F1)
`stage.mjs` stages `wallet.html` to `<dest>/web` and `resolveRuntimePaths` expects `<base>/web`, but `electron-builder.yml` maps only `resources/runtime` into `resourcesPath` — `resources/web` is dropped, so the facade logs "wallet page not served" and `wallet.html` 404s in packaged builds. (CLI installs are unaffected.)
**Evidence:** tools/desktop-runtime/stage.mjs:713-731; apps/desktop/electron-builder.yml:36-40; services/backend-facade/src/backend_facade/wallet_page_routes.py:54.
**Remediation:** Add `- from: resources/web` `to: web` to `extraResources`.

### RISK-broker-unwired. AC5 capability broker token/URL never delivered to the ai-backend child → host-folder grants can't work end-to-end
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** desktop-app (F2)
`startCapabilitySubsystem` mints the per-boot broker token but `buildServiceEnv` sets no `DESKTOP_BROKER_URL`/`DESKTOP_BROKER_TOKEN` (and the passthrough allowlist strips them), while `workspace_backend.py` waits for exactly those vars. The renderer grants UI/picker/broker all run against no consumer.
**Evidence:** apps/desktop/main/{index.ts:166-207,services/service-env.ts:11-248}; services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py:123-124.
**Remediation:** Complete the "slice 2" wiring — inject broker url+token into the ai-backend child env; until then gate the renderer grant UI off so users can't mint grants nothing honors.

### RISK-desktop-boot. Supervised ai-backend env omits `BACKEND_BASE_URL` (Null resolvers); dev supervised recipe's default sign-in cannot succeed; local-models flag never set
**Severity/confidence:** high/high (BASE_URL) · **Verification:** confirmed · **Cluster:** flow-desktop-boot (F2, F5, F4)
At the audit base, the supervised ai-backend env omits `BACKEND_BASE_URL`, so project/membership/notification/routine/policy resolvers fall back to Null (the confirmed "BYOK runs broken on desktop" bug; fixed post-base by `bcc65dbb`/#114 — verify it covers `run-local.mjs`, SSOT-7). Separately, the documented `COPILOT_RUNTIME_DIR npm run dev` recipe supervises with children at `*_ENVIRONMENT=production` (no dev-mint route) but *without* `COPILOT_PRODUCTION=1` resolves DEV posture, so the "Sign in (local)" button routes to dev-mint and always fails against its own stack (only wallet/Google work). `RUNTIME_ENABLE_LOCAL_MODELS` is documented in the boot contract + set by `run-local` but the real supervisor never sets it, so the shipped desktop lacks the Local-models section the docs promise.
**Evidence:** apps/desktop/main/services/service-env.ts:174,195,228-236; posture.ts:24-28; apps/desktop/main/index.ts:548-551; tools/desktop-runtime/README.md:70.
**Remediation:** Add `BACKEND_BASE_URL` + `RUNTIME_ENABLE_LOCAL_MODELS` to `buildServiceEnv`; treat `shouldSupervise` as a production-posture input (or dev-gate the supervised children's env).

### RISK-boot-secrets. Desktop boot-secrets plaintext fallback is unconditional and unwarned
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** desktop-app (F6)
When safeStorage is unavailable, `persist()` writes `ENTERPRISE_AUTH_SECRET`, pg password, vault secret, audit HMAC key as chmod-600 plaintext with no `allowPlaintextFallback` gate and no warning — whereas the sibling stores (secret-storage, grant-store) fail closed without an explicit dev flag.
**Evidence:** apps/desktop/main/services/boot-secrets.ts:103-116.
**Remediation:** Emit a visible degraded-security signal (log + boot status) and align the three stores on one policy knob.

### RISK-harness-userdata. Live-smoke harness dev posture writes into the real prod `userData` (phantom isolation)
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** desktop-distribution (F4)
`driver.mjs:93` sets `COPILOT_DESKTOP_USER_DATA_SUBDIR="cli-test-dev"` "so the dev session never collides with prod", but nothing in `apps/desktop` reads it — the isolation is imaginary, so dev-posture smoke runs share the production `~/Library/Application Support/0xCopilot` secrets/pgdata and can corrupt a real install.
**Evidence:** tools/cli-testing/harness/driver.mjs:93; apps/desktop/main/index.ts (uses `app.getPath("userData")` directly).
**Remediation:** Implement the env var in main (`app.setPath("userData", ...)` before ready), or delete the dead line and set `COPILOT_HOME` + a distinct app name in the harness.

### RISK-publish. Manual, unreproducible npm publish folds a gitignored credential into the public tarball
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** desktop-distribution (F5)
No publish workflow exists; `@0x-copilot/cli` is published from a maintainer machine where `prepack` copies `apps/desktop/google-oauth.json` (gitignored, may contain `client_secret`) into the payload. Payload content depends on uncommitted local state with no CI attestation.
**Evidence:** tools/cli/scripts/assemble-payload.mjs:205-229; apps/desktop/.gitignore:15-18.
**Remediation:** Publish from a workflow (env-synthesized OAuth *id* only — desktop PKCE clients need no secret); fail prepack if `google-oauth.json` contains `client_secret`.

### RISK-stager-untested. Zero behavioral tests + zero CI for the stager and destructive CLI code
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** desktop-distribution (F7)
No `*.test.*` in any of the three tool dirs; `ci-cli.yml` runs only syntax/help-smoke/manifest-check; no workflow path-filters `tools/desktop-runtime/**`. The untested code includes `rmSync`-adjacent logic in `uninstall.mjs`/`repair.mjs` and `mac-shell` plist surgery — bugs here delete user data or brick launches.
**Evidence:** .github/workflows/{ci-cli.yml,ci-desktop.yml}; tools/cli/lib/{uninstall.mjs:206,repair.mjs:119}.
**Remediation:** Unit-test `isSafeTarget`/pid-parsing/process-matching with `node:test` (zero-dep); add `node --check` for desktop-runtime + a path filter; consider a weekly scheduled `run-local.mjs` job.

---

## Build / CI

### RISK-no-ci-tests. CI runs no tests for frontend / chat-surface / chat-transport / surface-renderers — the SSOT interaction layer is untested in CI
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** build-deploy, chat-surface-core
**Merged sources:** build-deploy F2, chat-surface-core F0.
`ci-frontend` only typechecks + builds; the only `npm run test` in any workflow is desktop's. 157 frontend + 186 chat-surface test files (plus chat-transport/surface-renderers) never execute in CI — and `ci-frontend` doesn't even *trigger* on `packages/chat-surface/**`, so a chat-surface regression ships to both hosts unobserved. chat-surface's own eslint (the substrate-boundary "one hard rule") currently fails with 11 errors, proving it runs nowhere.
**Evidence:** .github/workflows/ci-frontend.yml:4-52; ci-desktop.yml:75; packages/chat-surface/package.json:11.
**Remediation:** Add `npm run test`/`lint`/`typecheck` steps for frontend + the three packages (a `ci-packages` job with matching path filters); add a test-file eslint override so the boundary rule stays strict for production code.

### RISK-branch-protection. Branch-protection applier can never run (SyntaxError) + would deadlock merges if applied + staged-rollout gate always fails non-canary
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** build-deploy (F1, F8, F4)
`apply-branch-protection.yml` uses top-level `return` in inline Python (compile-time SyntaxError) — every invocation fails, so branch protection is unenforced aspiration (`bypass_actors: []`). If applied, its `required_status_checks` name path-filtered workflows that never report on unrelated PRs (deadlock) and three backend workflows share the job name `test-and-audit`. The production staged-rollout gate synthesizes prior records as `{tier:"unknown",environment:"unknown"}` while `check_staged_rollout.py` counts only `tier==upstream AND environment=="production"`, so `early`/`general` always exit 1 and can only proceed with `force_deploy=true` — institutionalizing the bypass the policy exists to prevent.
**Evidence:** .github/workflows/apply-branch-protection.yml:54-84; deploy/branch-protection.json:36-50; .github/workflows/deploy.yml:145-147; deploy/scripts/check_staged_rollout.py:61-68.
**Remediation:** Wrap the script in `main()` (or a checked-in `deploy/scripts/apply_branch_protection.py` + pytest smoke) + an actionlint/py-compile pre-commit hook; drop path filters on required workflows (in-workflow change detection) so they always report; persist real tier/environment into deploy runs (or downgrade the gate to warn-only). None of the deploy scripts have tests today.

### RISK-path-filters. CI/release path filters drifted from the real dependency graph
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** build-deploy (F3)
`ci-frontend` triggers only on `apps/frontend` + `api-types` + `design-system` — but frontend imports `@0x-copilot/chat-surface` 169× and `chat-transport` 3×, so a chat-surface change that breaks the frontend build merges without `ci-frontend` running. `release-images` omits `packages/audit-chain` though backend/ai-backend Dockerfiles `pip install` it — an audit-chain-only change publishes no new images and the next deploy manifest silently excludes it.
**Evidence:** .github/workflows/ci-frontend.yml:4-12; release-images.yml:13-26; services/backend/Dockerfile:12-16.
**Remediation:** Derive filters from each component's manifest (or add the missing paths now with a comment convention).

### RISK-version-skew. Python 3.14 images vs 3.13 CI/venvs vs pyright 3.11; ai-backend supply-chain unpinned
**Severity/confidence:** medium/high · **Verification:** confirmed (4 auditors) · **Clusters:** build-deploy, ai-runtime-api, backend-platform, backend-facade
**Merged sources:** build-deploy F5 + F6, ai-runtime-api F9, backend-platform F13, backend-facade F9.
All three service images are `python:3.14-slim-bookworm` while CI/venvs/desktop-runtime pin 3.13 and `pyrightconfig.json` targets 3.11 — production runs an interpreter minor no test suite exercises. Separately, ai-backend (the largest dependency surface — LangChain stack) has no `requirements.in`, no hash pinning, and plain `pip install -r` in CI + Dockerfile, while backend/facade use pip-compile + `--require-hashes`.
**Evidence:** services/{backend,backend-facade,ai-backend}/Dockerfile:1; .github/workflows/ci-backend.yml:37; pyrightconfig.json:11; ci-ai-backend.yml:41-46.
**Remediation:** Pin images to `python:3.13-slim` (or move CI to 3.14) + fix pyright to 3.13; bring ai-backend onto pip-compile + hashes.

---

## Docs & lower-severity inconsistencies

### RISK-docs. Living-doc inconsistencies that misroute doc-driven agents
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** docs-corpus (F1, F2, F4, F5, F6)
The doc corpus is developed-by-agents, so a stale/mislabeled doc is *executed*, not just read: (a) `docs/unused-code/backend/` actually audits **ai-backend** (its README scopes out `services/backend`) — `services/backend` (~120k LOC) has **never** had a dead-code pass; (b) `docs/use-cases/README.md` links 13/14 files that don't exist; (c) workspace-layout facts drifted across `workspace-topology.md` + two `.cursor` mirrors + `service-boundaries.md` (still list `apps/mac`/`apps/windows`, mark `apps/desktop` "Planned"); (d) `multi-tenant-deployment.md` lists RLS/KMS-vault/SIEM as "planned" while the C5-C9 security runbooks document them implemented; (e) the two ai-backend dead-code passes contradict on `share_service._collect_sources` (High-confidence-redundant vs "false positive").
**Evidence:** docs/unused-code/backend/README.md:1; docs/use-cases/README.md; docs/architecture/{workspace-topology.md,multi-tenant-deployment.md,service-boundaries.md}.
**Remediation:** Merge the two ai-backend passes + audit `services/backend`; regenerate the use-cases + topology indexes from disk (or a generation script); reconcile the multi-tenant matrix against the runbooks; resolve `_collect_sources` at HEAD once and delete the losing claim.

### RISK-frontend-hygiene. Phantom deps, dual identity conventions, incomplete rename, inverted test coverage
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** frontend-web (F3, F4, F5, F8)
`apps/frontend/package.json` declares neither `@0x-copilot/chat-surface` (imported 100+×) nor `chat-transport` (resolved only via workspace hoisting; the Dockerfile hand-copies both) and puts `typescript`/`vite` in `dependencies`; ~20 API modules still thread `org_id`/`user_id` as query params (leaking ids into access logs) while newer modules are bearer-only; the Atlas→0xCopilot rename is incomplete (user-visible `wallet.html` title, 476 `atlas` refs, `atlas-*` CSS); and coverage is inverted — ~3.8k LOC of tests on dead API modules while the three largest interactive screens (ChatScreen/SettingsScreen/LoginScreen, ~5.2k LOC incl. the full sign-in matrix) have no direct tests.
**Evidence:** apps/frontend/package.json:13-35; src/api/config.ts:6-11; wallet.html:6; (absent) ChatScreen.test.tsx.
**Remediation:** Declare both packages + move build tools to devDeps; finish the bearer-only identity migration; fix the `wallet.html` title immediately + batch-rename opportunistically; add screen-level tests for the auth matrix + run-lifecycle happy path (the DEAD-1 sweep removes the dead-test ballast).

### RISK-training-optout. Training opt-out is weaker than its code comments imply
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** ai-runtime-execution (F5)
For Anthropic the opt-out emits a speculative `anthropic-disable-training` header the comment admits is a placeholder; for Gemini it is an empty dict; and `_NO_PROVIDER_FLAG` (created so middleware could log the gap) is never wired, so the promised warning never fires. A workspace with `training_data_opt_out=true` gets no effective provider-side signal on 2 of 3 providers, silently — a real compliance-claims risk for regulated buyers.
**Evidence:** services/ai-backend/src/agent_runtime/execution/provider_kwargs.py:48-73.
**Remediation:** Verify the current Anthropic/Google contractual mechanisms, wire the absent-flag warning (or an audit annotation), surface the limitation in Settings copy.

### RISK-inconsistency-misc. Prompt self-contradiction, trace-metadata drop, DEV_AUTH_BYPASS ghost, and other confirmed inconsistencies
**Severity/confidence:** medium · **Verification:** confirmed
- **Supervisor prompt contradicts itself on checkpoints:** `DEFAULT_INSTRUCTIONS` still says "emit a checkpoint *before* calling another tool" — the exact wording documented as causing loop termination — while the harness suffix mandates the opposite (ai-runtime-execution F4). → Fix `DEFAULT_INSTRUCTIONS`.
- **`runtime_config` drops trace metadata for root runs:** the `metadata.update(redact(...))` is nested inside `if parent_trace_id is not None` (reads like an indentation slip), so root runs (the common case) drop trace metadata from graph config/observability (ai-runtime-execution F6). → Dedent + add a root-run assertion.
- **`DEV_AUTH_BYPASS` documented as removed but still modeled/read:** `deployment_profile.py` still carries `dev_auth_bypass_allowed` + `_enforce_consistency` reads the env var (now overloaded as "dev mint allowed"); `docker-compose.dev.yml` still sets `DEV_AUTH_BYPASS: "true"` and the facade honors it (backend-core F10, build-deploy F9). → Rename the toggle to `dev_mint_allowed`, delete the env check, migrate docker-dev to dev-IdP mint.
- **In-process worker silently refuses non-in-memory stores** (returns without logging) while docs advertise the flag unconditionally (flow-run-streaming F9). → Warning log + doc note.
- **`mcp_catalog` docstring describes removed seeding behavior**; `deploy-website.yml` uses floating action tags while all others SHA-pin; nginx gateway config duplicated + drifted (prod has the `proxy_read_timeout 3600s` SSE fix, dev doesn't); `apps/website` has no PR CI; legacy `MCP_TOKEN_VAULT_PROVIDER` alias in dev configs (backend-platform F14, build-deploy F10/F11/F13/F14).

### RISK-low-accepted. Low-severity risks accepted as-written (track, not urgent)
**Severity/confidence:** low · **Verification:** accepted
Desktop file-store + browser bearer/session data plaintext-at-rest by design (ai-runtime-persistence F11, flow-data F12); `/readyz` always reports ready in both facade + ai-backend (backend-facade F12, ai-runtime-api F8); dev-open identity fallback rests on env discipline (ai-runtime-api F12); `LocalTokenVault.decrypt` catches broad `Exception` masking wrong-backend errors (backend-identity F9, backend-platform F12); non-constant-time service-token compare `!=` (backend-identity F10, backend-platform F11); `_anonymous_service_headers` empty-token fallback (backend-facade F11); `CallMcpTool` swallows every exception with no logging (ai-runtime-capabilities F13); `SubagentArtifactsBackend` sync `_run_sync` deadlock latent (ai-runtime-execution F10); import-time monkey-patch + module-global checkpointer env reads in the domain (ai-runtime-execution F8); `BudgetReservationManager` docstring promises absent methods (ai-runtime-execution F9); composer Tools listing bypasses the effective-auth-state upgrade (flow-mcp F8); three interrupt-resume decision parsers with diverging vocabularies (ai-runtime-capabilities F10); runtime-policies route returns decrypted BYOK over an extra HTTP hop (backend-product F12); billing stub / tools 501 / misplaced row store (backend-product F14); stale VS-Code-fork commentary + `formatRelative` misnomer in chat-surface (chat-surface-core F10/F12); design-system + chat-transport SSE/zod/font minor items (shared-packages F5-F13); stale harness "broken wallet.html" comment (desktop-distribution F10).
