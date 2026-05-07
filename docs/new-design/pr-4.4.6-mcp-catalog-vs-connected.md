# PR 4.4.6 — MCP Catalog vs Connected: split, install flow, OAuth-setup recovery

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4 follow-up to PR 4.4 (Settings → Connectors)
> **Owner:** backend (drop seed; add catalog endpoint + install endpoint; replace 8 seed tests with install tests) · backend-facade (zero — proxy) · api-types (catalog DTOs + InstallMcpServerRequest) · frontend (Settings page filter; new McpOverlay with tabs; ConnectorCard reads server brand fields; OAuth-setup error class) · design-system (zero — reuses existing primitives)
> **Size:** **L.** Architectural: catalog leaves the user's row table. ~500 LoC.
> **Depends on:**
>
> - ✅ PR 4.4 (Settings → Connectors detail surface)
> - ✅ PR 3.4.1 partial — `McpServerRecord` brand metadata columns are already in code (linter pre-staged), but the _seed loop_ that backfills them is wrong. This PR replaces that loop with an explicit install path.
>
> **Reads alongside:**
>
> - [`pr-3.4.1-connector-popover-fidelity.md`](pr-3.4.1-connector-popover-fidelity.md) — chat popover state vocabulary; this PR establishes parity for the workspace-level surface.
> - [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) — Settings → Connectors page, McpOverlay wizard.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md).

---

## 0 · TL;DR

The current Settings → Connectors page conflates two distinct concepts:

1. **Catalog** — the curated list of well-known MCP servers we ship in code (`mcp_catalog.py`). Static. Org-agnostic. Has brand metadata, default scopes, and a per-vendor OAuth posture.
2. **Connected** — what the user has actually installed and authorized in their workspace. Per-user DB rows. Has tokens, audit trail, run-time enable/disable.

Today the backend lazy-seeds the entire catalog into every user's `mcp_servers` table on first list call. The Settings page then renders all 13 unauthorized rows in "Connected" with an "OAuth required" hint. This is wrong on two axes: (a) **a user has not "connected" anything just because we created a row**, and (b) several catalog entries (Atlassian, GitHub, PayPal, Plaid, Square, Intercom) cannot be 1-click authorized — they need a pre-registered OAuth client. Toggling them on currently dumps the raw 4xx body into the card.

This PR splits the two. Catalog becomes a read-only list served by a new endpoint; Connected stays as the user's installed-server table. The install path becomes explicit: the user clicks Install in the Manage MCP servers modal, supplies pre-registered OAuth credentials when the vendor requires them, the backend creates the row and starts auth. Failed installs surface a "Setup required" inline form instead of a JSON dump.

| Surface                              | Today                                                                                                                         | After this PR                                                                                                                                                                                                                                 |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `McpRegistryService.list_servers`    | Auto-seeds 13 rows when the user has zero, every list.                                                                        | Returns only what the user has installed. No seeding.                                                                                                                                                                                         |
| `mcp_catalog.py`                     | Loaded by the seed loop and copied into rows.                                                                                 | Source of truth for `GET /v1/mcp/catalog`; copied into a row only on install.                                                                                                                                                                 |
| `GET /v1/mcp/catalog`                | Doesn't exist.                                                                                                                | New public endpoint. Returns `McpCatalogResponse` (list of `McpCatalogEntryResponse`).                                                                                                                                                        |
| `POST /v1/mcp/servers/install`       | Doesn't exist.                                                                                                                | New endpoint. Body: `{slug, oauth_client?}`. 422 when `requires_pre_registered_client` and `oauth_client` missing. Idempotent on slug.                                                                                                        |
| `CatalogEntry` (Python)              | `slug, display_name, url, transport, auth_mode, description, verified, logo_url, brand_color, scopes_summary, default_scopes` | + `requires_pre_registered_client: bool`                                                                                                                                                                                                      |
| Settings page "Connected" section    | Renders all 13 seeded entries as disabled + "OAuth required".                                                                 | Renders only `isAuthenticated(server)` rows. Empty state copy: "No connectors installed yet."                                                                                                                                                 |
| Manage MCP servers modal             | 5-step wizard (browse / auth / scope / confirm / connected) keyed by hardcoded frontend `CATALOG`.                            | Two tabs: **Catalog** (server-driven grid; Install button per card; setup-required form inline) and **Connected** (full management via `ConnectorRow`).                                                                                       |
| `seedCatalogMeta.ts` (frontend)      | Hardcoded brand colour + description map.                                                                                     | **Deleted.** Server provides on `McpServer` and `McpCatalogEntry`. Single source of truth.                                                                                                                                                    |
| OAuth-setup error from `/auth/start` | Raw `{"detail": "..."}` JSON dumped into the card.                                                                            | Caught and classified as `OAuthSetupRequiredError`. UI shows "Setup needed" CTA → opens credentials form. No JSON in the DOM.                                                                                                                 |
| 8 seed tests                         | Validate wrong behaviour (auto-seed).                                                                                         | **Deleted.** Replaced by `test_mcp_catalog_install.py` — 9 tests covering catalog endpoint shape, install creates a row, install is idempotent, install rejects missing pre-registered client, brand metadata is copied, install is per-user. |

LoC estimate: **backend ≈ 220** (contracts +90, service +80, app +50, drop ~50 from old seed paths) · **api-types ≈ 50** · **frontend ≈ 320** (`McpOverlay` rewrite, `ConnectorCard` migration to server-supplied brand fields, OAuth-error class, `useMcpCatalog` hook, settings page filter) · **CSS ≈ 60** (square glyphs, tighter density, accent wash). Net delete: `seedCatalogMeta.ts`.

The four runtime / streaming invariants are preserved:

1. **Frozen at run-start.** No change to `AgentRuntimeContext.connector_scopes`.
2. **Binary at runtime.** Disabled or unauthenticated → not in `runtime_connector_scopes()` → not exposed to the LLM. **Same filter today.**
3. **No new event type.** No streaming change. The `mcp_auth_required` mid-run flow is untouched.
4. **Single PATCH endpoint.** `PATCH /v1/agent/conversations/{id}/connectors` is unchanged; per-chat scope persistence is unaffected.

---

## 1 · PRD

### 1.1 Problem

Three shipped behaviours are wrong:

