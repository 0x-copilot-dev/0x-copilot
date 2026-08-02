# P2 — MCP → `langchain-mcp-adapters` per-tool + direct-connect: decomposed implementation plan

**Status:** planning · **Base:** `84a67dc7` (P1b at HEAD) · **Author target:** ai-backend
**Why decomposed:** the P1b monolith crashed at 91 min / 273 tool calls. P2 is **9 narrow
increments**, each independently testable, committable green, and `≤~800 LOC`. The additive,
flag-guarded components land first (like P1a); the registration **flip** and the **deletions**
come last, each its own increment, so a regression never forces reverting the deletes.

> Ground truth verified at HEAD (not assumed):
> P0 seams `ToolSource` / `CredentialProvider` / `ToolMiddleware` / `MiddlewareStage` /
> `MIDDLEWARE_ORDER = (POLICY, EXEC_POLICY, OBSERVE, ERROR_MAP, CITATIONS)` exist in
> `capabilities/policy/contracts.py`. `McpDispatchPolicy.evaluate` / `McpCapabilityDescriptorSource.describe`
> exist in `capabilities/mcp/descriptor_source.py` (P1b). `ToolAccessGate.park_for_approval`
> at `surfaces_v2/gate.py:281`. `StreamMessageProcessor` keys on `McpDispatcherUnwrap.effective_server_name`
> at `runtime_worker/stream_tools.py:445,953,961` and returns **`None`** for any non-`call_mcp_tool`
> payload. Deployment selector `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop` in
> `runtime_adapters/factory.py:111-121`. `mcp` / `langchain-mcp-adapters` **absent** from
> `services/ai-backend/requirements.txt`.

---

## 0. The two migrations are separable (the finding that shapes the whole plan)

**Per-tool** (replace the single `call_mcp_tool` gateway with one `BaseTool` per real MCP tool,
move the PDP from gateway-internal into per-tool middleware, swap `McpDispatcherUnwrap` for a
name→connector resolver) is **independent** of **direct-connect** (ai-backend opens the MCP
server itself instead of proxying JSON-RPC through `services/backend`).

- Per-tool is **transport-independent**: the PDP re-plumb, the descriptor source, and the resolver
  do not move any credential. They ship over the existing proxy if desired.
- Direct-connect is the **only** part that forces the credential-boundary decision, because today
  neither the server **URL** (`service.py:1634`, absent from `McpServerCard`) nor the decrypted
  **token** (`store.py` ciphertext) ever crosses into ai-backend.

P2 therefore ships **per-tool for both deployments**, **direct-connect for desktop immediately**
(via the existing loopback broker — no new trust boundary), and **direct-connect for web GATED**
on one `services/backend` endpoint that is **not assumed** (see §1).

---

## 1. Credential-boundary decision (explicit; web is a gated sub-decision)

For direct-connect, `McpToolSource` needs `(url, transport, auth)` per server. `auth` is a rotating
`httpx.Auth` supplied by a P0 `CredentialProvider`; `(url, transport)` come from the same round-trip
(a `McpConnectionDirectory`) — kept on the **credential plane**, never added to the model-visible
`McpServerCard`. This recasts backend's "endpoints are process-local" as "endpoints are
credential-plane-local" without weakening it.

### DECIDED — Desktop: broker-mediated keychain read (least-invasive, isolation-preserving)

The MCP OAuth token lives in Electron `safeStorage` behind `SecretStorage`'s **active-workspace
gate**, in the **main process**. The Python ai-backend child cannot read `safeStorage`. The
**existing authenticated loopback capability broker** (`apps/desktop/main/capabilities/broker.ts`,
127.0.0.1, per-boot 256-bit bearer via `LocalServiceChannelCredential`) is the seam the agent
already uses for host-fs. P2 adds **one broker route** — `POST /mcp/secret` →
`SecretStorage.get(activeWorkspace, "mcp", server_id)` → `{url, transport, token, expires_at}`
(MCP tokens are dynamic, added at connect time, so they cannot ride `bootSecrets` — they must be
read live).

**Why this and not the alternatives** (dossier A options): reading the vault directly (B) is
impossible — `safeStorage` is main-process-only and no Python-reachable keychain store exists;
moving the vault into ai-backend (C) violates the service split. The broker (a scoped form of A)
is strictly less code and less new attack surface than a fresh HTTP boundary, and **isolation is
preserved end-to-end**: the broker reads under the main process's active-workspace gate, so a
compromised run cannot pull another workspace's token; ai-backend holds only a short-lived bearer
inside `RefreshingBearerAuth` (`SecretStr`, `repr`-suppressed, never persisted, never logged).

