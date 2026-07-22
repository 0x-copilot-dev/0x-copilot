# PRD-06 — Connector access mode: the permission control needs a backend

## Problem

On the Tools surface every connected tool shows a three-way switch — **Read · Read & act · Off**. It lies twice.

1. **It always reads "Off."** Gmail, Safe, GitHub — everything paints Off on both web and desktop, no matter what the connector is actually allowed to do. A user looking at this screen concludes the agent has no tool access at all, then watches the agent use those tools in the next run.
2. **Clicking it does nothing durable.** On web, the click optimistically slides the pill over, fires a PATCH the server has never heard of, gets a 404, and slides back with an error banner. On desktop the buttons are inert — the handler was never wired.

Worse than a cosmetic bug: this is a **permission control that grants and revokes nothing**. Nothing downstream of it reads a mode, because no mode is ever stored. A security-shaped affordance that has no effect on behaviour is more dangerous than no affordance, because users act on it.

The design intends this to be the per-connector "which app may do what" switch — distinct from OAuth scopes (what the provider granted) and from the global approval policy in Settings → Model & behavior (how noisy the agent is about acting). That concept exists in the type system and in the UI. It does not exist in a database, in an endpoint, or in the runtime.

## Evidence

Every row opened and verified against the working tree at `claude/design-parity-audit-7ec82a`.