1. **Seed pollutes "Connected".** Every user's `mcp_servers` row count starts at 13, all `enabled=false`, all `auth_state=unauthenticated`, all `health=disabled`. The Settings page renders them in the Connected section because they're in `connectors.servers`. The user reads "I am connected to Asana" — but they aren't. A real connection requires that a user has explicitly opted in _and_ completed an OAuth flow. We currently meet neither bar before showing rows.
2. **OAuth-setup errors are unreadable.** Toggling Atlassian on triggers `POST /v1/mcp/servers/{id}/auth/start`. Six of our 13 catalog entries (Atlassian, GitHub, PayPal, Plaid, Square, Intercom) require a **pre-registered OAuth 2.0 client** because the vendor doesn't expose RFC 8414 metadata or RFC 7591 dynamic client registration. The endpoint returns a 4xx with `{"detail": "MCP OAuth setup requires authorization-server metadata, dynamic client registration, or a configured OAuth client for this server"}`. The frontend dumps that JSON verbatim into the connector card. Users have no path forward.
3. **No clean install path.** The 5-step `McpOverlay` wizard (PR 4.4) keys off a frontend `CATALOG` array hardcoded in `McpOverlay.tsx` that diverged from the backend `mcp_catalog.py`. The wizard's "Add to workspace" calls `addServer(url)` which always 200s for seeded URLs because the row already exists — so step 5 says "connected" before the user has done anything. The mental model is broken.

### 1.2 Goals

