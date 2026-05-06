# PR 8.0.5 — Policy runtime integration (close the deferred glue)

> **Status:** PRD (draft).
> **Plan reference:** Closes the deferred items from [PR 8.0.3](./pr-8.0.3-atlas-completion-plan.md). Settings UI ↔ wire ↔ storage all green at the contract layer; this PR makes the runtime _actually behave differently_ when those settings change.
> **Owner:** ai-backend (most of the diff — runtime middleware + memory + retention + provider hooks) · backend (postgres adapters + auth lane hardening) · backend-facade (none, already proxies) · frontend (none, panels already mount the round-trip).
> **Size:** **M.** Net new code ≈ 600 LOC across ai-backend + backend; ≈ 200 LOC of postgres adapters; **0 net new wire shapes** (every visible behavior reuses an envelope or audit row already in flight).
> **Depends on:** ✅ PR 8.0.3 (a–g) shipped: stores + routes + facade forwarders + FE panels + AI-backend snapshots + auth-middleware bearer extension + JSONB backfill script.

---

## 0 · TL;DR

We shipped six knobs the user can flip in Settings:

- **Tool-use modes** — `read` / `write` / `destructive` × `auto` / `ask` / `require` / `block`.
- **Privacy → memory** — cross-chat memory on/off.
- **Privacy → retention** — auto-delete after N days.
- **Privacy → region** — pin runs to `us-east-1` / `eu-west-1` / `ap-northeast-1`.
- **Privacy → training opt-out** — provider do-not-train signal.
- **Privacy → admin-visible metadata** — share thread metadata with workspace admins.

Plus two infra surfaces:

- **Personal API keys** — `atlas_pk_*` bearer for CI / scripts.
- **Notifications v2** — typed `(event_kind, channel) → enabled` matrix + quiet hours.

Today every panel persists fine. **Nothing in the runtime reads any of it yet.** Five of the six runtime-visible knobs are no-ops; the API-key path lacks postgres adapters; the v2 notification dispatcher hasn't cut over from JSONB.

This PR threads each saved setting back into the place it was supposed to bite, **without inventing a single new envelope, audit action, or wire shape**. We piggy-back on:

- The existing `APPROVAL_REQUESTED` / `APPROVAL_RESOLVED` flow for ask/require gates (the same path mid-thread approvals already use — same FE card, same reducer, same Command(resume=…) plumbing).
- The existing `RUN_REJECTED` envelope (PR B7 budget enforcement) for `block` and for `region`-mismatch upstream rejection.
- The existing `MemoryPolicyAuthorizer` for the memory toggle.
- The existing C8 retention-policy table for `retention_days`.
- The existing provider-kwargs surface for `training_opt_out` + `region`.
- The existing `audit_chain` for every privileged action — no new actions; we tag the existing `tool_call_outcome` / `run_failed` rows with the policy that fired.

Everything else is postgres adapters that mirror the in-memory ones we already shipped. No new tables, no new envelopes, no new audit actions.

---

## 1 · PRD

### 1.1 Problem

PR 8.0.3 closed the storage + route + UI loop end-to-end so the user can flip every Settings knob and see it persist. **The runtime ignores every persisted value.** Concretely:

1. **Tool-use modes do not gate.** A user setting `destructive=block` watches the agent execute a destructive tool the next turn. No runtime check; no audit row distinguishing this from an accidental run. The knob is a stage prop.
2. **Memory toggle does nothing.** Memory writes proceed regardless of `memory_enabled=false`.
3. **Retention is double-sourced.** `workspace_defaults.retention_days` (PR 1.6) is the live signal; the new `privacy_settings.retention_days` (per-user override) lands on the row but the C8 sweeper never reads it, so user-level overrides have no effect.
4. **Region is decorative.** Provider-call region routing reads deployment config; the per-user / workspace `region` field is unused.
5. **Training opt-out is decorative.** Provider kwargs don't read `training_opt_out` so the do-not-train header never ships.
6. **Notifications v2 dispatcher doesn't exist.** The legacy v1 dispatcher reads JSONB; the typed `notification_preferences` + `notification_quiet_hours` tables sit empty for live users (only the backfill writes them today, and the backfill is gated behind a feature flag that nothing ever flips).
7. **Personal API keys live in process memory.** `InMemoryApiKeyStore` is the only adapter; production restart loses every key. The pen-test the plan called out hasn't run.