### GATED — Web / self-host: one `services/backend` mint endpoint (do **not** assume)

`services/backend` owns `TokenVault` + `connectors/store.py` + `mcp_transport.py`. ai-backend
already reaches it at `/internal/v1/mcp/*` with service-token headers. Direct-connect for web needs
**one new endpoint**:

> `POST /internal/v1/mcp/servers/{server_id}/access-token` → `{url, transport, access_token, expires_at, scopes}`
> — runs the **same** access-mode gate + `_require_valid_token` refresh; issues a per-`(tenant,user,server)`
> **short-TTL access token** (never the stored refresh token). Tenant isolation and encryption-at-rest
> stay **inside** the backend; ai-backend gets only a narrow expiring bearer.

**This backend change is a gated sub-decision, flagged not assumed.** Until it is approved and
shipped, **web keeps the legacy proxy gateway** (per-tool flag stays OFF for the web profile). Web
per-tool _can_ later ride the proxy if direct-connect is declined outright, but that proxy-backed
`McpConnectionDirectory` variant is **out of P2's guaranteed scope** — P2 guarantees per-tool +
direct-connect on **desktop**, and delivers the web path only behind the gate.

Provider selection is by `ENTERPRISE_DEPLOYMENT_PROFILE` at factory wiring, mirroring the existing
store/adapter selection in `runtime_adapters/factory.py`.

---

## 2. PRESERVE / DELETE file list

Two new deps in `services/ai-backend/requirements.txt`: `mcp` + `langchain-mcp-adapters==0.3.1`
(`langchain-core`, `httpx` already present).

### PRESERVE (reuse verbatim — the P2 substrate)

| File                                                                                           | Role in P2                                                                                                                              |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `capabilities/policy/contracts.py`                                                             | P0 seams — implemented against, unchanged                                                                                               |
| `capabilities/policy/service.py` (`PdpPolicyService`)                                          | the PDP the Policy stage consults — unchanged                                                                                           |
| `capabilities/mcp/descriptor_source.py` (`McpCapabilityDescriptorSource`, `McpDispatchPolicy`) | **keystone** — the per-tool descriptor + decision, **not re-derived**                                                                   |
| `capabilities/mcp/policy_allowlist.py`                                                         | `McpConnectorPrincipal` + `CardConnectorAllowlist` PDP inputs                                                                           |
| `surfaces_v2/gate.py` (`ToolAccessGate.park_for_approval`)                                     | the GATE interrupt — reused by the Policy stage                                                                                         |
| `capabilities/mcp/permissions.py`                                                              | `McpPermissionPolicy` card visibility/authorization — still gates exposure                                                              |
| `capabilities/mcp/annotations.py`                                                              | untrusted `readOnlyHint`/`destructiveHint` capture feeding `_action`                                                                    |
| `capabilities/mcp/cards.py`                                                                    | `McpServerCard`, `McpToolDescriptor`, `McpTransport`, error codes (types stay; `McpToolCallRequest` vestigial **as a model schema**)    |
| `capabilities/mcp/registry.py`                                                                 | `DynamicMcpRegistry` + `resolve_server` seam (only the provider impl swaps)                                                             |
| `capabilities/mcp/{revision_feed,revision_resolver,freshness}.py`                              | descriptor-revision control plane — transport-independent (re-fed from ai-backend under direct-connect)                                 |
| `capabilities/mcp/gateway_context.py`                                                          | Operation-Gateway composition + run binding — transport-independent                                                                     |
| `capabilities/mcp/operation_adapter.py`                                                        | **reshape** — only `_dispatch` (`:465-471`) re-points to the direct client                                                              |
| `capabilities/mcp/loader.py`                                                                   | **reshape** — discovery/pagination stay; provider swaps proxy→direct                                                                    |
| `capabilities/mcp/client.py`                                                                   | **reshape** — keep the error taxonomy (`McpAuthError`/`McpConnectionError`/…); drop proxy-shaped `McpClient` Protocol + `McpLeaseError` |
| `capabilities/mcp/middleware/cite_mcp.py`                                                      | Citations stage substrate — unchanged                                                                                                   |
| `capabilities/mcp/middleware/auth_mcp.py`                                                      | `auth_mcp` OAuth-request tool — direct-connect still needs the token to exist                                                           |
| `capabilities/mcp/middleware/dynamic_loader.py`                                                | `LoadMcpServerTool` — kept for the deferred-load posture (may go vestigial)                                                             |
| `runtime_worker/stream_tools.py`                                                               | **edit** — the envelope + `ToolInvocationRecord` projector; re-hook the 3 connector-identity seams                                      |
| backend `mcp_transport.py`, `mcp_session_pool.py`                                              | **PRESERVE unless the proxy is removed for _both_ deployments** (holds the credential boundary; conditional delete — see below)         |