| Claim                                                        | File:line                                                                         | What the code actually does                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web calls a route that does not exist                        | `apps/frontend/src/api/connectorsApi.ts:210-220`                                  | `setConnectorAccessMode` → `httpPatchQuery("/v1/connectors/{id}/access-mode")`. Confirmed.                                                                                                                                                                                                   |
| Facade has no such route                                     | `services/backend-facade/src/backend_facade/connector_routes.py:41-54`, `:68-328` | Path table lists 12 paths (LIST, ITEM, START_OAUTH, OAUTH_CALLBACK, REFRESH, DISCONNECT, SCOPES, AUDIT, STREAM, 3 desktop). Exactly 12 decorators; the only `@app.patch` is `SCOPES` at `:309`. No catch-all forwarder. **Confirmed** — brief said "40-54", actual 41-54.                    |
| Backend has no such route                                    | `services/backend/src/backend_app/connectors/routes.py:409-450`                   | The only PATCH is `/v1/connectors/{connector_id}/scopes` (202 ACCEPTED). No access-mode route anywhere in the module.                                                                                                                                                                        |
| Backend wire model cannot carry the field                    | `services/backend/src/backend_app/connectors/routes.py:60-77`                     | `ConnectorResponseModel` is `extra="forbid"` and its field list has no `access_mode`. Brief said `:55-77`; class actually starts at `:60`. Materially identical.                                                                                                                             |
| Projection never emits it                                    | `services/backend/src/backend_app/connectors/routes.py:528-549`                   | `_to_wire` enumerates 12 fields; `access_mode` is not one. Brief said `:523-546`; actual `528-549`.                                                                                                                                                                                          |
| Record has no field                                          | `services/backend/src/backend_app/connectors/store.py:86-112`                     | `ConnectorRecord` (`extra="forbid"`) — id, tenant_id, slug, display_name, description, status, status_reason, owner_user_id, scopes, last_sync_at, last_error_at, created_at, updated_at, vault_ref. Confirmed.                                                                              |
| Table has no column                                          | `services/backend/src/backend_app/connectors/schema.sql:16-39`                    | 14 columns, no `access_mode`. Confirmed.                                                                                                                                                                                                                                                     |
| **Migration chain also has no column** (not in brief)        | `services/backend/migrations/0044_connectors.sql:15-38`                           | The durable DDL is `0044_connectors.sql`, not the module `schema.sql` — the module file is a mirror. `0045` is the highest migration; **`0046` is the next free id**. `migrations/MANIFEST.lock` is checksum-guarded by `tools/check_migration_manifest.py`.                                 |
| Service has no setter                                        | `services/backend/src/backend_app/connectors/service.py` (whole file)             | Writers are `disconnect` (`:324`), `refresh_token` (`:372`), `patch_scopes` (`:403`), `mark_error` (`:475`), `upsert_from_mcp_registration` (`:292`). No access-mode setter. Confirmed.                                                                                                      |
| api-types documents the gap                                  | `packages/api-types/src/connectors.ts:128-136`, `:304-321`                        | `access_mode?: ConnectorAccessMode` marked OPTIONAL because "the facade does not serve it until the access-mode PATCH lands (PRD §11)". `SetConnectorAccessModeRequest/Response` already defined. Confirmed.                                                                                 |
| Every row paints "Off"                                       | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx:338` | `accessMode={c.access_mode ?? "off"}`. Since the wire never carries the field, this is `"off"` for 100% of rows on both hosts.                                                                                                                                                               |
| UI is done and accessible                                    | `packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx:76-137`  | `role="radiogroup"`, roving `tabIndex`, `aria-checked`, Arrow/Home/End in `handleKeyDown`, options enumerated from `CONNECTOR_ACCESS_MODES`. Confirmed.                                                                                                                                      |
| Desktop has no binding at all                                | `apps/desktop/renderer/destinationBinders.tsx:481-489`                            | `<ConnectorsDestination items filter onFilterChange onConnect onOpenCatalogEntry onOpenApprovalSettings onRetry />` — `onSetAccessMode` is absent, so `ConnectorsDestination.tsx:339-343` passes `undefined` and the segment is inert.                                                       |
| Web owns optimistic apply + revert privately                 | `apps/frontend/src/features/connectors/ConnectorsRoute.tsx:304-345`, `:422-445`   | 40 lines of optimistic set / PATCH / revert-on-failure / error banner living in the web host only. Also re-used by the terminal-Connect flow at `:428-445`.                                                                                                                                  |
| **The parity harness masks the bug** (not in brief)          | `tools/design-parity/lib/render-live-tools.test.tsx:98-137`                       | The "live" fixture hand-feeds `access_mode: "read_act"` / `"read"` on all six rows. The measured report therefore contains `default.seg.selected` rows — i.e. the harness renders a state production can never produce. Style deltas are real; the Off-everywhere defect is invisible to it. |
| Runtime `off` semantics already exist under another name     | `services/ai-backend/src/agent_runtime/execution/contracts.py:315-319`            | `paused_connectors: frozenset[str]` — "A paused server is invisible, unloadable, and uncallable for the duration of this run." Exactly the semantics `off` needs.                                                                                                                            |
| …and are enforced at two gates                               | `.../capabilities/mcp/permissions.py:44`; `.../mcp/middleware/call_tool.py:82-92` | `is_server_card_authorized` denies paused server_ids for card listing/loading; `CallMcpTool.ainvoke` re-checks the same policy after registry resolve ("Defense-in-depth… a stale tool reference from an earlier turn can't bypass per-chat pausing").                                       |
| Tool-use policy is a _global_ axis, not per-connector        | `.../capabilities/tools/tool_use_enforcement.py:165-167`                          | `_GATED_TOOL_SIDE_EFFECTS = {call_mcp_tool: {EXTERNAL_CALL}}` — one umbrella tool for **all** connectors. It cannot express "Gmail read-only, Linear read+act".                                                                                                                              |
| No per-tool read/write classification exists                 | `.../capabilities/mcp/backend_provider.py:336-377`                                | `_tool_descriptor` builds name/description/schemas/display and hardcodes `risk_level=McpRiskLevel.MEDIUM`. MCP `annotations.readOnlyHint` is **never read**. `read_only` exists only on `McpResourceAccessPolicy` (`cards.py:394`), hardcoded `True`.                                        |
| The policy aggregate **fails open** by contract              | `.../agent_runtime/api/user_policies_resolver.py:63-72`, `:99-125`                | "Implementations must return `{}` (never raise) when the backend lane is not configured or the fetch fails." Network errors are swallowed → deployment defaults. Unsafe transport for a deny decision.                                                                                       |
| Session-carried connector state is frozen at login           | `services/backend/src/backend_app/identity/sessions.py:218-233`, `:391`           | `connector_scopes` is written onto the session record and replayed by session-touch (`backend-facade/auth.py:287-298`). A live mode change would not reach a run until re-login. Unsafe transport too.                                                                                       |
| Every MCP call already passes through one backend chokepoint | `services/backend/src/backend_app/service.py:640-654`; `app.py:1325-1353`         | `proxy_internal_rpc` resolves the server record, decrypts the vault token, and posts the JSON-RPC envelope. `BackendMcpClient._rpc` (`backend_provider.py:303-317`) routes **every** `tools/list` and `tools/call` through it. 15 lines, live, authoritative.                                |
| Card list is the other server-side chokepoint                | `services/backend/src/backend_app/app.py:1195-1209`                               | `GET /internal/v1/mcp/cards` → `McpRegistryService.list_internal_cards`; the ai-backend fetches it per run (`backend_provider.py:60-75`). Omitting a card here makes a connector invisible to the model by construction.                                                                     |
| Write authorization pattern to mirror                        | `services/backend/src/backend_app/connectors/service.py:572-591`                  | `_authorize_write`: 404 when the row is not in the tenant; return when `owner_user_id == caller`; return when caller has `admin`/`owner` (`_ADMIN_ROLES` at `:47`); else `ConnectorForbidden`. 404-not-403 for cross-tenant.                                                                 |
| Re-registration would clobber a stored mode                  | `services/backend/src/backend_app/connectors/store.py:708-721`                    | `upsert_from_mcp_registration` `model_copy(update={...})` overwrites display_name/description/status/scopes/vault_ref on every MCP re-registration. If `access_mode` is added to that update dict, a token refresh silently resets the user's choice.                                        |

Nothing in the brief was refuted. Three line ranges were off by a few lines (noted above); three material facts were **missing** from the brief and are added: the durable DDL lives in `migrations/0044_connectors.sql` (not just the module `schema.sql`), the parity harness cannot see this bug, and `proxy_internal_rpc` is a single live chokepoint that makes real enforcement cheap.

## Design intent

`tools/design-parity/design-kit/app-v3/copilot-app.jsx:136-152` — the control is one row-trailing segmented group with a **fixed three-option tuple in this order**:

```jsx
<div className="seg">
  {[
    ["read", "Read"],
    ["act", "Read & act"],
    ["off", "Off"],
  ].map(([id, l]) => (
    <button
      data-on={c.perm === id ? "true" : undefined}
      onClick={() => setPerm(c.id, id)}
    >
      {l}
    </button>
  ))}