These aren't seven independent bugs. They're **one coordination gap** between Settings (stateful) and the runtime (stateless w.r.t. these values). Closing it correctly is the difference between _demo-ware_ and _governance-ready_.

### 1.2 Goals

1. **The runtime reads every saved knob exactly once per run start.** A single `RuntimeUserPolicySnapshot` (see §2.1) is the cache, populated by one HTTP call to backend at the moment `RunService.create_run` resolves identity. Every consumer (tool gate, memory writer, provider wrapper, retention sweeper, region router, audit emitter) reads from this snapshot — never from the wire shape directly.
2. **Tool-use gates reuse the existing approval flow.** No new envelope kinds. `mode=ask` / `mode=require` emit `APPROVAL_REQUESTED` exactly the way `requires_confirmation=true` tools do today. `mode=block` rejects the run with the existing `RUN_REJECTED` envelope (the FE already renders this with a "blocked by policy" detail line — see PR 8.0 §2.13).
3. **Memory toggle hits one line in `MemoryPolicyAuthorizer`.** Where the existing path returns `MemoryPolicyDecision.allow()`, the new branch returns `.deny("memory_disabled_by_user")` when the snapshot says so.
4. **Retention reads through one helper.** The C8 sweeper already calls a `resolve_retention_days(org_id, user_id)` that today looks up `workspace_defaults`. We extend that helper to consult `privacy_settings.retention_days` first, falling back to workspace, falling back to deployment default. **No sweeper changes; one helper does the merge.**
5. **Region and training opt-out feed `provider_kwargs`.** The existing `provider_kwargs.py` is the single place every provider call composes its request. Two new optional fields land there; absent → existing behavior; present → forwarded to the provider's standard fields (`Anthropic-Beta: training-opt-out`, etc.).
6. **Notifications v2 dispatcher takes one feature flag.** `BACKEND_NOTIFICATION_DISPATCHER_VERSION=v2` flips the read source from JSONB to typed tables. Until flipped, v1 keeps working; backfill keeps the typed tables in sync. **Both run side-by-side until cutover.**
7. **Postgres adapters land for the four new stores** (`tool_use_policies`, `notification_preferences`, `privacy_settings`, `api_keys`). Each is a thin `psycopg`-rows wrapper on the existing backend pool. Same shape as `PostgresMeStore` (PR 4.1).
8. **`atlas_pk_*` pen-test runs.** Specifically: timing-bisect on the secret-verify path; empty-prefix DOS; revoked-key replay; prefix collision with a freshly-minted key. All four are added to the existing API-keys test suite as parametric red-team cases.

### 1.3 Non-goals

- **No new event types.** If we need a new envelope kind, we're doing it wrong — every behavior here is expressible through approvals, run lifecycle, and audit rows that already exist.
- **No new audit _actions_.** We tag existing rows (`tool_call_outcome`, `run_rejected`, `memory_write_denied`) with a `policy_fired` metadata field so SIEM exports can still discriminate without a schema change.
- **No new tables.** All four migrations (0021/0022/0023/0024) shipped in 8.0.3. We only add postgres _adapters_.
- **No new FE components.** The four panels already round-trip; we just make the round-trip mean something.
- **No dispatcher rewrite.** Notifications dispatcher v2 is a single read-source flip; v1's per-channel senders are the same code paths.
- **No new connectors.** Region routing chooses _which_ provider deployment to call; it doesn't add new providers.
- **No retroactive enforcement.** A `retention_days=30` policy applied today does not retroactively delete content older than 30 days from before the policy landed (the C8 sweeper already enforces "from now forward" semantics; we honor that).

### 1.4 Success criteria

