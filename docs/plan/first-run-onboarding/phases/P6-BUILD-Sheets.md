# P6-BUILD-Sheets — Google Sheets connector (build-ready v2)

**Status:** authoritative build spec. **Supersedes the Sheets sections of `phases/P6-ENG-plan.md` §4 and re-frames `phases/PRD-P6b.md`** wherever they assume the pre-`RESOLVED` architecture (a remote HTTPS Google Sheets MCP behind a per-deployment Google OAuth client). Owner posture: principal engineer. No bandaids — every control lands as a real component with a real enforcement point in code.

Build order for P6 is unchanged: **Sheets first, Safe second** (`P6-PRD-product.md` §7.1). Safe stays in `phases/P6-ENG-plan.md` Track W + `PRD-P6a-hardened.md`; this doc is Track S only.

All paths are relative to ROOT. Every file:line was re-verified against HEAD of `claude/0xcopilot-first-run-onboarding-d7eb30`.

---

## 0. What changed from v1 — the RESOLVED-decision pivot (read first)

`STATUS.md` records decision **C** as `RESOLVED (user, 2026-07-22)`:

> Sheets = **adopt an existing Sheets MCP** (prefer official Google Sheets MCP; else `xing5/mcp-google-sheets`), **bundled + version-pinned + run as a LOCAL server**, with **service-account auth as the DEFAULT** (no Google OAuth client to own, no consent screen, no sensitive-scope verification). FTUE row degrades honestly to "share your sheet with `copilot-svc@…iam.gserviceaccount.com` (Editor)". OAuth stays an ALTERNATE mode.

This overturns the **registration path** that v1 (`P6-ENG-plan.md` §4) and `PRD-P6b.md` §3–4 built against. Those wired Sheets through the **desktop OAuth overlay** (`desktop_profiles.yaml` + `_requested_permissions` write-scope widening + `requires_pre_registered_client`). That overlay **cannot host a local server**, by three hard loader invariants in `services/backend/src/backend_app/connectors/profile_catalog.py`:

- `endpoint_template` must be **HTTPS** — `_validate_https_endpoint` rejects any non-`https` scheme (`profile_catalog.py:106-114`). A local server is `http://127.0.0.1:<port>`.
- `transport` is `Literal["http"]` only (`profile_catalog.py:93`) — no stdio/loopback variant.
- a profile-owned seed **must** set `requires_pre_registered_client: true` (`_assert_installable_server`, `profile_catalog.py:263-267`) — i.e. it structurally assumes a remote OAuth client.

So the v2 default takes the **other** shipped registration path: a **local MCP server seeded through `mcp_catalog` / the MCP register-install machinery with `auth_mode=NONE`**, which the **connectors write-through** (migration `0044_connectors`) already projects as a first-class connector. The v1 OAuth overlay work is **retained verbatim as the ALTERNATE mode** (§7), not deleted — but it is no longer what ships first.

**One-line summary of the v2 architecture:**

```
bundled xing5/mcp-google-sheets  →  desktop supervisor spawns it on a loopback port
   (SA JSON via SERVICE_ACCOUNT_PATH)         │  http://127.0.0.1:<sheetsPort>/…  (streamable-HTTP)
                                              ▼
backend seeds a NONE-auth `gsheets` server (SHEETS_MCP_URL) ──► connectors write-through
   (proxy_internal_rpc is the SINGLE MCP egress; needs a NONE-auth no-bearer fix)   (0044 row, status=connected)
                                              ▼
runtime call_mcp_tool per-call HITL + name-based read/write classifier (durable allowlist fix)
```

---

## 1. Which existing Sheets MCP to adopt

### 1.1 Evaluation

| Axis                        | **`xing5/mcp-google-sheets`** (recommended)                                                                                                                                                                                | "Official Google Sheets MCP"                                                                                                                                                                                                                                                            | A self-hosted `services/sheets-mcp` (REST v4 wrapper)                                                                                    |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Exists & pinnable today     | **Yes** — PyPI `mcp-google-sheets`, `uvx mcp-google-sheets@…`; a concrete released version to pin.                                                                                                                         | **No standalone, cell-write, service-account server verifiably pinnable.** Google's Workspace MCP efforts are OAuth-oriented and moving; third-party "google-sheets-mcp" repos (amahpour, henilcalagiya) exist but are not "official". Treat "official" as aspirational, not shippable. | Would exist but is **net-new work** (own venv/Dockerfile/deploy) — exceeds P6b (`PRD-P6b.md` §3 out-of-scope; `P6-PRD-product.md` §3.2). |
| License                     | **MIT** — clean to bundle + redistribute.                                                                                                                                                                                  | n/a                                                                                                                                                                                                                                                                                     | ours                                                                                                                                     |
| Service-account auth        | **Yes, default-friendly** — `SERVICE_ACCOUNT_PATH` (JSON key file); also `CREDENTIALS_CONFIG` (base64), `CREDENTIALS_PATH` (OAuth), ADC.                                                                                   | varies                                                                                                                                                                                                                                                                                  | ours                                                                                                                                     |
| Cell-write coverage         | **Full** — `update_cells`, `batch_update_cells`, `batch_update`, `add_rows`, `add_columns`, `create_sheet`, `create_spreadsheet`, `copy_sheet`, `rename_sheet`, `share_spreadsheet`.                                       | varies                                                                                                                                                                                                                                                                                  | ours                                                                                                                                     |
| Read coverage               | `get_sheet_data`, `get_sheet_formulas`, `get_multiple_sheet_data`, `get_multiple_spreadsheet_summary`, `list_sheets`, `list_spreadsheets`, `list_folders`, `find_in_spreadsheet`, `search_spreadsheets`.                   | varies                                                                                                                                                                                                                                                                                  | ours                                                                                                                                     |
| Transport                   | stdio by default; also runs as a streamable-HTTP server. **We need HTTP** (see §1.3 — the backend egress is HTTP-only).                                                                                                    | —                                                                                                                                                                                                                                                                                       | ours                                                                                                                                     |
| Tool-name → classifier risk | **High** — five mutating tools carry no recognized write-term (`add_rows/add_columns/copy_sheet/rename_sheet/share_spreadsheet`). Since we adopt **as-is**, we can't rename them → forces the durable classifier fix (§4). | —                                                                                                                                                                                                                                                                                       | we'd control names                                                                                                                       |