</div>
```

`copilot.css:708-734`:

```css
.seg {
  display: inline-flex;
  gap: 2px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 2px;
}
.seg button {
  font-size: 12px;
  font-weight: 500;
  color: var(--mut);
  border: 0;
  border-radius: 5px;
  padding: 5px 12px;
}
.seg button[data-on="true"] {
  background: var(--panel3);
  color: var(--tx);
}
```

Literal token values (`copilot.css:10-19`): `--panel:#111114` = `rgb(17,17,20)`; `--panel3:#1d1d23` = `rgb(29,29,35)`; `--line:rgba(255,255,255,0.06)`; `--mut:#98989f`; `--tx:#ececf1`.

The design's seed data (`copilot-data.jsx:505-551`) is the decisive tell: five of six connectors carry `perm:"act"` and GitHub carries `perm:"read"`. **Not one row is `off`.** The design's steady state is a populated, differentiated permission column. Live paints six Offs.

Measured deltas (`tools/design-parity/surfaces/tools/out/report-default.md`, group "Permission control") — 3 HIGH, all background:

- `default.seg` backgroundColor `rgb(17,17,20)` → `rgb(9,9,11)`
- `default.seg.selected` backgroundColor `rgb(29,29,35)` → `rgb(13,13,16)`
- `default.seg.read.selected` backgroundColor `rgb(29,29,35)` → `rgb(13,13,16)`

plus MEDIUM: group radius `7px → 8px`, item radius `5px → 6px`, item padding `5px 12px → 4px 10px`, selected weight `500 → 600`. Those come from `AccessModeSegment.tsx:141-176` using `--color-bg` / `--color-bg-elevated` / `--radius-md` / `--radius-sm` instead of panel-family tokens. **Those style rows are assigned to the Tools styling PRD, not this one** — but note they were only measurable because the harness fakes a selected state. See Non-goals.

## Architectural decision

### D1 — The mode is a durable column on the connector row, not a new store

`connectors` is already the denormalized read model the Tools surface renders (`schema.sql:1-14`). Adding a nullable-free column with a CHECK constraint keeps one row = one connector's whole state, keeps the existing RLS tenant policy covering it for free, and keeps `_to_wire` a single projection.

Migration **`0046_connector_access_mode.sql`** (+ `.rollback.sql`, + regenerated `MANIFEST.lock`):

```sql
ALTER TABLE connectors
    ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'read'
    CHECK (access_mode IN ('read', 'read_act', 'off'));

CREATE INDEX IF NOT EXISTS connectors_tenant_access_mode_idx
    ON connectors (tenant_id, access_mode)
    WHERE access_mode <> 'off';
```

Rollback: `ALTER TABLE connectors DROP COLUMN IF EXISTS access_mode;` plus `DROP INDEX IF EXISTS connectors_tenant_access_mode_idx;`.