- ✅ Setting `destructive=block` and triggering a destructive tool ends with `RUN_REJECTED` (`reason="tool_use_policy_block"`); zero tool execution; one audit row tagged `policy_fired=tool_use.destructive`.
- ✅ Setting `destructive=require` triggers an inline `<ApprovalTool>` exactly like a high-risk built-in tool today; the user's ⌘↩ resumes the run.
- ✅ Setting `memory_enabled=false` and asking the agent to remember something produces `OBSERVATION` with `summary="Memory writes disabled by user"`; zero rows land in the memory store.
- ✅ Setting `retention_days=30` (per-user) on a thread lands a row in `runtime_retention_policies` with `effective_days=30`; the C8 sweeper picks it up on next pass.
- ✅ Setting `region=eu-west-1` on a tenant whose provider config has no EU deployment yields `RUN_REJECTED` (`reason="region_unavailable"`) at run start — never silently routed to a US provider.
- ✅ Setting `training_opt_out=true` adds the provider's documented opt-out header to every model call (verified by inspecting `runtime_model_call_log` rows: every call carries `provider_kwargs.training_opt_out=true`).
- ✅ Flipping `BACKEND_NOTIFICATION_DISPATCHER_VERSION=v2` makes the dispatcher read from `notification_preferences` instead of `user_preferences.preferences.notifications`. Existing v1 traffic keeps flowing until the env flips.
- ✅ Personal API keys persist across backend restart (postgres adapter under `BACKEND_DATABASE_URL`).
- ✅ Pen-test cases on `atlas_pk_*` all return 401 in constant time (no statistically-detectable variance > 5ms across 1000 trials per case).
- ✅ Cross-stack tests stay green: backend ≥ 470, ai-backend ≥ 980, facade ≥ 79, frontend 530/530.

### 1.5 User stories

| #    | Persona                                                  | Story                                                                                                                                                                                                                                              |
| ---- | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | **Sarah · Marketing Ops**                                | Sets `destructive=require` once. Asks Atlas to "delete the test launch from #launch-aurora". Sees an inline approval card before any delete fires. ⌘↩ resumes. Audit row records who approved, when.                                               |
| US-2 | **Priya · IT admin**                                     | Workspace policy: `destructive=block`. Sarah's prompt that would have caused a delete short-circuits with `RUN_REJECTED · "Workspace policy blocks destructive tools."` — no tool dispatched, no partial state. Audit row exports cleanly to SIEM. |
| US-3 | **Devi · brand reviewer (recipient of a shared thread)** | Memory disabled per her org's data-handling rule. The agent answers but doesn't write to her memory store. Subsequent threads start fresh.                                                                                                         |
| US-4 | **Compliance auditor**                                   | Sets `retention_days=30`. Posts a chat. 31 days later, the C8 sweeper deletes the messages, events, and payloads. Audit row records the deletion with the policy id that fired.                                                                    |
| US-5 | **EU customer**                                          | Sets `region=eu-west-1`. Provider config has an EU Anthropic deployment. Subsequent runs route there; `runtime_model_call_log.region` reads `eu-west-1`.                                                                                           |
| US-6 | **EU customer with US-only provider**                    | Sets `region=eu-west-1`. Provider config has no EU deployment. Run rejects at start with safe message: "EU residency unavailable in your provider config — contact your admin." Better than silently routing to the US.                            |
| US-7 | **Privacy-conscious user**                               | Sets `training_opt_out=true`. Every provider call ships the opt-out header. The user sees no UI change; the provider's billing dashboard reflects opt-out within the provider's own SLA.                                                           |
| US-8 | **DevOps engineer**                                      | Mints `atlas_pk_*` from Settings; uses it from a CI pipeline. Backend restarts; the key still works. Pen-test on the bearer fails to bisect the secret via timing.                                                                                 |
| US-9 | **Operator rolling out v2 notifications**                | Runs `backfill_notification_preferences.py` on Friday. Verifies typed tables match JSONB. Flips `BACKEND_NOTIFICATION_DISPATCHER_VERSION=v2` Monday morning. Zero user-visible drift; v1 stays available as rollback.                              |