### DELETE (last, in P2-9 — only after the flip is validated)

| File / symbol                                                                                                           | Condition                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `capabilities/mcp/middleware/call_tool.py` — `CallMcpTool` as a model tool                                              | its `_authorize_mcp_dispatch` + interrupt-GATE + `_policy_denied_result` + `_refusal` **relocate** into the Policy stage (P2-4), then the umbrella tool is removed |
| `capabilities/mcp/dispatcher.py` — `McpDispatcherUnwrap`                                                                | superseded by the name→connector resolver (P2-1); the 3 stream seams stop importing it                                                                             |
| `capabilities/mcp/backend_provider.py` — `BackendMcpProvider`/`BackendMcpClient` (JSON-RPC proxy, lease, service-token) | **only when direct-connect is active for that deployment.** Desktop: delete. Web: **keep until the gated mint endpoint ships**                                     |
| backend `mcp_transport.py`, `mcp_session_pool.py`                                                                       | **only when the proxy is removed for _both_ desktop and web.** Until web direct-connect ships, **PRESERVE**                                                        |

---

## 3. The nine increments

Each row: files · test · ship-criteria · parallelism. `LOC` is the whole increment incl. tests.

### Wave 0 — foundation (sequential, first)

#### P2-1 · deps + connection contracts + name→connector resolver — `~250 LOC` · **additive, unwired**

- **Files:** `requirements.txt` (+`mcp`, +`langchain-mcp-adapters==0.3.1`); new `capabilities/mcp/connection.py`
  (`McpServerConnectionConfig` frozen `extra="forbid"`, `McpConnectionDirectory` Protocol, `MintedToken`
  frozen with `value: SecretStr` `repr`-suppressed); new `capabilities/mcp/connector_resolver.py`
  (`ToolConnectorResolver` — a built-at-registration `tool_name → server_slug` map; the exact replacement
  for `McpDispatcherUnwrap`).
- **Test:** `tests/.../mcp/test_connection_contracts.py` — frozen/`extra="forbid"`; `MintedToken.value` never
  in `repr`/logs; resolver maps known names, returns `None` for unknown (native step, unchanged semantics).
- **Ship:** `pip install -r requirements.txt` clean; `import mcp, langchain_mcp_adapters` succeed in CI;
  contracts + resolver unit-green. **Nothing consumes them yet.**
- **Parallel:** first; blocks all others.

### Wave 1 — additive components (parallel after P2-1)

#### P2-2 · `RefreshingBearerAuth` (the refresh-in-a-small-Auth) — `~200 LOC` · **additive, unwired**

- **Files:** new `capabilities/mcp/credentials/__init__.py`, `credentials/refreshing_auth.py` — an
  `httpx.Auth` whose `async_auth_flow` fetches on first use, caches to `expiry − skew`, and on a single
  `401` yields **exactly one** refreshed request (never loops). Refresh lives here, not in a subsystem —
  one long-lived `MultiServerMCPClient` never rebuilds.
- **Test:** `test_refreshing_auth.py` — cached until skew; refresh after expiry; single retry on 401;
  no loop on repeated 401; `SecretStr` never rendered.
- **Parallel:** after P2-1 (uses `MintedToken`); independent of P2-3/6.

#### P2-3 · `McpToolSource.load()` + annotations bridge — `~750 LOC` · **additive, unwired** · _keystone_