**Default is `read`, not `off`.** Rationale: existing rows were installed under a regime where the connector was fully usable; defaulting them to `off` would silently break every deployed workspace on migrate, and defaulting to `read_act` would grant more than the user ever saw. `read` is the honest middle: nothing that already worked read-only breaks, and every act is newly gated until the user opts in. The partial index supports the enforcement lookup (`WHERE access_mode <> 'off'`) without indexing the common case twice. `ConnectorRecord` gains `access_mode: ConnectorAccessMode = "read"` (`store.py`), typed by a new `ConnectorAccessMode` StrEnum so the CHECK constraint and the Pydantic model share one enumeration.

`upsert_from_mcp_registration` **must not** include `access_mode` in its `model_copy(update=...)` (`store.py:708-721`, and the in-memory twin at `:347`). A token refresh or re-registration preserves the user's choice; only a first insert sets the default.

### D2 — One new route, mirroring the `scopes` PATCH, returning 200 not 202

`PATCH /v1/connectors/{connector_id}/access-mode`

- Request: `{"access_mode": "read" | "read_act" | "off"}` (`SetConnectorAccessModeRequest`, already in api-types).
- Response `200 OK`: `{"connector": Connector}`. **200, not the scopes route's 202** — scopes returns 202 because it may trigger a re-OAuth round trip; an access-mode change is complete when the row is written.
- `400 invalid_request` — value outside the union (Pydantic literal rejects before the service is reached).
- `403 owner_or_admin_only` — caller is a tenant member but neither owner nor `admin`/`owner` role.
- `404 connector_not_found` — unknown id **or** an id belonging to another tenant (404-not-403, matching `_authorize_write` at `service.py:572-591`).
- Scope guard `Depends(RequireScopes(RUNTIME_USE))`, identity via `BackendServiceAuthenticator.scoped_identity` — identical to `patch_scopes` (`routes.py:409-421`).

**Authorization rule (the permission boundary, stated once):** the connector's `owner_user_id`, or any caller holding `admin`/`owner` in the tenant, may change the mode. Nobody else — including other members of the same tenant who can _read_ the row. Identity is derived from the verified session by the facade and re-derived by `scoped_identity`; the request body carries no identity.

Service method `ConnectorsService.set_access_mode(...)` mirrors `patch_scopes`: `_authorize_write` → no-op return when unchanged (no audit noise) → `model_copy` → inside `store.transaction(org_id=tenant_id)` write the row and append **one** `ConnectorAuditRecord` with `action="connector.access_mode_changed"`, `before_state`/`after_state` via `_safe_dump`, and `correlation_id=f"{previous}->{next}"`. That audit row rides the existing per-tenant HMAC hash chain (`store.py:726+`) — so "who changed this connector's authority, when, from what to what" is tamper-evident and exportable through the existing `GET /v1/connectors/{id}/audit`.

Facade proxy is a byte-for-byte clone of `patch_scopes` (`connector_routes.py:309-324`) with a new `Constants.Paths.ACCESS_MODE` entry. No new auth logic; `FacadeAuthenticator.verify_with_touch` + `service_headers` unchanged.

`_to_wire` gains `access_mode=record.access_mode`; `ConnectorResponseModel` gains the field. In api-types, `Connector.access_mode` **stops being optional** — the backend now always emits it — which lets `ConnectorsDestination.tsx:338`'s `?? "off"` fallback be **deleted**. That fallback is the wrong abstraction: it existed only to paper over a field the server never sent, and it is the direct cause of the Off-everywhere symptom.

### D3 — Enforcement lives at the two chokepoints that already enforce per-chat pausing, plus one live backend gate

A mode that only paints the UI is not done. Three layers, each at a seam that already exists:

**(a) Visibility — `off` connectors are not offered to the model.** `McpRegistryService.list_internal_cards` (`app.py:1195-1209`) joins the connector row for each MCP server (`mcp_connector_slug` at `service.py:605-610` is the existing natural key) and omits cards whose connector is `off`. Fail-closed by construction: the model is never told the tool exists, so there is no call to deny.

**(b) Runtime re-check — mirrors `paused_connectors`.** `AgentRuntimeContext` gains `connector_access_modes: dict[str, ConnectorAccessMode]`, populated **server-side at run-create** from the same `/internal/v1/mcp/cards` response the runtime already fetches (each `McpServerCard` gains `access_mode`). `McpPermissionPolicy.is_server_card_authorized` (`permissions.py:31-49`) denies `off` alongside the existing paused check, so `CallMcpTool.ainvoke`'s defense-in-depth re-check (`call_tool.py:82-92`) catches a stale tool reference from an earlier turn. Zero new HTTP round trips; the field rides a call that already happens.

**(c) The real boundary — `proxy_internal_rpc`.** `services/backend/src/backend_app/service.py:640-654` is the single point every `tools/list` and `tools/call` passes through, and it is on the _authoritative_ side of the trust boundary. Insert the gate **before** `_require_valid_token`, so an `off` connector never even decrypts a vault token:

- `off` → raise, mapped to `403 connector_access_off`.
- `read` + JSON-RPC method `tools/call` + the target tool is not read-only → raise, mapped to `403 connector_access_read_only`.
- `read` + `tools/list`, or `read_act`, or any non-call method → proceed unchanged.

"Is the target tool read-only" requires a classification that does not exist today (`backend_provider.py:336-377` drops it). This PRD adds parsing of MCP tool `annotations.readOnlyHint` (MCP spec tool annotations) into `McpToolDescriptor.read_only: bool | None`, and the gate **fails closed**: `readOnlyHint` absent or false ⇒ treated as acting ⇒ denied under `read`. A connector whose server publishes no annotations is therefore fully usable in `read_act` and list-only in `read` — a conservative, explainable outcome, surfaced to the user in the row's tooltip rather than silently.

**In-flight runs.** Because (c) is a live read on the authoritative side, a mode change takes effect on the **very next tool call**, including inside a run that is already streaming. The run does not die: the call returns the existing `McpToolCallResult.fail(PERMISSION_DENIED, ...)` shape (`call_tool.py:85-92`), which the agent already handles as a recoverable tool failure. The frozen context from (b) may still show the model a tool it can no longer use for the remainder of that run; (c) makes that harmless. This is stated as the contract: **downgrades are effective immediately; upgrades take effect on the next run** (the frozen card list will not grow mid-run).

**Rejected alternatives:**

- _Ship the PATCH only and leave enforcement for later._ Rejected: that is precisely the current defect one layer down — a control that stores a value nothing reads. A permission boundary lands whole or not at all.
- _Carry the mode on `AgentRuntimeContext` alone (no backend gate)._ Rejected: the context is frozen at run-create, so an `off` set during a run would not stop the run, and the ai-backend is the _client_ side of the boundary — enforcing only there means a runtime bug is a permission bypass.
- _Transport the mode via `/internal/v1/policies/runtime`._ Rejected: `UserPoliciesResolver` is contractually **fail-open** (`user_policies_resolver.py:63-72`) — a 5-second timeout would silently un-gate every `off` connector.
- _Transport it on the session record next to `connector_scopes`._ Rejected: sessions freeze the value at login (`sessions.py:218-233`), so a revocation would not apply until the user signs out.
- _Reuse the tool-use policy axes (`read`/`write`/`destructive`)._ Rejected: that policy gates the single umbrella `call_mcp_tool` tool for **all** connectors at once (`tool_use_enforcement.py:165-167`); it structurally cannot express per-connector authority. The two are orthogonal and compose: access mode decides _whether_ a connector may act; tool-use policy decides _whether that act needs approval_.
- _Give each connector its own model-visible tool so per-connector policy works._ Rejected: explodes the model's tool surface and rewrites the MCP dispatcher for a permission feature.

### D4 — The optimistic-mutation logic moves into chat-surface behind one port

The same defect on two hosts must be fixed at the seam. Today 40 lines of optimistic-apply / PATCH / revert / error-banner live only in `apps/frontend/.../ConnectorsRoute.tsx:304-345`. Copying them into `destinationBinders.tsx` would be the bandaid.

Add `packages/chat-surface/src/destinations/connectors/ports/ConnectorAccessPort.ts` — one method, following the `FirstRunConnectorsPort` precedent (`packages/chat-surface/src/onboarding/ports/FirstRunConnectorsPort.ts:30-56`):

```ts
export interface ConnectorAccessPort {
  /** PATCH /v1/connectors/{id}/access-mode → the reconciled server row. */
  setAccessMode(id: ConnectorId, mode: ConnectorAccessMode): Promise<Connector>;
}
```

`ConnectorsDestination` accepts `accessPort?: ConnectorAccessPort` and owns the optimistic overlay, the revert on rejection, and the error banner (`data-testid="connectors-access-mode-error"`). `onSetAccessMode` is **removed** — it is the callback shape that forced each host to re-implement the state machine. Web supplies the port over `connectorsApi.setConnectorAccessMode`; desktop supplies it over `transport.request({method:"PATCH", path:"/v1/connectors/{id}/access-mode", body})`. Both are ~6 lines. chat-surface stays substrate-clean (no fetch/IPC).

## Scope

**`services/backend`**