---

## 2 · Spec

### 2.1 The single snapshot the runtime reads

Today the runtime carries `AgentRuntimeContext` (org_id, user_id, roles, permission_scopes, connector_scopes, …). We add **one optional field**: `user_policies: RuntimeUserPolicySnapshot | None`.

```python
# services/ai-backend/src/agent_runtime/execution/contracts.py
class RuntimeUserPolicySnapshot(RuntimeContract):
    """Composed snapshot of every user-flippable runtime knob.

    Loaded once per run start by ``RunService.create_run`` via a
    single HTTP call to backend's ``/internal/v1/policies/runtime``
    (new aggregate endpoint — see §2.2). Cached on
    ``AgentRuntimeContext.user_policies``. Every downstream consumer
    reads from this snapshot, NEVER re-fetches.
    """

    tool_use: ToolUsePolicySnapshot     # already exists (PR 8.0.3d)
    privacy:  PrivacySettingsSnapshot   # already exists (PR 8.0.3f)
```

Two existing snapshot types compose into one. Zero new fields. The `Optional` type means runs that fail to fetch (network blip, dev with backend off) fall back to deployment defaults — never crash, never silently stricter than today.

**Why an aggregate endpoint and not two HTTP calls.** Each run-start round-trip costs ≈ 5ms; collapsing two into one saves a measurable cold-start budget on a per-run basis (every run pays this cost). The aggregate endpoint is a 30-LOC wrapper on backend that fans out to the two existing reads in parallel and joins:

```python
# services/backend/src/backend_app/routes/runtime_policies.py (new)
@app.get("/internal/v1/policies/runtime", response_model=RuntimePolicyResponse)
def get_runtime_policies(...) -> RuntimePolicyResponse:
    tool_use, privacy = await asyncio.gather(
        _read_tool_use(identity),
        _read_privacy(identity),
    )
    return RuntimePolicyResponse(tool_use=tool_use, privacy=privacy)
```

Both halves are the same `*Snapshot.from_response(...)` parsers we already shipped. No new types, no new validation.

### 2.2 Tool-use gating — three modes, zero new envelopes

The existing `requires_confirmation` flow on tool specs already shows: when a high-risk tool is dispatched, the runtime emits `APPROVAL_REQUESTED`, pauses on the LangGraph interrupt, and resumes via `Command(resume=...)`. We **extend this trigger condition** to also fire on policy `mode=ask` / `mode=require` and to short-circuit on `mode=block`:

```python
# services/ai-backend/src/agent_runtime/capabilities/tools/runtime_gate.py (new, ≈ 90 LOC)
class ToolUsePolicyGate:
    """Run-start middleware that decides whether a tool dispatch
    proceeds, awaits an approval, or rejects the run."""

    @classmethod
    def decide(
        cls,
        *,
        snapshot: ToolUsePolicySnapshot,
        spec: LoadedToolSpec,
    ) -> ToolGateDecision:
        kind = kind_for_side_effects(spec.side_effects)
        mode = snapshot.mode_for_kind(kind)
        if mode is ToolUsePolicyMode.BLOCK:
            return ToolGateDecision.reject(
                reason="tool_use_policy_block",
                kind=kind,
            )
        if mode in {ToolUsePolicyMode.ASK, ToolUsePolicyMode.REQUIRE}:
            return ToolGateDecision.require_approval(
                kind=kind,
                one_time=mode is ToolUsePolicyMode.ASK,
            )
        return ToolGateDecision.allow()
```