- **Files:** new `capabilities/mcp/tool_source.py` — build the `langchain-mcp-adapters` `Connection` dict
  per server from `McpConnectionDirectory` + `CredentialProvider`, open one session per server,
  `load_mcp_tools(session) → list[BaseTool]`; `_ingest_annotations` writes `(tool.metadata or {})` tri-state
  hints into `McpToolAnnotationsRegistry`; then `McpCapabilityDescriptorSource.describe(card, server, tool, ctx)`
  → `list[tuple[BaseTool, CapabilityDescriptor]]`. Publishes `cards_by_urn` and the `tool_name→server_slug`
  map (feeds P2-1 resolver + P2-4 gate). Small `AuthorizedCardLister` (authorized via `McpPermissionPolicy`).
- **Test:** `test_tool_source.py` — with a **fake** `MultiServerMCPClient` + fake directory + fake provider:
  `load()` returns pairs; annotations tri-state → `descriptor.action` (`readOnlyHint is True`→READ, `None`→WRITE,
  `False`→WRITE — the fail-closed rule, unchanged); unauthorized cards excluded; `cards_by_urn` + name→connector
  map published; **no second `describe()` path** (assert the P1b source is the only derivation).
- **Parallel:** after P2-1; independent of P2-2 (creds injected as a Protocol, faked in test).

#### P2-6 · `stream_tools.py` connector-identity re-hook — `~200 LOC` · **additive fallback, safe pre-flip**

- **Files:** `runtime_worker/stream_tools.py` — at the 3 seams (`:445`, `:953`, `:961`) try
  `McpDispatcherUnwrap.effective_server_name(payload)` first (still resolves legacy `call_mcp_tool`); on
  `None`, fall back to the `ToolConnectorResolver` (`tool_name → server_slug`). **Inert under the legacy
  gateway** (payload is `call_mcp_tool` → unwrap resolves), **live under per-tool** (unwrap returns `None`
  → resolver resolves).
- **Test:** `tests/unit/runtime_worker/test_stream_connector_identity.py` — legacy `call_mcp_tool` payload
  still yields `connector_slug` (regression); synthetic per-tool payload (`tool_name="linear.create_issue"`)
  yields `connector_slug` via the resolver, and `provenance` + `access_mode` attach; unknown tool → `None`
  (native step, unchanged).
- **Parallel:** after P2-1 (needs resolver); independent of P2-2/3. **Landing this before the flip is a
  deliberate de-risk** — per-tool connector identity is proven independently of P2-8.

### Wave 2 — middleware + credentials (parallel)

#### P2-4 · Policy middleware stage — `~450 LOC` · **additive, unwired** · _the load-bearing seam_

- **Files:** new `capabilities/mcp/middleware/policy_tool.py` — `PolicyToolMiddleware(stage=POLICY)`;
  `wrap(tool, descriptor)` binds a fixed `(card, server, tool)` and, **before** delegating, runs the P1b
  decision: `McpDispatchPolicy.evaluate(...)` → `ALLOW`→`inner.ainvoke`, `DENY`→typed refusal
  (`connector_unavailable` vs `permission_denied`), `GATE`→`gate.park_for_approval(...)` (park+resume in
  the **same** run; `gate is None`→**fail-closed refusal**, never a silent dispatch; decline→refusal;
  approve→execute once). This is `CallMcpTool._authorize_mcp_dispatch` refactored from "one gateway decoding
  server/tool from the payload" to "one wrapper bound to a fixed tool" — every P1a matrix invariant preserved
  for free. New seam: the source publishes `cards_by_urn` so the GATE can build its interrupt payload.
- **Test:** `tests/.../middleware/test_policy_tool.py` — **hermetic, thorough** (security seam): ALLOW falls
  through; DENY returns refusal **without** calling inner; GATE parks, approve→execute once, decline→refusal;
  `gate=None`→fail-closed; wrap is schema-identical (name/description/args_schema propagated).
- **Parallel:** after P2-3 (`cards_by_urn` + descriptor); parallel with P2-5.

#### P2-5 · Exec-policy + Error-map + Citations + Observe-stamp + `compose()` — `~650 LOC` · **additive, unwired**