- `migrations/0046_connector_access_mode.sql` — add column + CHECK + partial index.
- `migrations/0046_connector_access_mode.rollback.sql` — drop index + column.
- `migrations/MANIFEST.lock` — regenerate via `python tools/check_migration_manifest.py --write`.
- `src/backend_app/connectors/schema.sql` — keep the module mirror in sync with 0044+0046.
- `src/backend_app/connectors/store.py` — `ConnectorAccessMode` StrEnum; `ConnectorRecord.access_mode`; column in both adapters' SELECT/INSERT/UPDATE; **exclude** from `upsert_from_mcp_registration`'s update dict (both adapters).
- `src/backend_app/connectors/service.py` — `set_access_mode()` with `_authorize_write` + no-op short-circuit + `connector.access_mode_changed` audit row.
- `src/backend_app/connectors/routes.py` — `SetAccessModeRequestModel`/`ResponseModel`; `access_mode` on `ConnectorResponseModel` and in `_to_wire`; `PATCH /v1/connectors/{id}/access-mode`.
- `src/backend_app/service.py` — `list_internal_cards` omits `off` connectors and stamps `access_mode` on each card; `proxy_internal_rpc` gains the pre-token access gate.
- `src/backend_app/contracts.py` — `access_mode` on the internal MCP card model.
- `src/backend_app/app.py` — map the new gate's exception to 403 on `/internal/v1/mcp/servers/{id}/rpc`.

**`services/backend-facade`**

- `src/backend_facade/connector_routes.py` — `Constants.Paths.ACCESS_MODE` + one `@app.patch` proxy.

**`services/ai-backend`**

- `src/agent_runtime/execution/contracts.py` — `connector_access_modes` field + normalizing validator (mirrors `_normalize_paused_connectors`).
- `src/agent_runtime/capabilities/mcp/cards.py` — `McpServerCard.access_mode`; `McpToolDescriptor.read_only: bool | None`.
- `src/agent_runtime/capabilities/mcp/backend_provider.py` — parse `access_mode` off the card payload; parse `annotations.readOnlyHint` in `_tool_descriptor`.
- `src/agent_runtime/capabilities/mcp/permissions.py` — deny `off` in `is_server_card_authorized`.
- `src/agent_runtime/api/run_coordinator.py` — thread `connector_access_modes` onto the run context server-side.

**`packages/api-types`**

- `src/connectors.ts` — `Connector.access_mode` becomes required; doc comment updated to record that the route now exists.
- `src/connectors.test.ts` — flip the "optional" assertion to "required".

**`packages/chat-surface`**

- `src/destinations/connectors/ports/ConnectorAccessPort.ts` — new port.
- `src/destinations/connectors/ConnectorsDestination.tsx` — accept `accessPort`, own optimistic/revert/banner, **delete** `onSetAccessMode` and the `?? "off"` fallback at `:338`.
- `src/index.ts` — export the port type.

**`apps/frontend`**

- `src/features/connectors/ConnectorsRoute.tsx` — delete `handleSetAccessMode` (`:304-345`); pass a port; keep the terminal-Connect call (`:428-445`) routed through the same port.

**`apps/desktop`**

- `renderer/destinationBinders.tsx` — build a `ConnectorAccessPort` over `Transport` and pass it to `ConnectorsDestination`.

**`tools/design-parity`**

- `lib/render-live-tools.test.tsx` — fixture must be built from a backend-shaped `Connector` (i.e. `access_mode` present because the wire type requires it), not hand-forced, so the harness can never again render a state production cannot produce.

## Non-goals

- **The segment's computed-style deltas** (`--panel`/`--panel3` vs `--color-bg`/`--color-bg-elevated`, `7px/5px` radii, `5px 12px` padding, weight 500). Real, measured, and owned by the Tools styling PRD. This PRD must not touch `AccessModeSegment.tsx`'s style objects.
- **Per-tool granularity.** The mode is per-connector. "Allow `send_email` but not `delete_thread`" is the tool-use policy's `destructive` axis, unchanged.
- **Changing the global approval policy.** Settings → Model & behavior keeps deciding whether an _allowed_ act needs a human. `read_act` grants authority; it does not skip approval.
- **Per-chat overrides.** The conversation-scoped `enabled_connectors` pause (`runtime_api/schemas/conversations.py:179-200`) stays as-is. Access mode is the tenant-durable floor; the per-chat toggle is a narrower, session-scoped mute on top of it. No merge-precedence UI in this PRD (semantics: the more restrictive of the two wins).
- **Backfilling historical intent.** Every pre-existing row migrates to `read`. There is no prior signal to recover.
- **An admin-wide policy that pins a connector's mode for all members.** Owner-or-admin per-row is the boundary this PRD ships.
- **Emitting `access_mode` on the connectors SSE stream.** The row refetch after PATCH is sufficient; stream projection is a follow-up.