The `decide(...)` call lands in **one** existing site: the `ToolDispatchMiddleware` that already runs before every tool call (it's where `requires_confirmation` is honored today). Three branches:

- `allow()` → no-op; existing fast path.
- `require_approval()` → reuses `ApprovalRequestService.create(approval_kind="tool_use_gate", ...)`; the existing FE `<ApprovalTool>` renders it; resume threads through the same `Command(resume=…)` machinery the harness uses for every other approval today. `one_time=True` (mode=ask) caches the user's approval per (run, tool_name); `one_time=False` (mode=require) re-prompts on every dispatch.
- `reject()` → emits `RUN_REJECTED` with `reason` + `policy_fired` metadata; same envelope and FE rendering already live for `RUN_REJECTED` from B7 budget enforcement.

**Audit.** The existing `WorkerAuditEmitter.emit_tool_call_outcome(...)` gains a `policy_fired: str | None = None` kwarg (purely additive — old call sites pass nothing); SIEM queries can now pivot on which policy gated which dispatch without a new audit action.

**Why the existing approval flow.** It already handles: assignment to a different user (PR 1.4 forwarding); audit chain; FE inline card; ⌘↩ keybind; Workspace pane decided list; replay. None of this needs reinventing.

### 2.3 Memory toggle — one branch in the existing authorizer

```python
# services/ai-backend/src/agent_runtime/context/memory/policy.py (one method extended)
class MemoryPolicyAuthorizer:
    @classmethod
    def authorize_write(
        cls,
        *,
        request: MemoryAccessRequest,
        snapshot: PrivacySettingsSnapshot | None,
    ) -> MemoryPolicyDecision:
        if snapshot is not None and not snapshot.memory_writes_allowed():
            return MemoryPolicyDecision.deny(reason="memory_disabled_by_user")
        # ... existing checks unchanged
```

The `MemoryWriteGuard` already handles `MemoryPolicyDecision.deny(...)` by emitting an `OBSERVATION` envelope and skipping the write. **No new code in the guard;** we only feed it a richer decision.

### 2.4 Retention — extend one resolver

The C8 retention sweeper already calls `RetentionResolver.effective_days(org_id, user_id)`. Today it reads `workspace_defaults.retention_days`. We extend it to:

```python
# services/ai-backend/src/agent_runtime/retention/policy_resolver.py (one method)
class RetentionResolver:
    @classmethod
    async def effective_days(
        cls, *, org_id: str, user_id: str | None,
    ) -> int | None:
        # Per-user override (new; PR B2) wins.
        if user_id is not None:
            user = await cls._privacy.get_for_scope(org_id=org_id, user_id=user_id)
            if user is not None and user.retention_days is not None:
                return user.retention_days
        # Workspace default (existing; PR 1.6) is the fallback.
        ws = await cls._workspace_defaults.get(org_id=org_id)
        if ws is not None and ws.retention_days is not None:
            return ws.retention_days
        return cls._deployment_default()
```

The sweeper, audit chain, and downstream cascade-delete jobs all stay unchanged.

### 2.5 Provider kwargs — region + training opt-out

```python
# services/ai-backend/src/agent_runtime/execution/provider_kwargs.py (extended)
class ProviderKwargsBuilder:
    @classmethod
    def build(cls, *, snapshot: PrivacySettingsSnapshot, base_kwargs: ...) -> ...:
        kwargs = dict(base_kwargs)
        if snapshot.training_opt_out:
            # Provider-specific header; centralised in one map so we can
            # add new providers without touching call sites.
            kwargs.setdefault("extra_headers", {}).update(
                _OPT_OUT_HEADERS_BY_PROVIDER[base_kwargs["provider"]]
            )
        if snapshot.region is not None:
            deployment = cls._region_router.resolve(
                provider=base_kwargs["provider"],
                region=snapshot.region,
            )
            if deployment is None:
                raise RegionUnavailableError(snapshot.region)
            kwargs["base_url"] = deployment.base_url
        return kwargs
```

`RegionUnavailableError` translates into the existing `RUN_REJECTED` envelope with `reason="region_unavailable"`. The runtime worker catches it before the first model call lands.

`_region_router` is a tiny static map from deployment config (`PROVIDER_REGION_DEPLOYMENTS=anthropic:us-east-1=https://api.anthropic.com,anthropic:eu-west-1=https://eu.api.anthropic.com,…`). Five lines in deployment config; zero new infra.

### 2.6 Notifications dispatcher v2 — one feature flag

The existing dispatcher reads `user_preferences.preferences.notifications`. We add a parallel reader for the typed tables and gate on env:

```python
# services/backend/src/backend_app/notifications/dispatcher.py (extended)
class NotificationDispatcher:
    def _should_notify(self, *, user_id: str, event: str, channel: str) -> bool:
        if _USE_V2:  # BACKEND_NOTIFICATION_DISPATCHER_VERSION == "v2"
            return self._read_v2(user_id=user_id, event=event, channel=channel)
        return self._read_v1(user_id=user_id, event=event, channel=channel)
```

Quiet hours: when v2 is on AND the current local time is inside the user's window, **only** `approval_requested` events break through (critical-by-default, per the migration's spec). Everything else is suppressed. Window evaluation uses `zoneinfo` (already in stdlib, so no new dep).