1. **Catalog is read-only and org-agnostic.** `GET /v1/mcp/catalog` returns a static list driven by `mcp_catalog.py`. No per-user state. No DB read.
2. **Connected requires authorization.** The Settings page Connected section shows only servers where `isAuthenticated(auth_state)` (currently: `authenticated`, `auth_skipped`, `auth_unsupported`). Unauthenticated rows live in the Manage MCP servers modal as "Resume install" until completed.
3. **Install is explicit.** `POST /v1/mcp/servers/install` with `{slug, oauth_client?}`. Backend resolves the slug against `mcp_catalog.py`, validates the pre-registered-client requirement, creates the row with brand metadata copied from the catalog entry, returns the record. Frontend then calls `authenticate(serverId)` which redirects to OAuth. Idempotent: re-installing returns the existing row unchanged.
4. **Setup-required errors recoverable inline.** Any 4xx from `/auth/start` whose `detail` matches the OAuth-setup pattern is classified as `OAuthSetupRequiredError`. The UI catches it on the card / install button and shows a "Setup required — add OAuth credentials" CTA that opens the credentials form pre-filled with the catalog URL.
5. **Single source of truth for brand metadata.** Backend `mcp_catalog.py` ships the canonical brand colour, logo URL, scopes summary, default scopes. The catalog endpoint exposes them. The install path copies them onto the new row. The Settings page renders them from the row. The frontend deletes its hardcoded `seedCatalogMeta.ts`.
6. **Per-vendor auth posture is wire-visible.** Each `McpCatalogEntryResponse` has `requires_pre_registered_client: bool`. Frontend uses it to decide whether the Install button is one-click or opens the credentials form first.
7. **Existing seeded rows degrade gracefully.** Users who already have 13 ghost seed rows from the old loop see them filtered out of Connected (they're not authenticated) and rendered in the modal Catalog tab as "Resume install". No data loss; no migration needed for postgres in this PR.

### 1.3 Non-goals

- **Per-tool scope toggles.** The catalog ships `default_scopes`. Per-tool granularity is PR 4.4-extension territory.
- **Auto-favicon discovery for custom MCP servers.** Per PRD 3.4.1 §1.3 — admins paste a URL, get the letter glyph fallback.
- **Vendor-managed pre-registered client distribution.** When `requires_pre_registered_client=true`, the user supplies their own credentials. Org-level pre-provisioning (so a workspace admin pastes credentials once and every user can 1-click install) is a follow-up.
- **Migrating existing seeded rows.** Postgres cleanup query for rows like `WHERE server_id LIKE 'seed:%' AND auth_state = 'unauthenticated' AND enabled = false` is out of scope. The frontend filter is sufficient for the user-visible behaviour; a cleanup migration can ship later. In-memory store resets on dev restart; no action needed.
- **Streaming event additions.** The catalog endpoint is REST-only. No SSE.
- **Skill catalog parity.** Skills have a different model (markdown-driven, scoped per user/org) and are out of scope.

### 1.4 Success criteria

- ✅ `GET /v1/mcp/catalog` returns 13 entries with `slug`, `display_name`, `url`, `transport`, `auth_mode`, `description`, `logo_url`, `brand_color`, `scopes_summary`, `default_scopes`, `requires_pre_registered_client`, `verified`. No per-user state in the response.
- ✅ `POST /v1/mcp/servers/install` with `{slug: "linear"}` creates a row with `server_id="seed:linear"`, copies all brand metadata from the catalog, and returns the record. `auth_state=unauthenticated`. `enabled=true` (so the user-meant install reaches runtime once auth completes).
- ✅ `POST /v1/mcp/servers/install` with `{slug: "atlassian"}` (no `oauth_client`) returns 422 with `detail` "Pre-registered OAuth client required for atlassian." No row is created.
- ✅ `POST /v1/mcp/servers/install` with `{slug: "atlassian", oauth_client: {client_id, client_secret, scope, authorization_endpoint, token_endpoint}}` creates the row and persists the OAuth client config (encrypted via TokenVault for the secret).
- ✅ `McpRegistryService.list_servers` does not call `_seed_catalog`. Fresh user → empty list.
- ✅ Re-installing the same slug returns the existing row (idempotent). The audit chain has one `mcp_server_installed` row, not two.
- ✅ Settings page Connected section: empty state shows "No connectors installed yet." with a primary CTA "Manage MCP servers" that opens the modal.
- ✅ Settings page Connected section: only renders `isAuthenticated(server.auth_state)` rows.
- ✅ Manage MCP servers modal Catalog tab: one card per catalog entry. Each card shows brand favicon (square 36px), name, `description`, `scopes_summary`, and a state-aware CTA — `Install`, `Resume install`, or `Installed` — derived from cross-referencing `connectors.servers` by `server_id == "seed:" + slug`. Vendors with `requires_pre_registered_client` show "Setup needed" hint in the card subtitle.
- ✅ Manage MCP servers modal Connected tab: one row per authenticated server, full management (Re-authenticate, Skip auth, Remove) via existing `ConnectorRow`.
- ✅ Click Install on Atlassian → modal expands an inline form for OAuth credentials → submit → install happens → OAuth redirect.
- ✅ The 4xx from `/auth/start` matching the OAuth-setup pattern is caught as `OAuthSetupRequiredError`. The card never shows raw JSON.
- ✅ `seedCatalogMeta.ts` is deleted; all metadata reads come from `server.logo_url` / `server.brand_color` / `server.scopes_summary` / `catalog.requires_pre_registered_client`.
- ✅ Streaming integration test: list of `runtime_connector_scopes()` for a fresh user is empty (no seeded rows leak through). Toggling on a freshly installed + authenticated Linear results in `connector_scopes["seed:linear"]` matching `default_scopes` from the catalog.
- ✅ `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- ✅ Backend MCP test suite passes: `tests/test_mcp_registry.py` (existing, untouched), `tests/test_mcp_api_flow.py` (existing, untouched), new `tests/test_mcp_catalog_install.py` (9 tests). Old `tests/test_mcp_catalog_seed.py` deleted.
- ✅ A11y: install buttons carry explicit verbs ("Install Linear"); setup-required form inputs have labels; modal tabs are keyboard-navigable (`role="tablist"` + arrow keys).

### 1.5 User stories

| #    | Persona                              | Story                                                                                                                                                                                                                                                                                         |
| ---- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah · fresh org admin              | I open Settings → Connectors. Connected is empty: "No connectors installed yet." I click "Manage MCP servers". The modal opens to the Catalog tab. I see 13 cards. Linear shows "Install" — tap, OAuth window opens, I auth, I land back on the page. Linear is now in the Connected section. |
| US-2 | Sarah · pre-registered vendor        | I tap Install on Atlassian. The card expands a small form: client_id, client_secret, scope, auth endpoint, token endpoint. I paste from my Atlassian developer console and submit. The row is created; OAuth opens; I return; Atlassian is in Connected.                                      |
| US-3 | Sarah · cancelled install            | I started Linear install, hit Cancel on the OAuth screen. The Settings page Connected section doesn't show Linear (not authenticated). The Catalog tab in the modal shows Linear with "Resume install" — tap, OAuth opens again.                                                              |
| US-4 | Marcus · workspace member            | I open Connectors. I see 3 active servers. I toggle Notion off — it stays in Connected, badge changes to "Paused (workspace)". The agent runtime stops loading Notion tools next run.                                                                                                         |
| US-5 | Sarah · expired token                | A connected server fails its next refresh. The card shows "Sign-in expired" badge with a "Re-authenticate" action. I click — OAuth happens — back to Active.                                                                                                                                  |
| US-6 | Workspace admin · custom MCP         | Catalog tab → "Add custom URL" card at the end. I paste `https://internal-tools.acme/mcp`, submit. Same install flow; row appears in Connected.                                                                                                                                               |
| US-7 | Compliance auditor                   | The audit log shows `mcp_server_installed` for every install (slug or custom URL), `mcp_auth_started`, `mcp_auth_completed`, `mcp_server_updated` for toggles, `mcp_server_deleted` for removals. The chain is unbroken.                                                                      |
| US-8 | Sarah · existing user with old seeds | After this PR ships, my workspace has 13 ghost seed rows from the old code. The Settings page Connected section is empty (filter dropped them). The Catalog tab shows them as "Resume install" or "Install" depending on `auth_state`. No data was lost; no behaviour I cared about changed.  |
| US-9 | Sarah · removes a connector          | In Manage MCP servers → Connected tab, I click ⋯ on Linear → Remove. Confirm. Linear leaves Connected; Catalog tab now shows it as "Install" again. The OAuth tokens are revoked server-side.                                                                                                 |

---

## 2 · Spec

### 2.1 Wire — backend contracts

**`McpServerRecord`** ([`contracts.py:257`](../../services/backend/src/backend_app/contracts.py#L257)) — pre-staged by linter, plus one new field:

```python
# existing 12 fields …
logo_url: str | None = None
brand_color: str | None = None
scopes_summary: str | None = None
default_scopes: tuple[str, ...] = ()
admin_managed: bool = False
description: str = ""           # NEW — copied from catalog on install
```

**`McpServerResponse`** mirrors the same field. **`from_record`** copies it through.

**`CatalogEntry`** ([`mcp_catalog.py:32`](../../services/backend/src/backend_app/mcp_catalog.py#L32)) — one new field:

```python
@dataclass(frozen=True)
class CatalogEntry:
    # existing …
    requires_pre_registered_client: bool = False  # NEW
```

Per-vendor truth (sourced from each vendor's MCP / OAuth docs as of 2026-05):

| slug                     | DCR (RFC 7591)  | Auth-server metadata (RFC 8414) | `requires_pre_registered_client` |
| ------------------------ | --------------- | ------------------------------- | -------------------------------- |
| asana                    | ✅              | ✅                              | False                            |
| atlassian                | ❌              | ❌ (3LO uses fixed endpoints)   | **True**                         |
| cloudflare-bindings      | ✅              | ✅                              | False                            |
| cloudflare-observability | ✅              | ✅                              | False                            |
| github                   | ❌ (GitHub App) | ❌                              | **True**                         |
| intercom                 | ❌              | partial                         | **True**                         |
| linear                   | ✅              | ✅                              | False                            |
| notion                   | ✅              | ✅                              | False                            |
| paypal                   | ❌              | ❌                              | **True**                         |
| plaid                    | ❌              | ❌                              | **True**                         |
| sentry                   | ✅              | ✅                              | False                            |
| square                   | ❌              | ❌                              | **True**                         |
| zapier                   | ✅              | ✅                              | False                            |

7 of 13 are 1-click; 6 need pre-registered credentials.

**`McpCatalogEntryResponse`** (NEW):

```python
class McpCatalogEntryResponse(BackendContract):
    slug: str
    display_name: str
    url: str
    transport: McpTransport
    auth_mode: McpAuthMode
    description: str = ""
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = ()
    requires_pre_registered_client: bool = False
    verified: bool = True

class McpCatalogResponse(BackendContract):
    entries: tuple[McpCatalogEntryResponse, ...]
```

**`InstallMcpServerRequest`** (NEW):

```python
class InstallMcpServerRequest(BackendContract):
    org_id: str
    user_id: str
    slug: str
    oauth_client: McpOAuthClientRequest | None = None

    @field_validator("org_id", "user_id")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        return Validators.normalize_id(value)

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: object) -> str:
        return Validators.normalize_skill_slug(value)
```

### 2.2 Wire — backend routes

| Method                                | Path                      | Scope       | Body / Query              | Response                | Notes                                                             |
| ------------------------------------- | ------------------------- | ----------- | ------------------------- | ----------------------- | ----------------------------------------------------------------- |
| GET                                   | `/v1/mcp/catalog`         | `MCP_READ`  | —                         | `McpCatalogResponse`    | Static; no per-user state. Cache safe.                            |
| POST                                  | `/v1/mcp/servers/install` | `MCP_WRITE` | `InstallMcpServerRequest` | `McpServerResponse`     | Idempotent on slug. 422 when pre-registered required and missing. |
| GET                                   | `/v1/mcp/servers`         | `MCP_READ`  | —                         | `McpServerListResponse` | **Same shape; no longer auto-seeds.**                             |
| POST                                  | `/v1/mcp/servers`         | `MCP_WRITE` | `CreateMcpServerRequest`  | `McpServerResponse`     | Unchanged. Used for **custom URL** only.                          |
| `/v1/mcp/servers/{id}/auth/*`         | `CONNECTORS_AUTH`         | (existing)  | (existing)                | (existing)              | Unchanged.                                                        |
| `/v1/mcp/servers/{id}` (PATCH/DELETE) | `MCP_WRITE`               | (existing)  | (existing)                | Unchanged.              |

Two endpoints, both additive. The existing `POST /v1/mcp/servers` stays for custom URLs.

### 2.3 Backend service — `McpRegistryService`

**Removed:**

- `_seed_catalog(org_id, user_id) -> bool` — no longer called.
- The `if not existing: self._seed_catalog(...)` branch in `list_servers`.
- `reset_catalog(...)` — deferred to a follow-up if anyone asks.

**Added:**

- `list_catalog() -> McpCatalogResponse` — pure projection of `DEFAULT_CATALOG` to `McpCatalogEntryResponse`.
- `install_from_catalog(request: InstallMcpServerRequest) -> McpServerResponse`:
  - Resolve `request.slug` against `DEFAULT_CATALOG`. 404 if missing.
  - If existing row exists for `(org, user, slug)`, return it (idempotent).
  - If `entry.requires_pre_registered_client` and `request.oauth_client is None` → raise `ValueError("Pre-registered OAuth client required for {slug}.")` (mapped to 422).
  - Create the record with stable id `seed:{slug}`, copy all brand metadata + `default_scopes`, set `auth_state=UNAUTHENTICATED`, `enabled=True`, `health=HEALTHY`.
  - Audit `mcp_server_installed`.

`list_servers` body becomes:

```python
def list_servers(self, *, org_id: str, user_id: str) -> McpServerListResponse:
    return McpServerListResponse(
        servers=tuple(
            self._response_from_record(record)
            for record in self.store.list_servers(org_id=org_id, user_id=user_id)
        )
    )
```

### 2.4 Frontend — `useMcpCatalog` hook

```ts
// apps/frontend/src/features/connectors/useMcpCatalog.ts
export interface CatalogState {
  entries: McpCatalogEntry[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useMcpCatalog(): CatalogState {
  /* GET /v1/mcp/catalog, cached on mount */
}
```

The catalog is small (~13 entries) and rarely changes. Fetch on mount of the modal; refresh button triggers re-fetch.

### 2.5 Frontend — `installFromCatalog` action on `useConnectors`

```ts
// extends ConnectorState
async installFromCatalog(slug: string, oauthClient?: McpOAuthClientConfigRequest): Promise<McpServer> {
  const server = await installMcpServer(slug, requireIdentity(identity), oauthClient);
  await refresh();
  return server;
}
```

Returns the freshly created (or pre-existing) `McpServer` so the caller can chain `authenticate(server.server_id)`.

### 2.6 Frontend — `OAuthSetupRequiredError`

```ts
// apps/frontend/src/api/mcpErrors.ts (NEW)
export class OAuthSetupRequiredError extends Error {
  readonly serverId: string;
  constructor(serverId: string, message: string) {
    super(message);
    this.serverId = serverId;
  }
}

const SETUP_PATTERN =
  /authorization-server metadata|dynamic client registration|configured OAuth client/i;

export function classifyMcpError(serverId: string, err: unknown): Error {
  if (err instanceof Error && SETUP_PATTERN.test(err.message)) {
    return new OAuthSetupRequiredError(serverId, err.message);
  }
  return err instanceof Error ? err : new Error("Connector action failed.");
}
```

`useConnectors.authenticate` and the install flow wrap thrown errors through `classifyMcpError`. UI catches `OAuthSetupRequiredError` specifically and offers the credentials form.

### 2.7 Frontend — `McpOverlay` rebuild

Replace the 5-step wizard. The modal is a tabbed shell:

```
┌─ Modal: Manage MCP servers ─────────────────────────┐
│ [Catalog] [Connected]                  [Refresh]    │
│ ─────────────────────────────────────────────────── │
│ (Catalog tab)                                       │
│  Search ___________                                 │
│  ┌─[Linear logo]─Linear────────────[Install]──┐     │
│  │ Issues, projects, and cycles.              │     │
│  │ Read issues, projects, cycles              │     │
│  └────────────────────────────────────────────┘     │
│  ┌─[A]─Atlassian (Jira + Confluence)──[Install]─┐   │
│  │ Jira issues, Confluence pages.               │   │
│  │ Setup needed — pre-registered OAuth client   │   │
│  └──────────────────────────────────────────────┘   │
│  …                                                  │
│  ┌─[+]─Add custom URL─────────────────[Add]─────┐   │
│  └────────────────────────────────────────────  ┘   │
└─────────────────────────────────────────────────────┘
```

**Card states (per catalog entry):**

- `Install` — no row exists for this slug
- `Resume install` — row exists, `auth_state=unauthenticated` or `auth_pending` or `auth_failed`
- `Installed` — row exists, `isAuthenticated(auth_state)` true. Card greyed; CTA disappears.

**Setup-required inline form** — when card is clicked and `requires_pre_registered_client=true`, the card expands to show the form. Submit triggers `installFromCatalog(slug, oauthClient)`. On success, the OAuth redirect kicks off automatically.

**Connected tab** — uses existing `ConnectorRow` (Re-auth, Skip auth, Remove with confirmations). One row per `isAuthenticated(server)`.

**Tabs primitive** — small custom component (`role="tablist"`, arrow-key navigation, `aria-controls` linkage). ~30 LoC. No new dep.

### 2.8 Frontend — Settings page filter

```tsx
// SettingsScreen.tsx ConnectorsSettings — line ~590
const groups = useMemo(() => {
  const connected: McpServer[] = [];
  const needsAttention: McpServer[] = [];
  for (const s of connectors.servers) {
    if (!isAuthenticated(s.auth_state)) continue; // filter: Connected = authenticated
    if (s.auth_state === "auth_failed") needsAttention.push(s);
    else connected.push(s);
  }
  return { connected, needsAttention };
}, [connectors.servers]);
```

Empty state copy:

```tsx
{
  connectors.servers.length === 0 ||
  (groups.connected.length === 0 && groups.needsAttention.length === 0) ? (
    <Card>
      <p>
        No connectors installed yet. Open <strong>Manage MCP servers</strong> to
        install one of the well-known servers, or add a custom URL.
      </p>
    </Card>
  ) : null;
}
```

### 2.9 Frontend — `ConnectorCard` migration

Read brand fields from `McpServer` directly:

```tsx
<AppIcon
  name={server.name}
  color={server.brand_color ?? undefined}
  logoUrl={server.logo_url ?? undefined}
  size="lg"
/>
…
<p className="connector-card__sub">{server.scopes_summary ?? statusHint(server)}</p>
```

`seedCatalogMeta.ts` is deleted in this PR.

`<AppIcon logoUrl>` lands as part of PR 3.4.1's design-system change. If 3.4.1 hasn't merged when this PR lands, we ship this PR with `logo_url` ignored on the icon (letter glyph only), and 3.4.1 turns on the favicon when it ships. Both PRs are commutative.

### 2.10 Atlas visual fidelity

| Element           | Rule                                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Brand glyph shape | **Square 36px** with 8px radius (override on `.ui-app-icon` _inside_ `.connector-card`). The connector popover row keeps the round glyph.   |
| Toggle text       | `Switch` component renders `<strong>{label}</strong>` for a11y; CSS `clip` it visually inside cards. Aria still announces "Linear, Active". |
| Active card       | `background: color-mix(in srgb, var(--color-accent) 10%, var(--color-bg-elevated))`; `border-color: var(--color-accent-strong)`.            |
| Card density      | `padding: 14px 18px` (was 16px 24px); gap between rows in grid `12px` (was 16px).                                                           |
| Card sub-text     | `server.scopes_summary` first; fall back to status hint ("Sign in to use", "Sign-in expired").                                              |
| Status badge      | Small pill in top-right of card for non-active states. Uses `Badge tone={authStateDisplay(server.auth_state).tone}`.                        |

### 2.11 OAuth-setup error UX

Two surfaces handle `OAuthSetupRequiredError`:

1. **Inside the McpOverlay Catalog tab** — when an Install attempt fails this way (rare; should be pre-empted by `requires_pre_registered_client` flag), the card auto-expands the credentials form. No raw text is shown.
2. **On a Connected card** — when toggling on or re-authenticating fails this way (e.g., admin rotated credentials and the stored client is now invalid), the card shows a "Setup needed" badge with a "Update OAuth credentials" link that opens the McpOverlay scrolled to the row's Connected entry.

The raw `detail` string is **never** rendered. The classified error's message is "Setup required — provide an OAuth client for this server." or similar, copy-curated.

### 2.12 Three-state runtime, four-state UI — same rules, settings vocabulary

Per PR 3.4.1 §2.7. The settings page surfaces:

| Reason connector is not loaded next run                                            | Settings card state                                               | User action           |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------- | --------------------- |
| `enabled=false` (workspace toggle off)                                             | "Paused" — toggle to resume                                       | tap toggle            |
| `auth_state=auth_failed`                                                           | "Sign-in expired" — red badge                                     | tap "Re-authenticate" |
| `auth_state=auth_pending`                                                          | "Connecting…" — neutral spinner                                   | wait or tap "Cancel"  |
| `auth_state in {authenticated, auth_skipped, auth_unsupported}` and `enabled=true` | "Active" — toggle on, accent wash                                 | (tap toggle to pause) |
| `!isAuthenticated(auth_state)`                                                     | **not in Connected** — appears in Catalog tab as "Resume install" | tap card              |

Identical state machine; identical rule "binary at runtime"; just framed for the workspace-level surface.

### 2.13 Streaming impact — explicit zero (re-stated)

| Subsystem                                              | Touched? |
| ------------------------------------------------------ | -------- |
| `runtime_events` schema                                | **No.**  |
| `RuntimeEventEnvelope`                                 | **No.**  |
| SSE handshake                                          | **No.**  |
| `runtime_worker`                                       | **No.**  |
| `chatModel/eventReducer.ts`                            | **No.**  |
| `mcp_auth_required` event                              | **No.**  |
| `AgentRuntimeContext.connector_scopes`                 | **No.**  |
| `runtime_connector_scopes()` projection                | **No.**  |
| `ToolPermissionChecker._is_connector_scope_authorized` | **No.**  |

This PR is REST + UI only.

### 2.14 Permissions

- `GET /v1/mcp/catalog` — `MCP_READ`. Public-ish but still gated like the rest of MCP read. No org/user scoping (response is org-agnostic).
- `POST /v1/mcp/servers/install` — `MCP_WRITE`. Uses `BackendServiceAuthenticator.scoped_identity` like every other write path. Caller-supplied `org_id`/`user_id` is overridden by verified identity.
- `OAuth client` payload — secret stored via `TokenVault` (existing); never returned in `McpServerResponse` (only `oauth_client_configured: bool`).

### 2.15 Error semantics

| Condition                                                     | Response                                                             | UX                                                                          |
| ------------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Install with unknown slug                                     | 404 + `detail: "Unknown catalog entry: {slug}"`                      | Modal toast; user clicks Refresh                                            |
| Install missing pre-registered client                         | 422 + `detail: "Pre-registered OAuth client required for {slug}."`   | Card auto-expands credentials form                                          |
| Install with malformed OAuth client (e.g., `client_id` empty) | 422 from existing validation                                         | Form shows field-level error                                                |
| Re-install of already-installed slug                          | 200 with existing record                                             | Modal shows "Already installed" toast; tab switches to Connected            |
| Custom URL install duplicates an existing custom URL          | 200 with existing record (matched by URL within `(org, user)` scope) | Same toast                                                                  |
| Catalog endpoint is unavailable                               | 5xx                                                                  | Modal shows "Catalog unavailable, try refresh"; doesn't block Connected tab |

### 2.16 Accessibility

- Tabs: `role="tablist"`, `role="tab"`, `aria-selected`, arrow keys to switch, `Tab` exits.
- Each catalog card: `role="article"`, the brand glyph carries `aria-label={display_name}`.
- Setup-required form opens within the same card, focus moves into the first input on expand, `Esc` collapses without submitting.
- Switch label visually-hidden but ARIA-announced ("Linear toggle, on, press space to pause").
- Buttons carry explicit verbs ("Install Linear", "Resume Atlassian install", "Remove Notion").
- `prefers-reduced-motion`: no slide animation on card expand or modal open.

### 2.17 What we explicitly do NOT add

- **No `simple-icons`, `react-icons`, Iconify, or any icon dep.** Brand glyphs ride the existing `<AppIcon>` letter fallback until 3.4.1's `logoUrl` variant lands.
- **No new design-system primitive.** Tabs are a small feature-local component; if used elsewhere later we promote.
- **No migration in this PR.** Cleanup of stale seed rows in postgres is a separate, simple migration.
- **No frontend metadata duplication.** `seedCatalogMeta.ts` is deleted; the only source is `mcp_catalog.py` via the catalog endpoint.
- **No vendor-managed pre-registered client distribution.** Org admin pastes credentials per install today.

---

## 3 · Architecture

### 3.1 Data flow

```
mcp_catalog.py (Python constants)
       │
       │ GET /v1/mcp/catalog (NEW)
       ▼
McpCatalogResponse  (org-agnostic, no DB read)
       │
       │ backend-facade proxy (no change)
       ▼
McpCatalogEntry[]  (api-types)
       │
       │ useMcpCatalog()  (NEW)
       ▼
Catalog tab grid in McpOverlay
       │
       │ user clicks Install on Linear
       ▼
POST /v1/mcp/servers/install   {slug:"linear"}
       │
       │ McpRegistryService.install_from_catalog
       │   - resolve slug → CatalogEntry
       │   - check requires_pre_registered_client (false for Linear)
       │   - copy brand metadata onto McpServerRecord
       │   - audit "mcp_server_installed"
       ▼
mcp_servers row (server_id="seed:linear", auth_state=unauthenticated, enabled=true)
       │
       │ frontend: connectors.authenticate(server_id)
       ▼
POST /v1/mcp/servers/seed:linear/auth/start   (existing endpoint)
       │
       │ OAuth redirect dance (existing)
       ▼
auth_state=authenticated
       │
       │ refresh()
       ▼
GET /v1/mcp/servers     (existing endpoint, no longer seeds)
       │
       ▼
Settings page Connected section: Linear card lights up
```

### 3.2 Sequence — Atlassian install with pre-registered client

```
Sarah                  Catalog tab           McpOverlay              backend                Atlassian OAuth
  │                       │                      │                       │                       │
  │ click Atlassian Install │                    │                       │                       │
  │ ────────────────────► │                      │                       │                       │
  │                       │ requires_pre_reg=true│                       │                       │
  │                       │ ────────────────────►│                       │                       │
  │                       │                      │ render credentials form (in-card)             │
  │ paste client_id, secret, scopes              │                       │                       │
  │ ────────────────────────────────────────────►│                       │                       │
  │                       │                      │ POST /v1/mcp/servers/install                  │
  │                       │                      │   {slug, oauth_client}│                       │
  │                       │                      │ ──────────────────────►                       │
  │                       │                      │                       │ install_from_catalog  │
  │                       │                      │                       │ resolve slug          │
  │                       │                      │                       │ create record         │
  │                       │                      │                       │ audit mcp_server_installed│
  │                       │                      │ ◄─────────────────────│ McpServerResponse     │
  │                       │                      │ connectors.authenticate(server_id)            │
  │                       │                      │ ──────────────────────►                       │
  │                       │                      │                       │ POST /auth/start      │
  │                       │                      │                       │ (oauth_client present)│
  │                       │                      │                       │ build auth_url with   │
  │                       │                      │                       │ pre-registered client │
  │                       │                      │ ◄─────────────────────│                       │
  │                       │                      │ window.location → auth_url                    │
  │                       │                      │ ────────────────────────────────────────────► │
  │                       │                      │                       │                       │ user grants
  │                       │                      │                       │                       │ scopes
  │                       │                      │                       │ ◄─────────────────────│ redirect /callback
  │                       │                      │                       │ exchange code → tokens│
  │                       │                      │                       │ store via TokenVault  │
  │                       │                      │                       │ auth_state=authenticated│
  │ Settings Connected: Atlassian │              │                       │                       │
  │ visible, "Active"     │                      │                       │                       │
```

### 3.3 The Catalog vs Connected distinction (visualized)

```
┌─────────── Catalog ───────────┐    ┌─────────── Connected ───────────┐
│                               │    │                                 │
│ Static. Code. Org-agnostic.   │    │ Dynamic. DB rows. Per-user.     │
│                               │    │                                 │
│ Source: mcp_catalog.py        │    │ Source: mcp_servers table       │
│ Endpoint: GET /v1/mcp/catalog │    │ Endpoint: GET /v1/mcp/servers   │
│                               │    │                                 │
│ Has: brand metadata,          │    │ Has: tokens, audit, enabled,    │
│      default scopes,          │    │      auth_state, scopes,        │
│      auth posture,            │    │      created_at / updated_at    │
│      vendor docs URL          │    │                                 │
│                               │    │                                 │
│ Cannot: have credentials,     │    │ Cannot: exist without explicit  │
│         have tokens, be       │    │         user install action     │
│         "active"              │    │                                 │
│                               │    │                                 │
└───────────────┬───────────────┘    └────────────────┬────────────────┘
                │                                     │
                │  POST /v1/mcp/servers/install       │
                │  ─────────────────────────────────►│
                │  copy brand metadata onto row       │
                │  copy default_scopes                │
                │  audit mcp_server_installed         │
                │                                     │
                └─────────────────────────────────────┘
```

### 3.4 DRY — what's reused vs. what's added

| Concern         | Reuse                                                                     | Add                                                                             |
| --------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Persistence     | `mcp_servers` table (existing 5 brand columns from PR 3.4.1 staging)      | `description` column (default `''`)                                             |
| Audit chain     | `_audit(record, action)` helper                                           | new actions: `mcp_server_installed`, `mcp_catalog_listed` (the latter optional) |
| Auth flow       | `start_auth` / `complete_auth` (existing)                                 | —                                                                               |
| Token vault     | `TokenVault.encrypt` (existing)                                           | —                                                                               |
| Frontend hook   | `useConnectors` (existing)                                                | new: `useMcpCatalog`; new action `installFromCatalog`                           |
| API client      | `httpGet` / `httpPost` (existing)                                         | new: `listMcpCatalog`, `installMcpServer`                                       |
| Card primitives | `Card`, `Switch`, `Badge`, `AppIcon`, `Modal` (existing)                  | feature-local `Tabs` (~30 LoC; promote later if used elsewhere)                 |
| OAuth posture   | `mcp_oauth.py` discovery + DCR (existing)                                 | —                                                                               |
| Brand glyphs    | Existing `<AppIcon name>` letter chain + (future) `logoUrl` from PR 3.4.1 | —                                                                               |

Net new code: **backend ≈ 220 · api-types ≈ 50 · frontend ≈ 320 · CSS ≈ 60**. Net delete: `seedCatalogMeta.ts` (~100 LoC), `_seed_catalog` + tests (~200 LoC). Real footprint is roughly 350 LoC growth.

### 3.5 Edge cases

| Case                                                                 | Behaviour                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| User has 13 stale seed rows from old code                            | Settings filters them out; modal Catalog tab shows them as "Resume install". Re-clicking calls existing `authenticate(server_id)`. No data loss.                                                                                                       |
| User installs a slug, OAuth fails, re-installs                       | Idempotent. Existing row returned. `authenticate` retries.                                                                                                                                                                                             |
| User installs a slug, then deletes the row, then installs again      | Re-install creates a fresh row. Stable id `seed:{slug}` is recreated; audit chain has install→delete→install.                                                                                                                                          |
| Two users in same org install Linear                                 | Two rows: `(org, user_a, "seed:linear")`, `(org, user_b, "seed:linear")`. Per-user.                                                                                                                                                                    |
| User pastes a custom URL that happens to match a catalog entry's URL | Goes through `POST /v1/mcp/servers` (not `/install`); creates a row with a `uuid4` server_id (no `seed:` prefix). Catalog tab won't recognize it as Installed; Settings shows it normally. Acceptable for v1 — admins paste exact catalog URLs rarely. |
| Catalog endpoint stale-cached on the frontend                        | Cached in-memory in `useMcpCatalog` for the modal lifetime. Refresh button re-fetches.                                                                                                                                                                 |
| Vendor's OAuth posture changes (e.g., Atlassian adds DCR)            | Update `requires_pre_registered_client` in `mcp_catalog.py`; backend redeploy ships the change. No client redeploy needed (catalog is fetched).                                                                                                        |
| User has admin role but no pre-registered credentials at hand        | Card opens credentials form; user can defer (close modal); next time they install, the form reopens. No partial state in DB.                                                                                                                           |
| Multi-tab install — same user installs Linear in two tabs            | Both tabs POST install; second is idempotent. Both observe `auth_state=unauthenticated`. The faster tab redirects first; the other tab's hook sees `auth_state=authenticated` after refresh.                                                           |
| OAuth-setup error from `auth/start` for an already-installed row     | UI surfaces "Update OAuth credentials" CTA. The form submits a `PATCH /v1/mcp/servers/{id}` with new `oauth_client`, then retries `authenticate`.                                                                                                      |

### 3.6 Test plan

**Backend** (`services/backend`)

- `tests/test_mcp_catalog_install.py` (NEW; replaces `test_mcp_catalog_seed.py`):
  - `test_catalog_endpoint_returns_curated_entries` — count = 13; each has slug, url, brand_color, default_scopes; `requires_pre_registered_client` reflects per-vendor truth.
  - `test_install_creates_row_with_brand_metadata` — install Linear; verify all brand fields copied; `auth_state=unauthenticated`; `enabled=true`.
  - `test_install_is_idempotent_on_slug` — install Linear twice; one `mcp_server_installed` audit row; second call returns same `server_id`.
  - `test_install_requires_pre_registered_client_when_flagged` — install Atlassian without `oauth_client` → ValueError → 422.
  - `test_install_with_pre_registered_client_succeeds` — install Atlassian with `oauth_client`; row persists `oauth_client_configured=true`; secret never round-trips in response.
  - `test_install_unknown_slug_raises_404` — install `not-a-real-slug` → ValueError → 404.
  - `test_install_per_user_scoping` — install Linear for user_a; user_b's list is empty.
  - `test_list_servers_no_longer_seeds` — fresh user `list_servers` returns `()`.
  - `test_install_audits_with_correct_action_string` — `mcp_server_installed` appears in audit chain with stable signature.
- `tests/test_mcp_registry.py` — untouched.
- `tests/test_mcp_api_flow.py` — untouched (uses custom URLs, not catalog).
- `tests/test_tenant_isolation_skills_mcp.py` — untouched.

**Backend cross-service smoke** (`make test`)

- Start fresh user; `GET /v1/mcp/servers` returns empty. `GET /v1/mcp/catalog` returns 13. `POST /install slug=linear` returns row. `GET /servers` now returns 1. Run agent; `runtime_connector_scopes()` excludes Linear (unauthenticated). Complete OAuth (test fixture). `runtime_connector_scopes()` includes Linear.

**Frontend** (`apps/frontend`)

- `useMcpCatalog.test.ts` — fetches on mount; refresh re-fetches; error state surfaces.
- `installFromCatalog.test.ts` — happy path + 422 raises `OAuthSetupRequiredError`-classified error; idempotency via re-install.
- `McpOverlay.test.tsx` — Tab switching; Catalog grid renders correct CTA per row state (Install / Resume install / Installed); setup-required form expands on click for `requires_pre_registered_client`; Connected tab renders `ConnectorRow` per authenticated server.
- `ConnectorCard.test.tsx` — reads brand fields from `server.*`; falls through to letter glyph when missing; `seedCatalogMeta` import is gone (compile error if reintroduced).
- `SettingsScreen.test.tsx` — Connected section count equals authenticated server count; empty state CTA opens modal.

**Manual QA checklist**

- Fresh org → install Linear → OAuth → see in Connected.
- Install Atlassian → setup form → OAuth → see in Connected.
- Cancel OAuth mid-flow → row exists unauthenticated → Catalog tab shows "Resume install".
- Toggle off in Connected → still in Connected, "Paused" badge.
- Remove from Connected tab → Catalog shows "Install" again.
- 13 stale seed rows from old code (run pre-PR; upgrade) → Connected empty; Catalog tab fully populated as "Resume install".

### 3.7 Rollout

- **Single PR.** No flag. Backend + frontend land together because the frontend's empty-state copy depends on the backend not seeding.
- **Backout.** Revert PR. Re-introducing seeding is a one-line change. The new endpoints become 404; the frontend's `useMcpCatalog` surfaces the error and the modal Catalog tab shows "Catalog unavailable" — Connected continues to function via the pre-existing endpoints.
- **Stale seed rows.** Left in place by this PR. A follow-up cleanup migration deletes `WHERE server_id LIKE 'seed:%' AND auth_state = 'unauthenticated' AND enabled = false`. Owners: backend.
- **Compat.** Old clients still talking to new backend: `GET /v1/mcp/servers` shape unchanged; old client doesn't see `description` field but Pydantic ignores unknowns. `POST /v1/mcp/servers/install` is new; old client never calls it. Old backend talking to new client: `GET /v1/mcp/catalog` is 404; modal shows "Catalog unavailable"; user uses custom URL form.
- **CI/CD.** Path filters cover backend service + frontend app + api-types package. No deploy of `ai-backend`, `backend-facade` (proxy unchanged).

### 3.8 Open questions

1. **Should `mcp_server_installed` audit row include the catalog `slug` and `requires_pre_registered_client` snapshot for compliance?** Probably yes — it's relevant for "who installed what, at what posture" investigations. Tracked as a small follow-up; not blocking this PR.
2. **Org-level pre-registered credentials store.** Some teams want admins to paste OAuth credentials _once_ and have all org members 1-click install. That's a new entity (`mcp_org_oauth_clients` table or `org_settings.mcp_clients`). Worth doing; out of scope here.
3. **Should the catalog `verified` flag block install for unverified vendors?** Today verified is purely informational. Out of scope; tracked for security review.
4. **Skip auth for catalog vendors.** Should `requires_pre_registered_client=true` vendors also disable Skip auth (since calling without auth is guaranteed to fail)? Recommend: yes, gate Skip auth in the UI when the catalog entry says auth is required. Trivial flag — included in this PR (~10 LoC).

---

## 4 · Acceptance checklist

- [ ] `services/backend/src/backend_app/contracts.py` — `description` added to `McpServerRecord`/`McpServerResponse`/`from_record`; `McpCatalogEntryResponse`, `McpCatalogResponse`, `InstallMcpServerRequest` shipped.
- [ ] `services/backend/src/backend_app/mcp_catalog.py` — `requires_pre_registered_client` flag on `CatalogEntry`; per-vendor values match §2.1 table.
- [ ] `services/backend/src/backend_app/service.py` — `_seed_catalog` removed from `list_servers`; `list_catalog()` and `install_from_catalog()` shipped; idempotency on slug; 422 on missing pre-registered client.
- [ ] `services/backend/src/backend_app/app.py` — `GET /v1/mcp/catalog` and `POST /v1/mcp/servers/install` routes wired with correct scopes.
- [ ] `services/backend/tests/test_mcp_catalog_seed.py` deleted.
- [ ] `services/backend/tests/test_mcp_catalog_install.py` ships with 9 tests; all green.
- [ ] `packages/api-types/src/index.ts` — `description?` on `McpServer`; `McpCatalogEntry`, `McpCatalogResponse`, `InstallMcpServerRequest` types.
- [ ] `apps/frontend/src/api/mcpApi.ts` — `listMcpCatalog`, `installMcpServer` clients.
- [ ] `apps/frontend/src/api/mcpErrors.ts` — `OAuthSetupRequiredError`, `classifyMcpError`.
- [ ] `apps/frontend/src/features/connectors/useMcpCatalog.ts` — hook ships.
- [ ] `apps/frontend/src/features/connectors/useConnectors.ts` — `installFromCatalog` action added.
- [ ] `apps/frontend/src/features/connectors/seedCatalogMeta.ts` deleted.
- [ ] `apps/frontend/src/features/connectors/ConnectorCard.tsx` — reads brand fields from `server.*`; sub-text from `scopes_summary`; square glyph styling.
- [ ] `apps/frontend/src/features/connectors/mcp/McpOverlay.tsx` — rebuilt with Tabs (Catalog/Connected), Install/Resume install/Installed states, inline setup-required form, custom URL card, search.
- [ ] `apps/frontend/src/features/settings/SettingsScreen.tsx` — Connected section filtered on `isAuthenticated`; empty state CTA opens modal.
- [ ] `apps/frontend/src/styles.css` — square brand glyph rules, 10% accent wash on active card, tighter density.
- [ ] No new design-system primitive.
- [ ] No new npm dependency.
- [ ] No streaming change. `runtime_events`, `RuntimeEventEnvelope`, SSE handshake, `mcp_auth_required` all byte-identical.
- [ ] `make test` green; `services/backend` pytest green; `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.

---

## 5 · References

- [`apps/frontend/src/features/connectors/mcp/McpOverlay.tsx`](../../apps/frontend/src/features/connectors/mcp/McpOverlay.tsx) — current 5-step wizard; rebuilt by this PR.
- [`apps/frontend/src/features/connectors/useConnectors.ts`](../../apps/frontend/src/features/connectors/useConnectors.ts) — extended with `installFromCatalog`.
- [`apps/frontend/src/features/connectors/ConnectorCard.tsx`](../../apps/frontend/src/features/connectors/ConnectorCard.tsx) — migrated off `seedCatalogMeta`.
- [`apps/frontend/src/features/settings/SettingsScreen.tsx`](../../apps/frontend/src/features/settings/SettingsScreen.tsx) — Connected filter + empty CTA.
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — `McpServer` `description`; new catalog/install DTOs.
- [`services/backend/src/backend_app/contracts.py`](../../services/backend/src/backend_app/contracts.py) — record/response brand fields; catalog/install DTOs.
- [`services/backend/src/backend_app/mcp_catalog.py`](../../services/backend/src/backend_app/mcp_catalog.py) — `requires_pre_registered_client` flag.
- [`services/backend/src/backend_app/service.py`](../../services/backend/src/backend_app/service.py) — `list_catalog`, `install_from_catalog`; `_seed_catalog` removed.
- [`services/backend/src/backend_app/app.py`](../../services/backend/src/backend_app/app.py) — two new routes.
- [`pr-3.4.1-connector-popover-fidelity.md`](pr-3.4.1-connector-popover-fidelity.md) — chat popover state vocabulary; this PR establishes parity for settings.
- [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) — predecessor PR (Settings → Connectors detail).
- Vendor MCP / OAuth docs (auth posture as of 2026-05):
  - [Asana MCP](https://developers.asana.com/docs/using-asanas-mcp-server)
  - [Atlassian Rovo MCP](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/)
  - [Linear MCP](https://linear.app/docs/mcp)
  - [Notion MCP](https://developers.notion.com/docs/mcp)
  - [Sentry MCP](https://docs.sentry.io/product/sentry-mcp/)
  - [Zapier MCP](https://help.zapier.com/hc/en-us/articles/36265392843917-Use-Zapier-MCP-with-your-client)
- Anthropic remote MCP servers: [docs.claude.com](https://platform.claude.com/docs/en/agents-and-tools/remote-mcp-servers), [pre-built connectors](https://support.claude.com/en/articles/11176164-pre-built-integrations-using-remote-mcp).