## Risks & rollback

| Risk                                                                                                                     | Guard                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`ConnectorResponseModel` is `extra="forbid"`** — a mismatched field name between store and wire is a 500 on every list | `services/backend/tests/unit/connectors/test_connectors_routes.py` exercises the list/detail projection; add an assertion that `access_mode` round-trips.                                                                                |
| Migration default `read` silently downgrades a workspace that was effectively acting                                     | Deliberate and documented. The one-time effect is: acts now require the user to click "Read & act". Release note required. Alternative (`read_act` default) grants authority nobody chose — worse.                                       |
| `upsert_from_mcp_registration` clobbers the mode on token refresh                                                        | `services/backend/tests/unit/connectors/test_mcp_write_through.py` — add a test that a re-upsert over a row with `access_mode="off"` leaves it `off`.                                                                                    |
| The `proxy_internal_rpc` gate breaks all MCP traffic if the connector row is missing for a registered MCP server         | Gate must **skip** (allow) when no connector row joins the server — an unprojected server is not a user-set `off`. Test both branches.                                                                                                   |
| `readOnlyHint` fail-closed makes `read` mode useless against servers with no annotations                                 | Intended and bounded: `read` still permits `tools/list`. Surfaced in UI copy. `services/ai-backend/tests/unit/agent_runtime/capabilities/mcp/` covers descriptor parsing.                                                                |
| Deleting `onSetAccessMode` is a breaking prop change                                                                     | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.test.tsx` + `apps/frontend/src/features/connectors/__tests__/ConnectorsRoute.test.tsx:320-420` fail loudly if a host is missed. TypeScript catches it at build. |
| Migration checksum drift breaks CI                                                                                       | `python tools/check_migration_manifest.py` is the CI guard; run `--write` in the same commit.                                                                                                                                            |

**Rollback.** Three independent stages, revertable in reverse order: (1) revert the host + chat-surface commits — the segment goes back to reading `access_mode` and the port disappears; (2) revert the enforcement commits in `services/backend` + `services/ai-backend` — the gate is removed, behaviour returns to today's ungated MCP path while the column stays and keeps serving the UI truthfully; (3) apply `0046_connector_access_mode.rollback.sql` and revert `MANIFEST.lock` — only needed if the column itself is the problem. Stage 2 without stage 3 is a valid resting state (honest display, no enforcement); stage 3 without stage 1 is not (the wire type requires the field).

## Definition of Done

1. `cd services/backend && .venv/bin/python -m pytest tests/unit/connectors/test_connectors_routes.py` passes, including a new test asserting `PATCH /v1/connectors/{id}/access-mode` with `{"access_mode":"read_act"}` returns **`200`** and a body whose `connector.access_mode == "read_act"`.
2. The same file asserts the permission boundary: a caller who is a tenant member but neither `owner_user_id` nor holder of `admin`/`owner` gets **`403 owner_or_admin_only`**; a caller from a different tenant gets **`404 connector_not_found`** (not 403); a body of `{"access_mode":"maybe"}` gets **`400`**.
3. `cd services/backend && .venv/bin/python -m pytest tests/unit/connectors/test_connectors_service.py` passes with a test asserting one `ConnectorAuditRecord` with `action == "connector.access_mode_changed"` and `correlation_id == "read->off"` is appended per change, and **zero** rows when the mode is set to its current value.
4. `cd services/backend && .venv/bin/python -m pytest tests/unit/connectors/test_mcp_write_through.py` passes with a test that `upsert_from_mcp_registration` over an existing row whose `access_mode == "off"` returns a record still equal to `"off"`.
5. `python tools/check_migration_manifest.py` exits 0, and `services/backend/migrations/MANIFEST.lock` contains a line beginning `0046_connector_access_mode sha256=`.
6. `grep -c "access_mode" services/backend/migrations/0046_connector_access_mode.sql` ≥ 1 and the file contains the literal `CHECK (access_mode IN ('read', 'read_act', 'off'))` with `DEFAULT 'read'`.
7. `cd services/backend-facade && .venv/bin/python -m pytest tests/test_connectors_proxy.py` passes (new file) with a test asserting the facade forwards `PATCH /v1/connectors/{id}/access-mode` to `{backend}/v1/connectors/{id}/access-mode` with `org_id`/`user_id` query params and `FacadeAuthenticator.service_headers`, and that the client-supplied body is forwarded unmodified.
8. **Enforcement — `off`:** `cd services/backend && .venv/bin/python -m pytest tests/unit/` passes with a test asserting `McpRegistryService.proxy_internal_rpc` raises (→ `403`) for a server whose connector row is `access_mode="off"`, **and** that `token_vault.decrypt` was never called on that path.
9. **Enforcement — `read`:** the same suite asserts `proxy_internal_rpc` allows a `tools/list` envelope under `access_mode="read"`, allows a `tools/call` whose target tool advertises `annotations.readOnlyHint: true`, and rejects a `tools/call` whose target tool has **no** `annotations` key (fail-closed).
10. **Enforcement — no regression:** the same suite asserts `proxy_internal_rpc` is unchanged (allows everything) when the MCP server has no joined connector row.
11. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/capabilities/mcp/` passes with (a) a test that `McpPermissionPolicy.is_server_card_authorized` returns `False` when `context.connector_access_modes[server_id] == "off"`, and (b) a test that `_tool_descriptor` sets `read_only=True` for `{"annotations":{"readOnlyHint":true}}` and `read_only=None` when `annotations` is absent.
12. `npm run test --workspace @0x-copilot/chat-surface` passes with `ConnectorsDestination.test.tsx` asserting: rendering a connector with `access_mode:"read_act"` produces `data-testid="access-mode-segment"` with `data-value="read_act"`; clicking `access-mode-option-off` calls `accessPort.setAccessMode(id,"off")` exactly once, flips `data-value` to `"off"` optimistically, and on a rejected promise reverts to `"read_act"` **and** renders `data-testid="connectors-access-mode-error"`.
13. `grep -rn 'access_mode ?? "off"' packages/chat-surface/src` returns **no matches**, and `grep -rn "onSetAccessMode" packages/chat-surface/src apps/frontend/src apps/desktop/renderer` returns **no matches** — the callback shape and the least-privilege fallback are both deleted, not merely bypassed.
14. `npm run typecheck --workspace @0x-copilot/api-types` passes with `Connector.access_mode` declared **without** `?`, and `packages/api-types/src/connectors.test.ts` asserts a `Connector` literal omitting `access_mode` is a type error (via `@ts-expect-error`).
15. **Regression guard for this exact bug:** `apps/desktop/renderer/__tests__/destinationBinders.test.tsx` asserts that `ConnectorsBinder`, given a transport returning two connectors with `access_mode` `"read"` and `"read_act"`, renders two segments whose `data-value` attributes are `"read"` and `"read_act"` — i.e. **not** both `"off"` — and that clicking a third option issues a `PATCH` to `/v1/connectors/{id}/access-mode`.
16. **Design value pinned numerically:** `packages/chat-surface/src/destinations/connectors/AccessModeSegment.test.tsx` asserts the radiogroup renders exactly **3** radios whose accessible names are, in order, `"Read"`, `"Read & act"`, `"Off"` — matching `tools/design-parity/design-kit/app-v3/copilot-app.jsx:138-141` `[["read","Read"],["act","Read & act"],["off","Off"]]` — and that exactly one has `aria-checked="true"`.
17. `tools/design-parity/lib/render-live-tools.test.tsx` builds its six-row fixture from the required-field `Connector` type (no local `access_mode` literal injection beyond the design's own mix: five `read_act`, one `read`, matching `copilot-data.jsx:505-551`), and `node --test` / the harness run still produces a report; the design-parity report for `tools` shows **0 HIGH rows for anchor group "Permission control" attributable to a missing/incorrect `data-value`** (re-run per `tools/design-parity/SKILL.md`). Remaining background/radius/padding HIGH+MEDIUM rows in that group are expected and owned by the Tools styling PRD.
18. `npm run typecheck --workspace @0x-copilot/frontend` and `npm run typecheck --workspace @0x-copilot/desktop` both pass.

## Dependencies

**Must land first:** none — this PRD is self-contained across `backend` → `facade` → `api-types` → `chat-surface` → both hosts, and touches no file the sibling design-parity PRDs own.

**Ordering note:** the Tools styling PRD (segment tokens: `--panel`/`--panel3`, `7px`/`5px` radii, `5px 12px` padding) edits `AccessModeSegment.tsx`'s style objects while this PRD edits only its call sites and tests. Land this one first if they collide — a truthful `data-value` is a prerequisite for the styling PRD's `.seg.selected` anchors to measure anything real (today those anchors only exist because the harness fakes a selection, per Evidence).

**This unblocks:** any per-connector authority work that needs a durable authority field — connector-scoped project allowlists, the Activity surface's "which tool acted under whose authority" attribution, and admin-level org-wide connector policy (which will extend `set_access_mode`'s authorization rule rather than introduce a second store).