- **Files:** new `middleware/exec_policy_tool.py` (`RetryingTool` keyed on `descriptor.action`: READ retries
  `McpConnectionError`/`McpTimeoutError`; WRITE/DESTRUCTIVE `retry_exceptions=()`; never retry
  `McpAmbiguousDispatchError`; write→disable stream resumption — the never-replay edge as one `action`-keyed
  rule); `middleware/error_map_tool.py` (normalize the `McpClientError` taxonomy → `{safe_message, code,
retryable}` aligned to `RuntimeErrorCode`/`McpLoadErrorCode`); `middleware/citations_tool.py` (forward to
  `CitationProjectingMcpMiddleware` keyed on `server_slug`); `middleware/observe_tool.py` (thin OBSERVE
  binding-stamp — attaches the connector binding to the call so downstream projection has it; the durable
  envelope + `ToolInvocationRecord` stay in `stream_tools.py` per P2-6); `middleware/compose.py`
  (`compose(pair, stack)` asserts `tuple(m.stage for m in stack) == MIDDLEWARE_ORDER`, wraps innermost-first).
- **Test:** `test_exec_error_citations.py` — each wraps schema-identically; `compose` raises on wrong order;
  exec-policy no-retry-on-write + never-retry-ambiguous; error-map mapping table (`McpAuthError`→AUTH_FAILURE
  non-retryable, `McpRequestRejectedError`(4xx)→refused non-retryable, `McpConnection/Timeout`→retryable,
  `McpAmbiguous`→non-retryable never-replay); citations forwards `(connector, tool_call_id, result)`.
- **Parallel:** after P2-3; parallel with P2-4.

#### P2-7 · CredentialProvider impls (desktop ships; web gated) — `~500 LOC` · **additive**

- **P2-7a — desktop (ships):** `apps/desktop/main/capabilities/broker.ts` new route `POST /mcp/secret`
  (+TS test) → `SecretStorage.get(activeWorkspace,"mcp",server_id)`; new Python
  `capabilities/mcp/credentials/desktop.py` (`DesktopKeychainCredentialProvider` — `CredentialProvider` +
  `McpConnectionDirectory` via a `LocalCapabilityBrokerClient`; `auth_for` returns
  `RefreshingBearerAuth(fetch=broker read)`).
- **P2-7b — web (GATED, build only on approval):** `services/backend` mint route
  `POST /internal/v1/mcp/servers/{id}/access-token` (+test); Python `credentials/backend.py`
  (`BackendScopedTokenCredentialProvider`; `auth_for` returns `RefreshingBearerAuth(fetch=mint POST)`).
- **Test:** `test_desktop_provider.py` — reads from a fake broker → `RefreshingBearerAuth` + `connection_for`;
  active-workspace gate preserved (a foreign-workspace read is refused by the broker). `test_backend_provider.py`
  (gated) — mints from a fake backend; requires service-token headers.
- **Parallel:** after P2-2 (+P2-1). 7a and 7b parallel with each other; 7b is behind the §1 gate — if the
  backend change is not approved, ship 7a only and web stays legacy.

### Wave 3 — the flip, then the deletions (sequential, last)

#### P2-8 · flip registration to per-tool behind a flag — `~450 LOC` · **RISKY** · _the single riskiest increment_

- **Files:** `execution/factory.py` `_model_visible_tools` — behind `MCP_PER_TOOL_ENABLED` (default **OFF**,
  profile-gated): when ON, wire the profile-selected `CredentialProvider`/`McpConnectionDirectory`, build
  `McpToolSource`, `pairs = await source.load()`, `wrapped = compose(pair, stack)` per pair, register each as
  `ModelToolDeclaration.declared(wrapped, owner=ModelToolOwner.MCP)`; build `interrupt_on` **descriptor-driven**
  (GATE-eligible per action×trust) instead of the single `call_mcp_tool` key; publish `ToolConnectorResolver`
  - `cards_by_urn`. When OFF: the legacy `CallMcpTool` gateway is untouched.
- **Test (de-risk keystone):** `tests/unit/runtime_worker/test_mcp_per_tool_gate_e2e.py` — **hermetic
  real-graph run→stream**, mirroring `test_mcp_write_gate_e2e.py`: deterministic fake model drives a real
  graph with the flag **ON**; a per-tool **READ** auto-runs; a per-tool **WRITE** parks the GATE, resume→executes
  once; `connector_slug` + `provenance` + `access_mode` present in the stream (proves P2-6 under a real per-tool
  call); flag **OFF** → legacy path byte-identical (regression).
- **Parallel:** **sequential** — after P2-3, P2-4, P2-5, P2-6, P2-7a. Flag default OFF keeps `dev`/`main` green.

#### P2-9 · deletions + desktop default-ON — `~net-negative LOC` · **cleanup, its own increment**