**Cutover sequence:**

1. Run `backfill_notification_preferences.py` (already shipped) — populates typed tables for every user.
2. Verify with the script's `--dry-run` mode + a sampling query.
3. Flip `BACKEND_NOTIFICATION_DISPATCHER_VERSION=v2` in deploy config.
4. Watch error budget. v1 read path stays in code as a rollback for one release cycle, then we delete it.

### 2.7 Postgres adapters

Each new in-memory store (`InMemoryToolUsePolicyStore`, `InMemoryNotificationPrefsStore`, `InMemoryPrivacySettingsStore`, `InMemoryApiKeyStore`) gets a sibling `Postgres*Store` that implements the same Protocol. Pattern is verbatim from `PostgresMeStore` (PR 4.1):

```python
class PostgresToolUsePolicyStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    def list_for_scope(self, *, org_id: str, user_id: str | None) -> tuple[ToolUsePolicyRow, ...]:
        scope = user_id or "__org__"
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT ... FROM tool_use_policies "
                "WHERE org_id = %s AND COALESCE(user_id, '__org__') = %s",
                (org_id, scope),
            ).fetchall()
        return tuple(_row_to_record(r) for r in rows)

    # ... upsert / delete_for_scope mirror the in-memory shapes
```

Selection: existing `BACKEND_DB_BACKEND` env (already controls `me_store`). `in_memory` keeps current dev behavior; `postgres` wires the four new adapters against the existing pool. No new connections, no new health checks.

LOC budget: ≈ 50 LOC × 4 stores = 200 LOC, all CRUD against a known schema.

### 2.8 `atlas_pk_*` pen-test cases

Add to `services/backend/tests/test_api_keys_routes.py` under a new `class TestApiKeyPenTest`:

| Case                    | Threat                                                             | Assertion                                                                       |
| ----------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| Timing-bisect on secret | Attacker bisects secret byte-by-byte from response timing          | 1000 verify trials with random bad secrets must show p99 latency variance < 5ms |
| Empty bearer DOS        | Attacker floods with `Bearer atlas_pk_`                            | Route rejects in < 1ms each, no DB hit                                          |
| Revoked-key replay      | Attacker captures pre-revocation bearer, replays                   | All trials → 401, last_used_at NOT stamped                                      |
| Prefix collision        | Attacker mints, observes prefix; tries same prefix on a second key | Mint refuses (UNIQUE on key_prefix); router never hits the wrong row            |

These are 4 new test cases (≈ 50 LOC), all parametric on the existing test fixtures.

### 2.9 What ties it all together — the streaming trace

After this PR ships, end-to-end (existing surface in _italic_, new in **bold**):