**Decision: adopt `xing5/mcp-google-sheets`, version-pinned, run as a local streamable-HTTP server.** It is the only candidate that is MIT-licensed, service-account-native, cell-write-complete, and pinnable today. Keep "prefer official" as a documented future swap: the connector contract (a local HTTP MCP endpoint + SA env) is server-agnostic, so replacing it later is a config change, not a re-architecture.

**Full tool surface (verbatim, 19 tools)** — load-bearing for §4:
`add_columns`, `add_rows`, `batch_update_cells`, `batch_update`, `copy_sheet`, `create_sheet`, `create_spreadsheet`, `find_in_spreadsheet`, `get_multiple_sheet_data`, `get_multiple_spreadsheet_summary`, `get_sheet_data`, `get_sheet_formulas`, `list_folders`, `list_sheets`, `list_spreadsheets`, `rename_sheet`, `search_spreadsheets`, `share_spreadsheet`, `update_cells`.

### 1.2 Bundling + version pin

Third-party server code is a supply-chain input; pin and vendor it, don't `uvx …@latest` at runtime.

- **Pin:** record the exact released version in a new manifest `tools/sheets-mcp/PIN` (e.g. `mcp-google-sheets==<x.y.z>` + the resolved `--require-hashes` lock). Review the pinned source before first bundle (RESOLVED-C caveat: "third-party MCP code is pinned/reviewed").
- **Vendor for desktop:** stage the pinned server + its Python deps into the desktop runtime the same way the three services are staged (`tools/desktop-runtime/stage.mjs`, `manifest.json`). It runs under the **bundled Python** the supervisor already ships (`paths.pythonBin`, `desktop-supervisor.ts:286`), so no new runtime toolchain. Add a `sheets-mcp` entry to `tools/desktop-runtime/manifest.json` and stage its wheel + `SERVICE_ACCOUNT` mount point.
- **Vendor for self-host:** a pinned image `deploy/self-host/…/sheets-mcp` (own Dockerfile installing only the pinned wheel), added to the web compose stack as an internal-network service. No public ingress — only the `backend` service dials it.
- **CI supply-chain gate:** a test asserts the pin file resolves to a single hashed version and that the bundled tree contains no OAuth-client secret and no unpinned `@latest` invocation.

### 1.3 Running it as a LOCAL server (relate to desktop process supervision)

The **backend is the single MCP egress**: the ai-backend never dials an MCP server directly — `BackendMcpClient._rpc` POSTs every JSON-RPC envelope through `…/internal/v1/mcp/servers/{id}/rpc` (`services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py:303-320`), and the backend's `proxy_internal_rpc` (`services/backend/src/backend_app/service.py:621-635`) is what physically connects, via `_post_remote_mcp_rpc` — a plain **HTTP POST to `record.url`** with `Accept: application/json, text/event-stream` (`service.py:776-803`). There is **no stdio spawn anywhere in the backend egress** and `transport` is not even consulted on the RPC path. Therefore the local Sheets MCP **must expose a streamable-HTTP endpoint on a loopback URL** — stdio is not reachable without net-new backend transport code (out of scope).

**Desktop (primary FTUE target).** The desktop already supervises a fixed set of children — Postgres + `backend` + `ai-backend` + `backend-facade` — each spawned by `createService` (`apps/desktop/main/services/desktop-supervisor.ts:270-306`) on `127.0.0.1:<allocated port>` with health-gating (`waitForHealthy`). Add the Sheets MCP as a **fifth supervised child**:

1. Extend `SupervisedServiceName` (`apps/desktop/main/services/runtime-paths.ts:28`) — or add a parallel `SupervisedSidecarName` if you want to keep the uvicorn-shaped union clean — with `"sheets-mcp"`. It is **not** a uvicorn app, so give it its own spawn shape (command = bundled python `-m mcp_google_sheets --transport http --host 127.0.0.1 --port <p>`; confirm the pinned server's HTTP-serve flag names at bundle time) rather than the `UVICORN_MODULES` template (`service-env.ts:38`).
2. Allocate a `sheets` port alongside the existing four (`AllocatedPorts`, `supervisor.ts:16-21`) and `portFor` (`desktop-supervisor.ts:312`).
3. Inject the SA credential into the child's env as `SERVICE_ACCOUNT_PATH` (§2). Start the Sheets MCP **before** the backend health-gate completes so the seed (§3) can be registered against a live endpoint; treat its health like the other children (crash-loop → `onFatal`).
4. Pass `SHEETS_MCP_URL=http://127.0.0.1:<sheetsPort>/…` (and `SHEETS_MCP_SERVICE_ACCOUNT_EMAIL=<client_email>` for the degrade copy, §2.3) into the **backend** child's env via `buildServiceEnv` (`service-env.ts:169`). This is the single hand-off the backend needs.

**Gate:** the child + the seed only come up when SA config is present **and** preview is enabled (§5). Absent SA config → the child is not spawned, `SHEETS_MCP_URL` is unset, the seed is absent, and the FTUE row degrades honestly (§2.3) — never a dead button.

**Self-host (web Docker stack).** The Sheets MCP runs as an internal-network container (`deploy/self-host`), reachable by the `backend` service at `http://sheets-mcp:<port>/…`; `SHEETS_MCP_URL` points there. Because the seed URL bypasses the public-URL validator (§3.2), a compose-DNS host is fine. **Self-host caveat (call out in the runbook):** one shared service account across tenants is coarse — the SA only ever sees sheets a user explicitly shared with it (Google-enforced), but per-tenant SA isolation is the hardening for multi-tenant self-host; single-user desktop is unaffected.

---

## 2. Service-account auth as the default

### 2.1 Where the SA JSON is provided

The SA JSON is an **operator/desktop config input**, injected into the **Sheets MCP child process env** (`SERVICE_ACCOUNT_PATH`), never sent over the wire and never placed in a URL:

- **Desktop:** the operator drops a service-account key file into the desktop app's config dir (a `sheets-service-account.json` under the runtime dir / userData), or supplies it through the desktop secure-config path used for boot secrets (`apps/desktop/main/services/boot-secrets.ts`). The supervisor resolves that path and passes it to the child as `SERVICE_ACCOUNT_PATH`. It is read **only** by the Sheets MCP process; the desktop main process and the Python services never parse it.
- **Self-host:** a Docker secret / mounted file on the `sheets-mcp` container; `SERVICE_ACCOUNT_PATH` points at the mount. Never baked into the image, never committed (CLAUDE.md secret rules; `P6-PRD-product.md` §5.2.9).

### 2.2 How the connector record stores it — it doesn't (and shouldn't)

From 0xCopilot's side the local Sheets server is **`auth_mode=NONE`**: there is no OAuth handshake, no bearer, no client secret that 0xCopilot holds. The Google credential lives **with the MCP process** (the SA JSON), not in the connector record. So:

- **Do NOT** store the SA JSON in `TokenVault` and **do NOT** overload `mcp_catalog.CatalogEntry` / the record's `oauth_client` (`service.py:_oauth_client_config`) with it. `oauth_client` is for a _pre-registered OAuth client_ (`mcp_oauth.py`), which the SA default explicitly avoids. Repurposing it would re-introduce exactly the client-ownership liability RESOLVED-C removed.
- The connector **record** is a NONE-auth server whose only stored field of interest is `url` (`SHEETS_MCP_URL`). Its `auth_state` is `AUTHENTICATED` by construction (`service.py:242-244`, `:381-384`) and it projects to `status=connected` with no token (`project_mcp_status`, `service.py:639-640`).
- If a deployment prefers not to keep the SA key on the MCP host's filesystem, the SA JSON _may_ be stored encrypted at rest via `TokenVault` and materialized to a tmpfs path at child-spawn — but that is an **operator hardening**, keyed to the process, **not** the connector record. Default is the plain mounted file.

**Consequence for the egress path (a real, required backend change — see §6-B1):** `proxy_internal_rpc` today **unconditionally** requires a token — `_require_valid_token(record)` raises `"MCP server is not authenticated"` when no token exists (`service.py:629-635`, `:731-738`) — and `_post_remote_mcp_rpc` always sends `Authorization: Bearer <token>` (`service.py:782-788`). A NONE-auth local server has **no token**, so the first tool call would fail today. This path has never been exercised for a NONE-auth server (the existing NONE-auth catalog entries are never called as tools in tests). **Fix:** for `record.auth_mode == McpAuthMode.NONE`, skip `_require_valid_token` and omit the `Authorization` header (make `access_token` optional in `_post_remote_mcp_rpc`). This is the load-bearing enabler for the whole local-server default; ship it with a regression test.

### 2.3 The FTUE-row degrade (relate to PRD-P6b §4.3 and the desktop profile 409/needs-setup)

`PRD-P6b.md` §4.3 designed the OAuth degrade as a `connector_oauth_setup_required` → **HTTP 409** "needs setup" card when no operator client exists. Under the SA default there is no OAuth client, so the equivalent honest degrade has **two distinct states**:

1. **SA not configured** (no `SHEETS_MCP_URL`) → there is no installable server behind the `gsheets` slug. The marketing row still renders (catalog.yaml, §3.1) but its connect affordance is a **needs-setup card**: _"Add a Google service account to enable Sheets."_ Mirror the desktop overlay's honest-unavailable posture (`ConnectorAvailability.ADMIN_SETUP_REQUIRED` / `PREVIEW`, `profile_catalog.py:140-152`) rather than the OAuth 409 code — the 409 belongs to the OAuth alternate (§7). Never a dead button (acceptance `P6-PRD-product.md` §5.2.7).
2. **SA configured but the user's sheet isn't shared with it** → the SA can create + own new sheets and read/write them, but **cannot reach the user's existing personal sheets until shared** (RESOLVED-C caveat). A tool call against an unshared sheet returns a Google `403`. Surface this proactively in the FTUE row subtitle and as the tool-error card copy: _"Share your sheet with `<SHEETS_MCP_SERVICE_ACCOUNT_EMAIL>` (Editor)."_ The `<client_email>` is read from the SA JSON's `client_email` and passed to the backend as `SHEETS_MCP_SERVICE_ACCOUNT_EMAIL` (§1.3) so the copy is exact, not a placeholder. Google upstream errors are caught and mapped to a sanitized typed error before they can reach model output (same discipline as `PRD-P6a-hardened.md` M6; never leak the SA email into logs beyond the intended card, never leak key material).

**OAuth alternate mode (note only, do not build first).** For deployments that want a branded Google consent screen instead of SA sharing, the v1 overlay (`desktop_profiles.yaml google-sheets` + `_requested_permissions` write-scope widening + `requires_pre_registered_client`) is the alternate. It is fully specified in §7; it is **not** on the default ship path.

---

## 3. Registration into the connectors store (`0044_connectors`) + the write-through

Verified against the store, the service, and `tests/unit/connectors/test_mcp_write_through.py` — **do not assume the shape; this is what the tests actually assert.**

### 3.1 The seed + marketing row

- **Marketing catalog** (`services/backend/src/backend_app/connectors/catalog.yaml`): add a `gsheets` entry after `gdrive` (`catalog.yaml:44-47`):
  ```yaml
  - slug: gsheets
    display_name: Google Sheets
    description: Read and write spreadsheet cells and formulas.
    icon_hint: gsheets
  ```
  This gives the connectors destination + FTUE popover a card for the slug (marketing entries without a desktop profile are already normal — e.g. `gcal`, `slack`). The write-through's projected slug must match this: `mcp_connector_slug` strips `seed:` → `gsheets` (`service.py:600-605`), so the seed's server_id **must** be `seed:gsheets`.
- **The installable server is a NONE-auth local seed, registered conditionally at boot.** `mcp_catalog.DEFAULT_CATALOG` is a static tuple of _remote_ brands (`mcp_catalog.py:83`) and the desktop child's loopback port is dynamic, so **do not** hard-code `gsheets` into `DEFAULT_CATALOG`. Instead add a boot-time helper — `ensure_local_sheets_connector(app)` invoked from app startup — that, **only when `SHEETS_MCP_URL` is set**, idempotently ensures a server record: `server_id="seed:gsheets"`, `name="gsheets"`, `display_name="Google Sheets"`, `url=$SHEETS_MCP_URL`, `transport=McpTransport.HTTP`, `auth_mode=McpAuthMode.NONE`, `enabled=True`. Reuse the idempotent ensure/create machinery (`_ensure_server` shape at `oauth_coordinator.py:264-296`; `create_server`/`install_from_catalog` at `service.py:217`,`:346`) so a reboot re-converges rather than duplicating. Absent `SHEETS_MCP_URL` → **no seed** → the degrade state §2.3(1).
  - _Why a conditional boot seed, not `DEFAULT_CATALOG` and not register-by-URL:_ the register-by-URL route validates a **public** URL and rejects loopback/private hosts unless `allow_localhost=True` (`Validators.validate_public_mcp_url`, `contracts.py:104-135`), so a user cannot register the loopback URL and we should not widen that route. A seed/ensure record carries its `url` straight through without the public-URL validator, which is the correct, minimal path for a trusted local endpoint. It also makes the whole connector **config-gated for free** (no config ⇒ no seed).

### 3.2 The write-through — exact path + shape (from the tests)

Every MCP mutation route already write-throughs; the seed registration rides the same helper. The path is:

`(_ensure/create/install)` → `_connector_write_through(app, record, action=…)` (`app.py:337-391`) → builds `mcp_input = mcp_upsert_input_from_server(record, existing=…)` (`service.py:647-694`) → `ConnectorsService.write_through_from_mcp(mcp_input=…, actor_user_id=record.user_id, action=…, correlation_id=f"mcp:{record.server_id}")` (`service.py:270-298`) → inside `store.transaction()`: `store.upsert_from_mcp_registration(mcp_input)` + `store.append_audit(ConnectorAuditRecord(...))` (atomic row + audit) → publish on the tenant SSE bus.

For the NONE-auth `gsheets` seed the projection is deterministic:

- `project_mcp_status` → **`("connected", None)`** because `auth_mode == McpAuthMode.NONE` (`service.py:639-640`). The row lands `status=connected` immediately — **no OAuth round-trip, no 409** (contrast the OAuth alternate). This is exactly the shape `test_mcp_write_through.py::TestRegisterByUrl::test_no_auth_server_lists_as_connected` asserts for a `auth_mode="none"` register (`test_mcp_write_through.py:148-154`).
- `slug="gsheets"`, `display_name="Google Sheets"`, `owner_user_id=<user>`, `tenant_id=<org>`, `vault_ref=f"mcp:seed:gsheets"`, `scopes=()` (NONE-auth has no OAuth scopes; the SA's authority is not an OAuth scope set).
- **Audit + SSE + isolation** are inherited and already tested: a `connector.*` audit row is appended (`test_mcp_write_through.py:163-172`), a `connector.created`/`status_changed` SSE event is published with **no `vault_ref`/token bytes on the wire** (`:284-314`), and the row is invisible to other tenants (`:424-431`, RLS in `0044_connectors.sql:55-62`).
- **Failure discipline:** write-through is log-and-continue — a connectors-store failure never fails the MCP mutation (`app.py:355-364`; `test_mcp_write_through.py:457-473`). Preserve it for the seed path.

**Do not add a new store or a new write path for Sheets.** The `0044_connectors` read model + the existing write-through is the single projection; the seed just feeds it. (The Safe half _does_ add a first-class `safe_bindings` store — that is a different, security-critical concern; Sheets rides the shared connector plumbing.)

### 3.3 Per-chat scope + first-use consent (unchanged, inherited)

Pausing `gsheets` for a conversation rides the existing `paused_connectors` gate (`PATCH /v1/agent/conversations/{id}/connectors` → `McpPermissionPolicy.is_server_card_authorized` denies both card listing and the `call_tool` re-check, `permissions.py`, `call_tool.py:83-92`). Because the seed is NONE-auth, there is no `mcp_auth_required` first-use interrupt — first use goes straight to the per-call tool approval (§4). That is correct: SA auth is a process-local fact, not a per-user OAuth consent.

---

## 4. Write-classification — the consent-clarity label (exact lines, real tool names, durable fix)

### 4.1 The mechanism and the exact lines

The runtime interrupts on **every** `call_mcp_tool` — the per-call gate is graph-level HITL, registered on the same `HumanInTheLoopMiddleware` as workspace writes (`services/ai-backend/src/agent_runtime/execution/factory.py:443` comment, `:480` registers `McpValues.ToolName.CALL_MCP_TOOL`; the constant is `"call_mcp_tool"` at `capabilities/mcp/constants.py:233`). The worker then projects each interrupted call into an `approval_requested` event with `approval_kind="mcp_tool"` (`services/ai-backend/src/runtime_worker/stream_events.py:832`), and classifies **read-vs-write by tool NAME**:

```
services/ai-backend/src/runtime_worker/stream_events.py
807:   read_only = cls._connector_action_is_read_only(tool_name)
817:   risk_level = "low" if read_only else "medium"
...
909:   @classmethod
910:   def _connector_action_is_read_only(cls, tool_name: str) -> bool:
911:       """Return ``True`` when the tool name contains no write-operation terms."""
912:       normalized = tool_name.lower()
913:       if any(
914:           term in normalized
915:           for term in ("create", "post", "send", "update", "delete", "write")
916:       ):
917:           return False
918:       return True
```

The returned `read_only` flag drives the consent card's **category, reason code, reversibility, and risk**: `_approval_category(read_only)` → `ApprovalCategory.READ if read_only else ApprovalCategory.WRITE`; `_approval_reversible(read_only, …)`; `_approval_reason_code(read_only, risk_level)` (`stream_events.py` `_mcp_approval_structured` and the `_approval_*` helpers). So a misclassification means a **mutating tool renders a READ consent card, plausibly marked reversible** — deceptive consent, not a bypass, but the exact clarity failure the reviews flag.

The write-term set at `stream_events.py:913-916` is `("create", "post", "send", "update", "delete", "write")`. **`append` is not in it** — and neither are `add`, `copy`, `rename`, `share`, `clear`, `insert`, `set`, `remove`, `patch`, `put`.

### 4.2 Why the v1 "name the tools with write-terms" belt does NOT apply here

`PRD-P6b.md` §4.4 / §6 and `P6-ENG-plan.md` §4.3 propose renaming write tools to carry write-terms (`write_append_values`, etc.). **That belt is inapplicable under RESOLVED-C** because we adopt `xing5/mcp-google-sheets` **as-is, pinned** — we do not author or fork its tool names. Its real mutating tools and their classification under the current denylist:

| Tool (verbatim)                                            | Mutates?                    | Contains a write-term? | Current classification   |
| ---------------------------------------------------------- | --------------------------- | ---------------------- | ------------------------ |
| `update_cells`                                             | yes                         | `update`               | **write** ✓              |
| `batch_update_cells`                                       | yes                         | `update`               | **write** ✓              |
| `batch_update`                                             | yes                         | `update`               | **write** ✓              |
| `create_sheet`                                             | yes                         | `create`               | **write** ✓              |
| `create_spreadsheet`                                       | yes                         | `create`               | **write** ✓              |
| `add_rows`                                                 | yes                         | —                      | **read** ✗ misclassified |
| `add_columns`                                              | yes                         | —                      | **read** ✗ misclassified |
| `copy_sheet`                                               | yes                         | —                      | **read** ✗ misclassified |
| `rename_sheet`                                             | yes                         | —                      | **read** ✗ misclassified |
| `share_spreadsheet`                                        | yes (**permission change**) | —                      | **read** ✗ misclassified |
| `get_sheet_data` / `get_sheet_formulas` / `get_multiple_*` | no                          | `get`                  | read ✓                   |
| `list_*` / `search_spreadsheets` / `find_in_spreadsheet`   | no                          | —                      | read ✓ (correct)         |

Five mutating tools — including `share_spreadsheet`, which changes document **permissions** — would render a READ consent card. Renaming is off the table. Therefore the **durable classifier fix is mandatory**, not optional.

### 4.3 The durable fix — invert the denylist to a fail-safe allowlist (the review's [low], honored)

Per `P6-plan-review.md` [low]: this classifier is a **consent-CLARITY label, never the write gate** (the interrupt fires for _every_ `call_mcp_tool` regardless; the actual per-call gate is graph HITL). Keep that framing, and take the review's preferred no-bandaid direction — **invert `_connector_action_is_read_only` to an allowlist so unknown verbs fail safe to WRITE:**

```python
_READ_ONLY_TERMS = ("get", "list", "search", "find", "fetch", "view", "read", "summary")

@classmethod
def _connector_action_is_read_only(cls, tool_name: str) -> bool:
    normalized = tool_name.lower()
    return any(term in normalized for term in cls._READ_ONLY_TERMS)
```

This is strictly safer and, verified against every shipped profile, **regresses no existing read tool** (all contain a read-term: gmail `search_/get_/list_`, gdrive `search_/list_/get_/read_`, atlassian `get…/search…` — every name contains `get` or `search`; outlook `…getMessage/…listSent/…searchMessages`). It **corrects two classes of live misclassification in passing**: gdrive's `copy_file` (`desktop_profiles.yaml:114`, `product_scope: write` but classified read today — the review names it) and the five Sheets tools above all fall to **write**. Unknown future verbs (`patch`, `revoke`, `grant`, `move`) now fail safe to write instead of silently reading.

Also realign the sibling **display-label** helper `_connector_action_name` (`stream_events.py:894-907`, terms `search/filter/find/list`, `read/get/fetch`, `create/post/send/update/delete`) so `add_/copy_/rename_/share_/clear_` render as `modify`, not `action` — cosmetic, ship in the same change.

**Explicitly do not** gate any interrupt _decision_ on `read_only` — it is a label only (`P6-plan-review.md` [low] "Do not gate any interrupt decision on read_only"). The write **enforcement** is the per-call HITL that fires for every `call_mcp_tool`.

### 4.4 The `per_call` approval wiring — two paths, be precise

- **Default (local seed) path:** there is **no `desktop_profiles.yaml` entry** for `gsheets`, so there is no declared `approval: session|per_call` contract on this path. Enforcement is entirely **graph-level**: `factory.py` registers `call_mcp_tool` on the HITL middleware (`:480`) with `allowed_decisions` including `approve`/`reject`, so **every** Sheets call — read or write — interrupts and waits for the human. Writes render as `category=WRITE` (post §4.3 fix), reads as `category=READ`. This over-prompts on reads (safe; `PRD-P6b.md` §8 "don't claim reads never prompt") and is the honest v1 posture.
- **OAuth alternate path (§7):** the `desktop_profiles.yaml google-sheets` profile declares each **mutating tool** `product_scope: write` + `approval: per_call`, enforced at load by `ConnectorToolPolicy._mutating_tools_require_per_call_approval` (`profile_catalog.py:75-84`, which raises `ProfileCatalogError` if a write/draft tool is not `per_call`). Note the loader keys the per-call requirement off `product_scope`, **not** the tool name — so the profile correctly declares `add_rows` as `product_scope: write` + `per_call` regardless of its name. The **name-based classifier is orthogonal** and still needs §4.3 for the _consent-card category_ on the alternate path too.

---

## 5. Release stage, preview gate, and acceptance tests

### 5.1 release_stage: preview + the gate

`release_stage`/`requires_preview_gate` are **`desktop_profiles.yaml` concepts** (`ConnectorReleaseStage`, `profile_catalog.py:43-45`, gated by `DESKTOP_CONNECTORS_ALLOW_PREVIEW`, `app.py:1975-1976`). The default Sheets connector is a **seed, not an overlay profile**, so it has no `release_stage` field. Realize "preview" as an explicit **config+preview gate on the seed**:

- The `ensure_local_sheets_connector` boot seed (§3.1) and the desktop Sheets-MCP child (§1.3) come up **only when both**: `SHEETS_MCP_URL` (SA configured) is set **and** preview is enabled. Reuse the existing operator signal `DESKTOP_CONNECTORS_ALLOW_PREVIEW=true` (already read at `app.py:1975`) as the preview flag so there is one preview switch, not two. When preview is off or SA config is absent → no child, no seed → the FTUE row degrades (§2.3(1)).
- Promote out of preview (gate the seed on config alone) only once the pinned server is reviewed, the SA-sharing UX is validated live, and the acceptance suite (§5.2) is green — the same "verified endpoint" bar `P6-PRD-product.md` §7.3 sets, translated to the local server.

### 5.2 Acceptance tests

**Hermetic real-graph run→stream with a fake Sheets endpoint** (the verification keystone: deterministic fake model + real graph, `MEMORY` "Verification program" P0; `PRD-P6b.md` §6 live-stack list). Stand up a **fake loopback Sheets MCP** — a tiny in-test HTTP server exposing the real tool names (`get_sheet_data`, `update_cells`, `add_rows`, `share_spreadsheet`, …) with canned JSON-RPC results — and assert:

1. **Connect (no OAuth):** boot with `SHEETS_MCP_URL=<fake>` → `gsheets` seed registers → `GET /v1/connectors` lists it `status=connected`, `status_reason=None`, a `connector.installed`/`connector.connected` audit row, no token/`vault_ref` on the SSE wire. (Extends `test_mcp_write_through.py` NONE-auth cases.)
2. **NONE-auth egress (the §6-B1 enabler):** a run that calls `get_sheet_data` reaches the fake server through `proxy_internal_rpc` **without** an `Authorization` header and **without** requiring a stored token; the pre-fix behavior (`"MCP server is not authenticated"`) is asserted gone.
3. **Write path + per-call gate:** a run that calls `update_cells` emits **exactly one** `approval_requested`, `approval_kind="mcp_tool"`, `read_only=false`, `category=write`; on `approve` the tool executes, on `reject` it does not and no cell mutates.
4. **Classifier correctness (the durable fix):** table test over the adopted tool names — `add_rows`/`add_columns`/`copy_sheet`/`rename_sheet`/`share_spreadsheet`/`update_cells`/`batch_update`/`create_spreadsheet` → `read_only=false` (write card); `get_sheet_data`/`get_sheet_formulas`/`list_spreadsheets`/`search_spreadsheets`/`find_in_spreadsheet` → `read_only=true`; **regression**: every existing gmail/gdrive/atlassian/outlook read tool stays read; gdrive `copy_file` flips to write.
5. **Read path:** a `get_sheet_data` call surfaces as `category=read`/`read_only=true` (proving the split), still gated (over-prompt, per §4.4 honesty).
6. **Unshared-sheet degrade:** the fake server returns a Google-style `403` for an unshared spreadsheet → the tool result is a **sanitized** typed error carrying the "share with `<client_email>` (Editor)" copy, with no key material and no raw upstream body leaked (M6 discipline).
7. **Preview/config gate:** with `SHEETS_MCP_URL` unset → no seed, `GET /v1/connectors` has no `gsheets` connected row, and the marketing row resolves to needs-setup (not a dead affordance); with preview off → same.
8. **Tenant isolation + per-chat pause:** another tenant never sees the row/SSE (inherited RLS test shape); pausing `gsheets` for a chat denies both card listing and `call_mcp_tool` (`call_tool.py:83-92`).
9. **Supply-chain gate (§1.2):** the pin file resolves to one hashed version; no committed SA JSON, OAuth client secret, or `@latest` invocation anywhere in the bundle.

---

## 6. Concrete code-change list (default/SA path)

| #      | File                                                                                               | Change                                                                                                                                                                                                                                                                                               | Why                                                                                                                                                                  |
| ------ | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B1** | `services/backend/src/backend_app/service.py`                                                      | `proxy_internal_rpc` (`:621-635`): when `record.auth_mode == McpAuthMode.NONE`, skip `_require_valid_token` and call `_post_remote_mcp_rpc(record.url, payload, access_token=None)`. `_post_remote_mcp_rpc` (`:776-803`): make `access_token` optional; omit the `Authorization` header when `None`. | The single MCP egress currently assumes an OAuth bearer; a NONE-auth local server has none. Load-bearing enabler for the whole local-server default. Test = §5.2(2). |
| **B2** | `services/backend/src/backend_app/connectors/catalog.yaml`                                         | Add the `gsheets` marketing entry after `gdrive` (`:44-47`).                                                                                                                                                                                                                                         | Card for the slug on the connectors destination + FTUE popover; the write-through slug must have a marketing row to reconcile against.                               |
| **B3** | `services/backend/src/backend_app/app.py` (or a new `connectors/local_sheets.py`)                  | Add `ensure_local_sheets_connector(app)` invoked at startup; when `SHEETS_MCP_URL` set **and** preview enabled, idempotently ensure a `seed:gsheets` NONE-auth HTTP server (`url=$SHEETS_MCP_URL`) and drive it through `_connector_write_through` (`app.py:337-391`).                               | Conditional local seed; config-gated for free; reuses the shipped write-through. Tests §5.2(1),(7).                                                                  |
| **B4** | `services/ai-backend/src/runtime_worker/stream_events.py`                                          | Invert `_connector_action_is_read_only` (`:909-918`) to the fail-safe read allowlist; realign `_connector_action_name` (`:894-907`).                                                                                                                                                                 | Adopted tool names can't carry write-terms; denylist misclassifies 5 mutating tools incl. a permission change. Consent-clarity only; never the gate. Tests §5.2(4).  |
| **B5** | `apps/desktop/main/services/{runtime-paths,supervisor,desktop-supervisor,service-env}.ts`          | Add the `sheets-mcp` supervised child (its own non-uvicorn spawn shape), allocate its port, inject `SERVICE_ACCOUNT_PATH` into the child and `SHEETS_MCP_URL` + `SHEETS_MCP_SERVICE_ACCOUNT_EMAIL` into the backend child. Gate on SA config + preview.                                              | Runs the bundled server locally under the existing supervisor; single hand-off to the backend.                                                                       |
| **B6** | `tools/desktop-runtime/{manifest.json,stage.mjs}` + `tools/sheets-mcp/PIN` + `deploy/self-host/**` | Stage/pin the MIT `xing5/mcp-google-sheets` wheel for desktop; internal-network container for self-host; supply-chain CI gate.                                                                                                                                                                       | Bundling + version pin (§1.2).                                                                                                                                       |
| **B7** | `packages/api-types` (if any status_reason string is surfaced)                                     | Add the needs-setup / share-with-SA reason strings to the connector status-reason surface if the FE renders them typed.                                                                                                                                                                              | Honest degrade copy (§2.3). Only if not already free-form.                                                                                                           |

**No `oauth_coordinator._requested_permissions` / `desktop_routes` `Literal["read","draft","write"]` widening on the default path** — that widening belongs to the **OAuth alternate** (§7), where `write` product-scope is actually requested. On the SA default there is no OAuth product scope: the SA has full `spreadsheets` authority and the per-call approval is the gate.

---

## 7. OAuth alternate mode (retained from v1 — build ONLY if a deployment opts in)

For a branded Google consent screen instead of SA sharing, ship the v1 overlay unchanged from `P6-ENG-plan.md` §4 / `PRD-P6b.md` §3–4, with these anchors verified at HEAD:

- `desktop_profiles.yaml`: a `google-sheets` profile (after `google-drive`, `:71-120`) — HTTPS `endpoint_template`, `spreadsheets.readonly` (read) + `spreadsheets` (write) scopes, mutating tools `product_scope: write` + `approval: per_call` (loader-enforced, `profile_catalog.py:75-84`), `requires_pre_registered_client: true`, `release_stage: preview` + `requires_preview_gate: true` (`profile_catalog.py:123-132`), non-colliding `server_id: "desktop:google:sheets"` (`_assert_installable_server`, `:243-267`).
- `oauth_coordinator.py` `_requested_permissions` (`:298-309`) + `start()` (`:143-166`): add `elif requested_product_scope == "write": wanted.add("write")`; `desktop_routes.py:91` `Literal["read","draft","write"]`; `packages/api-types/src/connectors-desktop.ts` `DesktopRequestedProductScope`. **Order invariant (unchanged, still binding):** coordinator-before-route — never expose `"write"` at the route boundary before `_requested_permissions` maps it, else a "connect for write" silently returns read-only permissions (`P6-ENG-plan.md` §4.2).
- Pre-registered client absent → `connector_oauth_setup_required` → **409** (`oauth_coordinator.py:171-181`, `desktop_routes.py:59`) — the OAuth-mode needs-setup card.

The alternate uses the **same adopted tool names**, so the §4.3 classifier fix is required on this path too (for the consent-card category; the profile's `per_call` covers enforcement).

---

## 8. Anti-bandaid ledger

- Does **not** shoehorn a local server into the HTTPS/OAuth desktop overlay — it takes the correct NONE-auth seed path and fixes the one real egress gap (B1) rather than faking an HTTPS endpoint.
- Does **not** rename third-party tools to satisfy a denylist (impossible for an adopted-as-is server) — it makes the classifier **fail safe to write** (B4), which also fixes gdrive `copy_file` in passing.
- Does **not** store the Google SA credential in the connector record or `TokenVault`-as-oauth-client — the credential lives with the MCP process; the record is honestly NONE-auth.
- Does **not** claim reads never prompt — every `call_mcp_tool` interrupts; the classifier is a consent-clarity label, never the write gate.
- Does **not** present a dead FTUE button — absent SA config the row degrades to needs-setup; configured-but-unshared degrades to "share with `<SA email>` (Editor)".
- Does **not** delete the OAuth work — it demotes it to an operator-opt-in alternate.

## 9. Open items to confirm at build time

- Pin the exact `mcp-google-sheets` released version + hash; review the pinned source (RESOLVED-C caveat).
- Confirm the pinned server's HTTP-serve invocation flags (`--transport http --host --port` names) for the supervisor spawn shape (§1.3).
- Confirm the desktop config location for `sheets-service-account.json` and whether to route it through `boot-secrets.ts` secure storage vs a plain mounted file (§2.1).
- Self-host multi-tenant: decide per-tenant SA vs one shared SA (default: shared; document the coarse-isolation caveat, §1.3).
- Whether `SHEETS_MCP_SERVICE_ACCOUNT_EMAIL` should be a typed connector field surfaced to the FE (for the exact degrade copy) or derived FE-side from a `/v1/connectors` status_reason string (B7).