- **Files:** flip `MCP_PER_TOOL_ENABLED` default **ON for the desktop profile** (after live-stack validation);
  DELETE `middleware/call_tool.py` (`CallMcpTool` as a model tool), `mcp/dispatcher.py` (`McpDispatcherUnwrap`);
  DELETE `mcp/backend_provider.py` **for desktop** (direct-connect active); **keep** for web until the §1 gate
  ships; reshape `loader.py` / `operation_adapter.py::_dispatch` to the direct client. Backend
  `mcp_transport.py`/`mcp_session_pool.py` deletion **deferred** until the proxy is gone for **both**
  deployments.
- **Test:** full ai-backend suite green with desktop per-tool default-ON; grep-assert no import of deleted
  symbols; "no legacy `call_mcp_tool` registered under per-tool" assertion.
- **Parallel:** **sequential, last.** Separated from P2-8 so a flip regression never forces reverting deletes.

---

## 4. Dependency waves (parallelism at a glance)

```
P2-1  deps + connection contracts + resolver           [FIRST, blocks all]
        │
        ├─▶ P2-2  RefreshingBearerAuth            ┐
        ├─▶ P2-3  McpToolSource.load (keystone)   │  Wave 1 — parallel
        └─▶ P2-6  stream_tools re-hook (pre-flip) ┘
                    │
        ┌───────────┴───────────────┐
        ▼                           ▼
   P2-4 Policy stage           P2-5 Exec/Error/Cite/Observe/compose   ┐ Wave 2
        (after P2-3)                (after P2-3)                       │ parallel
   P2-7 credentials (after P2-2): 7a desktop ships · 7b web GATED     ┘
        │
        ▼
   P2-8  FLIP behind flag (default OFF) — hermetic real-graph e2e   [RISKY, sequential]
        │
        ▼
   P2-9  deletions + desktop default-ON                             [LAST, sequential]
```

Critical path: **P2-1 → P2-3 → P2-4 → P2-8 → P2-9** (five sequential; the rest fill the waves).

---

## 5. Single riskiest increment + how it is de-risked

**Riskiest: P2-8 (the registration flip).** It is where the single `call_mcp_tool` gateway becomes
N per-tool tools and where the PDP + GATE + connector identity are exercised end-to-end for the first
time. A silent failure here is a **security** failure (a write dispatching without a gate) or a
**provenance** failure (an app losing its `connector_slug`).

De-risked five ways, all already in the plan:

1. **Flag default OFF, profile-gated** — `dev`/`main` stay on the proven gateway until validated.
2. **Connector identity proven _before_ the flip** — P2-6 lands the resolver fallback as an inert
   additive change, tested against a synthetic per-tool payload, so P2-8 is not the first time
   `connector_slug` resolves for a per-tool call.
3. **Policy seam isolated + hermetically tested before the flip** — P2-4 tests ALLOW/DENY/GATE/`gate=None`
   without touching the graph.
4. **The flip's own hermetic real-graph test** — `test_mcp_per_tool_gate_e2e.py` mirrors P1b's
   `test_mcp_write_gate_e2e.py`: deterministic fake model, real graph, asserts READ auto-run, WRITE
   GATE-park-resume, stream provenance, **and** flag-OFF regression parity.
5. **Deletions are a separate increment (P2-9)** — reverting a bad flip never means resurrecting deleted
   files; the blast radius of P2-8 is one flag.

---

## 6. Open design decisions to confirm before coding (recommendations)

1. **Endpoint URL surface** — recommend provider-supplied `(url, transport)` co-located with the token
   (the `McpConnectionDirectory`), **not** a new model-visible `McpServerCard.url`.
2. **`cards_by_urn` threading for the GATE payload** — recommend the source publishes the urn→card map to
   `PolicyToolMiddleware`; alternative is a distilled per-tool gate binding (`display_name`, `required_scopes`,
   `server_id`) that keeps the middleware card-agnostic.
3. **Descriptor re-derivation in the Policy stage** — `evaluate` rebuilds what the source built; it is
   pure/total/cheap. Recommend ship the reuse as-is; add `McpDispatchPolicy.decide_prebuilt(descriptor, …)`
   only if profiling asks.
4. **stdio session lifetime** — minimal pool vs one process per call; decides whether `McpConnectionDirectory`
   yields a command spec or a URL for local/stdio servers. Defer to P2-3 spike; does not block the HTTP/SSE lane.