```
Browser                                    ai-backend                                backend
─────────                                  ──────────                               ───────
*composer.send*           ─POST /runs─►   RunService.create_run
                                            └─ **fetches RuntimeUserPolicySnapshot
                                                via GET /internal/v1/policies/runtime**
                                            └─ caches on AgentRuntimeContext
                                            └─ runtime_worker claims, builds graph
*SSE open*               ◄─── stream ────  *run_started*       → topbar Running
                                            *reasoning_summary*
                                            *model_delta*       → prose streams
                                            **ToolUsePolicyGate.decide(...)**
                                              ┌─── allow ─→ *tool_call_started* / *_completed*
                                              ├─── ask ────→ *approval_requested*
                                              │                └─ user ⌘↩ → *approval_resolved*
                                              │                └─ tool_call_started/completed
                                              ├─── require ─→ same as ask but cached_per_call
                                              └─── block ──→ *run_rejected* (reason=tool_use_policy_block)
                                            **MemoryPolicyAuthorizer.authorize_write(...)**
                                              ├─ allow → memory_write_completed
                                              └─ deny  → *observation* (memory_disabled_by_user)
                                            **ProviderKwargsBuilder.build(...)**
                                              ├─ region routes to deployment
                                              ├─ training_opt_out adds header
                                              └─ region_unavailable → *run_rejected* (reason=region_unavailable)
                                            *final_response*    → footer
                                            *run_completed*     → topbar Ready

(Asynchronously)
                                            **C8 sweeper reads RetentionResolver.effective_days
                                              (now consults privacy_settings before workspace_defaults)
                                              and applies cascade-delete per existing job**
```

What's not on the wire: any new envelope kind, any new audit action, any new database table. Every visible change rides one of these existing rails.

### 2.10 Streaming-friendliness contract (R1–R5 reaffirmed)

The PR 8.0 §2.10 streaming rules apply unchanged. Specifically:

- **R1 — One renderer, one envelope kind.** Tool-use gate ASK/REQUIRE creates a _normal_ `<ApprovalTool>` part; the FE doesn't need to know it came from a policy vs. a built-in confirmation. BLOCK lands as `<RunRejectedCard>`, identical to budget rejection.
- **R2 — Idempotent reducers.** `ApprovalRequestService.create(...)` is already keyed on `(run_id, span_id)` for replay. Re-replay of the same gate yields the same approval row.
- **R3 — Out-of-order tolerance.** Approvals can arrive before the `tool_call_started` they gate; the FE reducer already tolerates this.
- **R4 — Resume by `after_sequence`.** The policy snapshot lives on `AgentRuntimeContext`, not on the wire; resume of an in-flight run uses the snapshot the original worker captured. Reconnect doesn't re-fetch.
- **R5 — Presentation comes from the projector.** New `presentation.summary` strings ("Workspace policy blocks destructive tools.", "Memory writes disabled by user.") land in the existing projector, never on the FE.

### 2.11 What we deliberately don't do

- **No new "policy" tab in Settings.** The existing Tool use / Privacy / Notifications panels are the surface; we don't add a meta-policy view that would just re-render the same data.
- **No retroactive enforcement.** Setting `retention_days=30` does not delete content older than 30 days that existed before the policy. C8's "from-now" semantics stand.
- **No mid-run policy reload.** The snapshot loads at run-start. If the user flips a setting mid-run, the _next_ run sees it. Mid-run reload would invalidate every R2 idempotency key and isn't worth the complexity for a knob the user changes once a quarter.
- **No region-routing per-tool-call.** Region is a run-level decision (which provider deployment do we hit). Per-tool-call region routing is a different problem and not in scope.

### 2.12 Errors & edge cases

| Surface          | Edge case                                                     | Behaviour                                                                                                                                        |
| ---------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Snapshot fetch   | Backend unreachable at run-start                              | Snapshot = deployment defaults (= today's behavior). Run proceeds. Single warning logged (rate-limited).                                         |
| Tool-use gate    | Tool spec has empty `side_effects`                            | Treats as `read` (most permissive). Comment in code: "Specs that don't declare side-effects are treated as read; we audit when this fires."      |
| Tool-use gate    | User declines an `ask` approval                               | `approval_resolved(status=rejected)` → run finishes with `RUN_FAILED(reason=tool_dispatch_rejected)`. Same as today.                             |
| Memory toggle    | `memory_enabled=false` on a thread that has prior memory rows | Existing rows stay accessible (read continues). Only writes block. Deletion is the user's explicit action via "delete all my data."              |
| Retention        | Per-user `retention_days=30`; workspace `retention_days=180`  | User wins (30). Logged in the audit row's `policy_fired` field so admins can audit.                                                              |
| Region           | `region=eu-west-1`, no EU provider deployment configured      | `RUN_REJECTED(reason=region_unavailable)`. Safe message: "Your data residency setting isn't supported by this deployment. Contact your admin."   |
| Provider opt-out | Provider doesn't support the opt-out header                   | The map omits the provider; `training_opt_out=true` is silently a no-op for that provider. Audit flags it on the call log so SOC can spot drift. |
| Notifications v2 | User has neither v1 JSONB nor v2 typed rows                   | Fall through to deployment defaults (already implemented in §B4 hydration).                                                                      |
| API keys         | Postgres pool exhausted                                       | Verify route returns 503 (existing behavior). Cached identity in the facade's touch LRU survives one cycle, so live sessions degrade gracefully. |
| API keys         | Pepper rotated                                                | Every key invalidates; clients see 401; users re-mint from Settings. Documented in the runbook.                                                  |

### 2.13 Verification

- **Unit tests** — every helper above (snapshot fetcher, gate, retention resolver, provider kwargs, dispatcher v2 reader, postgres adapters). ≈ 80 new tests.
- **Integration tests** — replay an existing run fixture with each policy mode set; assert the expected envelopes land. Reuses `tests/integration/test_*_replay_parity.py` infra.
- **Pen-test cases** — §2.8.
- **Cross-stack** — `make test`; backend ≥ 470, ai-backend ≥ 980, facade ≥ 79, frontend 530/530, typecheck clean.
- **Manual** — walk Sarah / Priya / Devi / EU customer flows in §1.5 in the dev stack with each store backed by postgres. Capture one screenshot per flow.

---

## 3 · Architecture summary

The PR's shape is intentionally narrow: **add one snapshot to `AgentRuntimeContext`; teach exactly five existing call sites to consult it; add four postgres adapters that mirror their in-memory cousins.** No new tables, no new envelopes, no new audit actions, no new FE components.

Every behavioral change rides an existing rail — approvals for tool-use gates, run rejections for blocks and region failures, memory denials for the memory toggle, the C8 sweeper for retention, provider kwargs for opt-out and region routing, the existing notifications dispatcher (read-source flipped) for v2.

By the time this PR ships:

- **Sarah** (US-1) sees a real approval card the next time a destructive tool fires.
- **Priya** (US-2) sees a real `RUN_REJECTED` for blocked policies.
- **Devi** (US-3) doesn't leak memory writes across her threads.
- **Compliance** (US-4) gets per-user retention enforced by the same sweeper that already enforces workspace retention.
- **EU customers** (US-5/6) either route to an EU deployment or get a clean rejection — never silent US fallback.
- **Privacy-conscious users** (US-7) ship the opt-out header on every call.
- **DevOps** (US-8) has API keys that survive restart and a pen-tested verify path.
- **Operators** (US-9) cut over to typed notifications with a single env flip and a documented rollback.

The smallest change that turns the policy panels from stage props into governance — and the only one that prevents the next reviewer from asking "what does this knob actually do?".

---

## 4 · Sequencing recommendation

Order by leverage × risk:

1. **§2.1 — Snapshot loader on `RunService.create_run`** (everything depends on this; pure additive).
2. **§2.7 — Postgres adapters** (unblocks production for everything 8.0.3 already shipped).
3. **§2.3 — Memory toggle** (one method; lowest blast radius).
4. **§2.4 — Retention resolver** (one method; existing sweeper).
5. **§2.5 — Provider kwargs (training opt-out)** (one map; existing call sites).
6. **§2.5 — Provider kwargs (region routing)** (one router; same call site).
7. **§2.2 — Tool-use gate** (largest user-visible change; reuses approvals so still small).
8. **§2.6 — Notifications v2 cutover** (gated by env flag; rollback is one flip).
9. **§2.8 — `atlas_pk_*` pen-test** (concurrent with 2 above).

Each step ships independently behind an env flag where applicable; no big-bang merge.
