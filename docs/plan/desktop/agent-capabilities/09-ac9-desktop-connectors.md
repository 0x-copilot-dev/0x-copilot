# AC9 — Desktop MCP connectors

> OAuth here is the **per-MCP-server auth layer**, not a parallel credential
> path: every desktop connector is a Model Context Protocol server, and its
> OAuth flow runs through the same backend registration + `TokenVault` authority
> the existing MCP connectors use. There is no second token system.

| Field             | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC9                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Status            | Draft; decision-complete and awaiting architecture review                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Wave              | 2 — Product wiring                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Estimated effort  | L — 12–16 engineer-days including catalog reconciliation, desktop OAuth UX, and vendor contract tests                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Dependencies      | AC1 desktop capability foundation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Required for      | AC8 connector-first routing, AC10 staged rollout                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Primary owner     | `services/backend` MCP/OAuth and connector catalog                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Supporting owners | Backend facade, Electron main authentication UX, AI-backend MCP client                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Web impact        | None — **but only because desktop OAuth transport is a desktop-only variant.** The desktop-only fields (`oauth_session_id`, the `callback` union, `requested_product_scope`, optional `code`) live in a new desktop-only `packages/api-types` module and must **not** modify the existing `StartConnectorOAuthResponse`/`ConnectorOAuthCallbackRequest` shapes the shipped web redirect flow consumes (`apps/frontend` `ConnectorsRoute.tsx`, `connectorsApi.ts`). Shared catalog fields are additive and optional. See "Web compatibility and the shared-type split". |

## Problem and why now

The product already has most of the correct connector architecture:

- `backend` owns MCP registrations, OAuth discovery/PKCE, token exchange, encrypted token storage, refresh, and the remote JSON-RPC proxy;
- `backend-facade` is the product-facing API;
- `ai-backend` discovers cards and invokes MCP through backend internal routes;
- Electron main already implements system-browser login, random-port loopback callbacks, deep links, and `safeStorage`;
- the connector destination is a read model over MCP registration rather than a second token system.

The desktop path is not complete. The public connector OAuth route still depends on boot-time stubs, the desktop has no generic MCP OAuth coordinator, and the two current catalogs disagree: `connectors/catalog.yaml` markets Gmail, Google Drive, and Outlook, while `mcp_catalog.py` has none of those and instead contains an existing Atlassian seed. A card can therefore appear without an installable MCP server, or an MCP seed can exist without a destination card.

AC9 wires the existing backend authority end to end and adds a desktop-profile catalog for:

- Google Workspace: Gmail and Drive, with Sheets/Slides only to the extent the official Drive MCP tools support them;
- Microsoft 365: Outlook through the Work IQ Mail MCP server;
- Atlassian: the existing Rovo MCP seed, initially exposing Jira read workflows.

It does not add provider SDK clients or copy OAuth tokens into Electron or the AI worker.

## Goals

- Make backend-owned connector registration/OAuth/token-vault/RPC-proxy behavior work end to end in packaged desktop mode.
- Generalize the existing Electron system-browser plus loopback/deep-link coordination without generalizing `openExternal` to the renderer.
- Add one validated backend-owned desktop profile catalog that reconciles marketing slugs with MCP server records.
- Preserve the existing Atlassian stable seed id; never create a second Jira registration.
- Add verified endpoints, provider setup, release-stage, permission, tool-policy, and support-status metadata for Google, Microsoft, and Atlassian.
- Request the least provider permission available and expose an even smaller product tool allowlist.
- Represent preview, tenant-disabled, admin-setup-required, scope-limited, tool-drift, region, and unsupported states explicitly.
- Keep provider access/refresh tokens and configured client secrets only in backend `TokenVault`.
- Keep renderer API traffic on `backend-facade` and agent traffic on backend internal MCP cards/client-session/RPC routes.
- Apply existing runtime permission, approval, budget, citation, event, payload, and audit middleware to all agent calls.
- Fail closed when official endpoint, OAuth metadata, issuer, scopes, or tool descriptors drift outside the pinned profile.

## Non-goals

- Provider-specific Gmail, Drive, Graph, Outlook, Jira, or Confluence SDK clients in Electron, facade, or AI backend.
- Storing connector tokens in Electron `safeStorage`; that store remains for the app’s own signed-in session and install secrets.
- Letting the renderer call `backend`, `ai-backend`, or a vendor endpoint directly.
- Letting `ai-backend` decrypt or receive a connector token.
- Treating marketing slugs such as `gmail` as proof that an MCP connector is installable.
- Shipping a shared Google, Microsoft, or Atlassian OAuth client secret in the desktop binary.
- Copy/paste OAuth codes, manual bearer/API-token entry, device-wide browser cookies, or headless API tokens.
- Claiming full Google Sheets/Slides APIs when only Drive file-level tools are present.
- Enabling Microsoft preview or Google Developer Preview connectors in production by default.
- Auto-exposing newly added vendor MCP tools.
- Changing connector availability or OAuth behavior in non-desktop deployment profiles.

## Provider decisions

### Google Workspace

Google’s official Workspace MCP servers are a **Developer Preview** and use separate endpoints per product.

#### Gmail

- Stable desktop slug: `gmail`
- Server id: `desktop:google:gmail`
- Endpoint: `https://gmailmcp.googleapis.com/mcp/v1`
- Transport: Streamable HTTP
- Provider auth: OAuth 2.0 with PKCE and a deployment/tenant-configured Google OAuth client
- Default requested scope: `https://www.googleapis.com/auth/gmail.readonly`
- Optional draft scope: `https://www.googleapis.com/auth/gmail.compose`
- Initial read tool allowlist:
  - `search_threads`
  - `get_thread`
  - `list_drafts`
  - `list_labels`
- Optional draft tool: `create_draft`, enabled only after reauthorization with `gmail.compose` and runtime approval.
- Label mutation tools are hidden in AC9 because the official setup scope set does not grant Gmail modify authority.
- Sending mail is not in the official Gmail MCP toolset documented for this preview; AC9 must not advertise it.

#### Google Drive

- Stable desktop slug: `gdrive`
- Server id: `desktop:google:drive`
- Endpoint: `https://drivemcp.googleapis.com/mcp/v1`
- Transport: Streamable HTTP
- Default requested scope: `https://www.googleapis.com/auth/drive.readonly`
- Optional create/upload scope: `https://www.googleapis.com/auth/drive.file`
- Initial read tool allowlist:
  - `search_files`
  - `list_recent_files`
  - `get_file_metadata`
  - `get_file_permissions`
  - `read_file_content`
  - `download_file_content`
- Optional write tools: `create_file` and `copy_file`, enabled only with `drive.file` and runtime approval.

`read_file_content` currently documents text representations for Google Docs, Google Sheets, Google Slides, PDFs, Office/OpenDocument files, and selected images. Therefore the desktop UI says **Drive files, including readable Sheets and Slides**. It does not claim cell/formula editing, chart operations, presentation-slide editing, or format-stable export. `create_file` can create an empty Google spreadsheet/presentation or convert uploaded content, but it is not a dedicated Sheets/Slides editing API.

Google profile entries show `preview` unless:

- the deployment explicitly enables preview connectors;
- the Google Cloud project has the relevant base API and MCP API enabled;
- an allowed OAuth client is configured;
- the user/tenant is enrolled in the Workspace Developer Preview;
- discovery and `tools/list` match the pinned profile.

Otherwise the entry remains visible with a specific unavailable reason and setup documentation; it does not attempt browser automation as an automatic fallback.

### Microsoft 365 / Outlook

- Stable desktop slug: `outlook`
- Server id: `desktop:microsoft:work-iq-mail`
- Tenant endpoint template: `https://agent365.svc.cloud.microsoft/agents/tenants/{tenantId}/servers/mcp_MailTools`
- Transport: Streamable HTTP
- Release stage: Microsoft preview
- Provider auth: Microsoft Entra delegated OAuth through metadata discovery and a tenant-configured public client id
- Required tenant/admin setup:
  - Agent 365/Work IQ enabled for the organization;
  - the tenant id configured by an administrator;
  - delegated `WorkIQ-MailServer` permission granted on the Agent 365 application;
  - admin consent and Work IQ policy allowing the Mail server.

`WorkIQ-MailServer` is a Microsoft application permission name, not a string that AC9 fabricates into a Microsoft Graph scope. The backend uses the protected-resource/authorization-server metadata for the exact OAuth request and records the returned grant. It does not request raw Graph `Mail.Read`/`Mail.Send`, nor does it reuse the separate generic Work IQ `WorkIQAgent.Ask` permission.

Initial read tool allowlist:

- `mcp_MailTools_graph_mail_getMessage`
- `mcp_MailTools_graph_mail_listSent`
- `mcp_MailTools_graph_mail_searchMessages`

Optional draft tool:

- `mcp_MailTools_graph_mail_createMessage`

Reply, reply-all, send, update, and delete are hidden in AC9 even if the provider advertises them. Microsoft explicitly describes the server and schemas as preview and subject to change, so an unknown/missing descriptor changes status to `tool_contract_mismatch` rather than being exposed.

The profile is unavailable by default in production. It requires a trusted `DESKTOP_CONNECTORS_ALLOW_PREVIEW=true` deployment setting plus tenant setup. There is no raw Graph fallback in AC9.

### Atlassian / Jira

- Stable desktop slug: `atlassian`
- Existing server id: `seed:atlassian`
- Endpoint retained from the current seed and current official getting-started guide: `https://mcp.atlassian.com/v1/mcp/authv2`
- Transport: Streamable HTTP
- Provider auth: interactive OAuth 2.1; deployment-configured client when discovery does not support registration
- Site authority: the exact Atlassian `cloudId` selected during consent
- Initial Jira read tool allowlist:
  - `getJiraIssue`
  - `searchJiraIssuesUsingJql`
  - `getTransitionsForJiraIssue`
  - `getIssueLinkTypes`
  - `getIssueWorklog`
  - `getJiraIssueRemoteIssueLinks`

Jira writes, Confluence writes, API-token-only Jira Service Management/Bitbucket tools, attachment commands, and unknown tools are hidden. OAuth 2.1 and provider permission groups are the authority; the current catalog’s `default_scopes=("read",)` is a **product tool policy label**, not an Atlassian OAuth scope. AC9 does not hard-code Jira REST 3LO scopes into Rovo MCP authorization.

The existing deterministic seed is migrated in place. Endpoint/auth metadata is updated only after verification; user removal and connection state are preserved.

## User experience and failure behavior

### Catalog

- The Connectors destination groups Gmail/Drive under **Google Workspace**, Outlook under **Microsoft 365**, and Atlassian under **Atlassian/Jira**.
- Each row shows `Available`, `Preview`, `Admin setup required`, `Tenant disabled`, `Unsupported`, `Reconnect`, or `Connected`.
- The detail view shows exact products/capabilities, release stage, requested provider permissions, product tool policy, admin prerequisites, and unsupported operations.
- A preview badge links to the vendor’s official terms/status. Preview entries never masquerade as generally available.
- Unsupported Sheets/Slides operations are named. The UI does not imply full support from a Drive icon.

### Connect

1. The renderer lists catalog/status through `backend-facade`.
2. **Connect** calls one allowlisted desktop preload method with only the stable slug. This is necessary because Electron main, not the renderer, owns loopback binding and the system browser.
3. Electron main binds `127.0.0.1` on a random port and calls the facade start route using its existing product bearer.
4. `backend` validates identity, catalog profile, provider setup, callback mode, scopes, and tool policy; creates one-time state and PKCE verifier; and returns an authorization URL.
5. Main arms the loopback state and opens the URL in the system browser.
6. Main receives only `code`, `state`, and provider error metadata and POSTs them to the facade callback.
7. `backend` consumes state once, exchanges the code, encrypts tokens in `TokenVault`, probes `initialize`/`tools/list`, applies the profile allowlist, and returns safe connection metadata.
8. Main reports success/failure to the renderer. No provider token or client secret is returned.

The existing deep-link dispatcher is retained as a configured fallback for enterprise OAuth clients whose registered redirect is `enterprise://oauth/callback`. The selected Google, Microsoft, and Atlassian desktop profiles use loopback + PKCE by default. Loopback and deep-link delivery race through one `ConnectorOAuthCoordinator`; the first valid state wins and the other listener closes.

### Agent use

1. `runtime_worker` asks backend internal `/internal/v1/mcp/cards`.
2. The runtime selects and loads a card through `DynamicMcpRegistry`.
3. It creates a backend client session and sends JSON-RPC to the backend internal RPC endpoint.
4. Backend revalidates the verified service identity, user/server ownership, grant, token expiry, endpoint, and tool allowlist.
5. Backend refreshes/decrypts the token only for the outbound vendor request and discards plaintext after the call.
6. Runtime receives only the vendor result and safe connector metadata.

### Failure behavior

- Missing preview enrollment, tenant id, admin consent, OAuth client, required API, region, or vendor support produces a stable availability status before opening a browser.
- OAuth state mismatch, replay, expiry, wrong signed-in user, wrong slug/server, redirect mismatch, and callback collision fail closed and delete the session.
- User denial returns `oauth_denied`; it is not a connector error and does not loop.
- Missing/extra tool descriptors produce `tool_contract_mismatch`. Known safe tools may remain visible only if their individual schemas still match; unknown tools stay hidden.
- A provider 401 triggers one backend refresh attempt under a per-connection lock. Failure marks `expired`/`reconnect_required`; no repeated refresh storm.
- 403 distinguishes tenant/admin policy denial from user scope when the vendor provides a safe code.
- Rate-limit responses preserve `Retry-After` as bounded metadata and do not auto-retry writes.
- Disconnect immediately prevents new proxy sessions, revokes with the provider when supported, and always wipes local access/refresh ciphertext.
- Vendor outage never causes AC8 browser fallback without a new user-visible proposal and browser consent.

## Alternatives considered

### Provider SDK clients in Electron or AI backend

Rejected. They duplicate OAuth/token refresh, move credentials into larger trust zones, create a second tool policy path, and violate service ownership.

### Store tokens in Electron `safeStorage`

Rejected. `safeStorage` owns the desktop product session and install bootstrap secrets. Connector tokens are backend records keyed by verified org/user/server and must remain behind `TokenVault` and RPC audit.

### One “Google Workspace” MCP registration

Rejected. Google publishes separate Gmail and Drive endpoints/scopes. Grouping is presentation only; grants remain separate and revocable.

### Raw Microsoft Graph adapter

Rejected for AC9. Work IQ is Microsoft’s published MCP path and provides tenant policy. Its preview status is surfaced honestly; a parallel Graph client would double contracts and tokens.

### Community Gmail/Drive/Outlook/Jira MCP servers

Rejected where an official vendor endpoint exists. Community servers create additional supply-chain, token, and data-processing trust without a requirement.

### Turn marketing catalog entries directly into MCP servers

Rejected. A marketing row lacks endpoint, auth metadata, release stage, permissions, tool policy, and verification evidence.

### Add another independent desktop catalog

Rejected. The desktop profile is a backend-owned reconciliation overlay that references existing marketing slugs and existing server ids. It does not become a third UI catalog.

### Manual token/API-token input

Rejected. Interactive desktop users use OAuth/PKCE. Atlassian API-token-only toolsets are outside AC9.

## Architecture and ownership

```text
Desktop renderer
  -> backend-facade /v1/connectors (list/detail/status)
  -> allowlisted preload connectConnector(slug)
       -> Electron-main ConnectorOAuthCoordinator
       -> backend-facade /v1/connectors/{slug}/start-oauth
       -> system browser -> loopback/deep link
       -> backend-facade /v1/connectors/oauth-callback
           -> backend McpRegistryService / RemoteMcpOAuthClient / TokenVault

runtime_worker
  -> BackendMcpProvider
  -> backend /internal/v1/mcp/cards + client-session + rpc
  -> official vendor MCP endpoint
```

### Ownership rules

- `services/backend` owns profiles, registrations, OAuth metadata, state/PKCE, client config, tokens, refresh/revoke, tool allowlists, RPC, audit, and availability.
- `services/backend-facade` authenticates and forwards public connector requests; it owns no provider logic.
- Electron main owns OS callback delivery and system-browser UX. It holds authorization codes transiently but never provider tokens.
- Renderer owns presentation only and calls facade for connector data.
- `services/ai-backend` owns agent-side card/tool loading and existing runtime middleware. It sees no token.
- Surface renderers remain pure functions of tool/result state and perform no I/O or OAuth.

### Catalog reconciliation

`connectors/desktop_profiles.yaml` is the source of truth for the desktop overlay. Each entry references:

- one existing marketing `connector_slug`;
- one stable MCP server id or one profile-owned seed definition;
- verified endpoint or tenant-template;
- release stage and `verified_at`;
- official reference URLs;
- OAuth setup and callback modes;
- provider scopes/admin permissions;
- allowed tools with risk and required product scope;
- required deployment/admin capabilities;
- user-facing supported/unsupported statements.

The loader fails boot in desktop mode on duplicate slugs/server ids, endpoint drift, unknown marketing reference, invalid HTTPS host/template, write tool without risk/approval metadata, or preview profile without a preview gate.

The generic web `DEFAULT_CATALOG` and current marketing catalog keep their non-desktop behavior. For desktop:

- `gmail`, `gdrive`, and `outlook` reuse their existing marketing rows and gain profile-owned MCP records;
- `atlassian` reuses `seed:atlassian` and gains one marketing row;
- no information is copied into Electron;
- product API projects a resolved profile; clients do not merge files.

### Endpoint and tool pinning

- Endpoints are exact vendor HTTPS origins and paths. Tenant placeholders are validated UUIDs substituted server-side.
- OAuth discovery may follow only a bounded chain to profile-allowed issuer/authorization/token/registration hosts.
- RPC does not follow redirects. A changed endpoint requires a catalog release.
- `tools/list` is schema-hashed after authentication. The backend intersects vendor tools with the profile allowlist.
- Unknown tools are hidden. Missing/schema-changed tools produce per-capability unavailable status.
- Vendor tool annotations inform, but do not replace, product risk policy.

## Typed contracts

### Backend profile contract

```python
class ConnectorReleaseStage(StrEnum):
    STABLE = "stable"
    PREVIEW = "preview"


class ConnectorAvailability(StrEnum):
    AVAILABLE = "available"
    PREVIEW = "preview"
    ADMIN_SETUP_REQUIRED = "admin_setup_required"
    TENANT_DISABLED = "tenant_disabled"
    UNSUPPORTED_BY_POLICY = "unsupported_by_policy"
    TOOL_CONTRACT_MISMATCH = "tool_contract_mismatch"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


class ProviderPermission(RuntimeContract):
    identifier: str
    kind: Literal["oauth_scope", "admin_permission", "provider_policy"]
    required_for: Literal["read", "draft", "write"]
    admin_consent_required: bool


class ConnectorToolPolicy(RuntimeContract):
    tool_name: str
    schema_sha256: str
    product_scope: Literal["read", "draft", "write"]
    risk: Literal["low", "medium", "high", "critical"]
    approval: Literal["session", "per_call", "disabled"]


class DesktopConnectorProfile(RuntimeContract):
    profile_id: str
    connector_slug: str
    server_id: str
    display_group: str
    endpoint_template: str
    transport: Literal["http"]
    release_stage: ConnectorReleaseStage
    verified_at: date
    reference_urls: tuple[str, ...]
    callback_modes: tuple[Literal["loopback_pkce", "deep_link_pkce"], ...]
    permissions: tuple[ProviderPermission, ...]
    tools: tuple[ConnectorToolPolicy, ...]
    unsupported_capabilities: tuple[str, ...]
    requires_pre_registered_client: bool
```

### Public catalog additions (shared, additive)

These extend `packages/api-types/src/connectors.ts` **additively**: every new field is optional, so existing web call sites and snapshots keep compiling unchanged.

```ts
export type ConnectorAvailability =
  | "available"
  | "preview"
  | "admin_setup_required"
  | "tenant_disabled"
  | "unsupported_by_policy"
  | "tool_contract_mismatch"
  | "temporarily_unavailable";

export interface ConnectorCapabilitySummary {
  readonly id: string;
  readonly label: string;
  readonly status: "supported" | "scope_required" | "unsupported";
  readonly read_only: boolean;
}

export interface ConnectorCatalogEntry {
  readonly slug: ConnectorSlug;
  readonly display_name: string;
  readonly description: string;
  readonly icon_hint?: string;
  readonly display_group?: string;
  readonly release_stage?: "stable" | "preview";
  readonly availability?: ConnectorAvailability;
  readonly availability_reason?: string;
  readonly capabilities?: ReadonlyArray<ConnectorCapabilitySummary>;
}
```

### Desktop-only OAuth transport (new module, not a shared-type change)

The desktop OAuth transport is a **new, desktop-only** module `packages/api-types/src/connectors-desktop.ts`. It does **not** touch the existing `StartConnectorOAuthResponse`/`ConnectorOAuthCallbackRequest` that the web redirect flow consumes. Desktop facade routes accept/return these desktop variants; web routes keep the existing shapes.

```ts
// packages/api-types/src/connectors-desktop.ts — desktop-only
export interface DesktopStartConnectorOAuthRequest {
  readonly callback:
    | {
        readonly kind: "desktop_loopback";
        readonly port: number;
        readonly path: "/connectors/oauth/cb";
      }
    | {
        readonly kind: "desktop_deep_link";
        readonly uri: "enterprise://oauth/callback";
      };
  readonly requested_product_scope: "read" | "draft";
}

export interface DesktopStartConnectorOAuthResponse {
  readonly oauth_session_id: string;
  readonly authorization_url: string;
  readonly state: string;
  readonly expires_at: string;
  readonly requested_permissions: ReadonlyArray<string>;
}

export interface DesktopConnectorOAuthCallbackRequest {
  readonly oauth_session_id: string;
  readonly code?: string;
  readonly state: string;
  readonly error?: string;
  readonly error_description?: string;
}
```

The backend reconstructs loopback URI from validated port and fixed path; it never accepts an arbitrary redirect URI from the request. Callback identity must match the org/user stored in the OAuth session.

### Web compatibility and the shared-type split

The earlier draft folded these desktop transport fields into the shared `StartConnectorOAuthResponse`/`ConnectorOAuthCallbackRequest`. That is a **breaking change**: making `oauth_session_id` required and demoting `code` to optional fails the web typecheck and the shipped web redirect flow (`apps/frontend/src/features/connectors/ConnectorsRoute.tsx`, `apps/frontend/src/api/connectorsApi.ts`), so "Web impact: none" would be false. The resolution and hard requirement for this AC:

- The web `StartConnectorOAuthResponse` (`{ authorization_url; state }`) and `ConnectorOAuthCallbackRequest` (`{ code; state }`) are **left byte-identical**; no field is added, removed, or made optional/required.
- All desktop OAuth transport lives in the desktop-only `connectors-desktop.ts` variant types above.
- Shared catalog additions are optional-only, preserving existing web call sites and snapshots.
- A web-regression test asserts `npm run typecheck --workspace @0x-copilot/api-types` and `@0x-copilot/frontend` pass with the shared web OAuth shapes unchanged.

### Stable errors

- `connector_profile_unavailable`
- `connector_preview_disabled`
- `connector_admin_setup_required`
- `connector_tenant_disabled`
- `connector_oauth_client_unconfigured`
- `connector_oauth_redirect_unsupported`
- `connector_oauth_state_invalid`
- `connector_oauth_expired`
- `connector_oauth_denied`
- `connector_oauth_exchange_failed`
- `connector_tool_contract_mismatch`
- `connector_scope_required`
- `connector_reconnect_required`
- `connector_provider_rate_limited`
- `connector_provider_unavailable`

## Critical current and proposed files

### Current evidence and reuse points

- `services/backend/src/backend_app/mcp_catalog.py` — deterministic MCP seed catalog; existing `seed:atlassian` at the verified `/authv2` endpoint.
- `services/backend/src/backend_app/connectors/catalog.yaml` — separate marketing catalog with Gmail, Drive, and Outlook but no Atlassian.
- `services/backend/src/backend_app/connectors/service.py` — connector read model and intended write-through reuse of `McpRegistryService`.
- `services/backend/src/backend_app/connectors/routes.py` — public routes; start OAuth remains a boot-injected stub and callback requires a binder.
- `services/backend/src/backend_app/mcp_oauth.py` — protected-resource/auth-server discovery, PKCE, dynamic/pre-registered client support, exchange, and refresh.
- `services/backend/src/backend_app/service.py` — MCP state, token encryption, client session, and token-attaching internal RPC proxy.
- `services/backend/src/backend_app/token_vault.py` — encrypted connector token authority.
- `services/backend/src/backend_app/desktop_app.py` — desktop production composition with install-local Fernet vault secret.
- `services/backend-facade/src/backend_facade/connector_routes.py` — thin authenticated connector proxy.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py` — cards/client-session/RPC path that keeps tokens in backend.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/registry.py` — permission-filtered dynamic card registry.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py` and `call_tool.py` — auth interrupts and normal invocation path.
- `apps/desktop/main/auth/google-login.ts` — facade-brokered system-browser/loopback precedent.
- `apps/desktop/main/auth/loopback-server.ts` — random-port, single-path, state-bound callback listener.
- `apps/desktop/main/deep-links.ts` — registered custom-scheme callback dispatch.
- `apps/desktop/main/auth/secret-storage.ts` — app-session storage; explicitly not connector token storage.
- `packages/api-types/src/connectors.ts` — current connector wire contracts.

### Proposed implementation files

- `services/backend/src/backend_app/connectors/desktop_profiles.yaml`
- `services/backend/src/backend_app/connectors/profile_catalog.py`
- `services/backend/src/backend_app/connectors/oauth_service.py`
- `services/backend/src/backend_app/connectors/tool_policy.py`
- `services/backend/tests/contract/connectors/test_desktop_profiles.py`
- `services/backend/tests/integration/connectors/test_desktop_oauth.py`
- `services/backend/tests/integration/mcp/test_profile_rpc_policy.py`
- `apps/desktop/main/connectors/oauth-coordinator.ts`
- `apps/desktop/main/connectors/connector-service.ts`
- `apps/desktop/main/connectors/oauth-coordinator.test.ts`
- `apps/desktop/renderer/connectors/ConnectorAvailability.tsx`
- `packages/api-types/src/connectors.ts` — **additive-only** (optional catalog fields); existing web OAuth shapes unchanged.
- `packages/api-types/src/connectors-desktop.ts` — **new** desktop-only OAuth transport variant types.
- `services/backend/docs/features/desktop-connectors.md`
- `docs/deployment/google-workspace-mcp.md`
- `docs/deployment/microsoft-work-iq.md`
- `docs/deployment/atlassian-rovo-mcp.md`

The facade changes only to forward the typed start/callback body and preserve verified identity. No provider logic belongs there.

## Security and threat model

| Threat                               | Control                                                                          | Required evidence                |
| ------------------------------------ | -------------------------------------------------------------------------------- | -------------------------------- |
| Caller-supplied org/user             | Facade bearer verification and backend service headers; callback owner match     | Cross-user/tenant tests          |
| OAuth CSRF/code injection            | 256-bit state, PKCE S256, exact callback, one-time pop, five-minute TTL          | Replay/mix-up tests              |
| Redirect URI abuse                   | Backend reconstructs fixed loopback/deep-link target                             | Arbitrary host/scheme tests      |
| Malicious discovery/SSRF             | Static endpoint/issuer host allowlist, HTTPS, bounded no-private redirect policy | Metadata redirect/DNS tests      |
| Token exfiltration by redirect       | RPC and token endpoints do not follow unapproved redirects                       | Cross-origin redirect tests      |
| Electron/renderer token theft        | Only code/state crosses main; no token response; no generic external-open bridge | IPC and memory/log assertions    |
| AI-worker token theft                | Backend attaches bearer after authorization and returns result only              | Mock-vendor header assertions    |
| Cross-connector token use            | Token keyed by verified org/user/server/resource/audience                        | Confused-deputy tests            |
| Tool catalog drift                   | Exact allowlist and schema hash; unknown hidden                                  | Added/destructive-tool fixture   |
| Broad provider permission            | Product tool intersection and per-call approval                                  | Microsoft/Atlassian policy tests |
| Prompt injection in mail/docs/issues | Connector output is untrusted; cannot expand scopes/tools/origins                | Malicious-content tests          |
| Refresh race/replay                  | Per-connection lock, atomic replacement, old-token invalidation                  | Concurrency tests                |
| Disconnect residual access           | Block sessions first, provider revoke when available, local token wipe           | Deletion/revoke tests            |
| Secret in telemetry                  | Structured allowlist logging and secret-shaped scanners                          | Logs/events/crash fixture        |

The local desktop token vault uses Fernet derived from an install secret generated/protected by Electron boot secret handling. This is product-level encrypted-at-rest protection for a single-user desktop, not a claim of managed KMS. Managed/server deployment profiles retain their KMS requirement.

## Persistence, retention, deletion, and recovery

- OAuth state stores server/org/user, PKCE verifier, exact redirect, requested product scope, provider permissions, created/expiry timestamps, and nonce. It expires after five minutes and is deleted on success, denial, error, timeout, or restart sweep.
- Access/refresh tokens and configured confidential-client secrets are encrypted only through backend `TokenVault`.
- Connector rows store safe endpoint/profile metadata, granted scope identifiers, token envelope reference, owner, state, schema hash, and timestamps; no plaintext secret.
- Electron keeps code/state only in memory until callback completes. It persists no provider credential.
- AI backend keeps no token and no refresh state.
- Disconnect blocks new sessions, attempts provider revocation where documented, destroys local token envelopes/cache entries, and retains a tombstoned connector/audit row so consumers show “needs reconnect.”
- User deletion removes the connector grant and token regardless of chat retention. A legal hold may retain audit metadata, not a live credential.
- OAuth client configuration belongs to deployment/admin setup. Removing a provider profile disables calls but does not silently delete user grants; emergency revoke is a separate audited operation.
- Connector tool outputs follow AC4/AC10 content retention: raw mail/document/issue payload artifacts default to 30 days, while safe chat summaries remain until explicit chat delete. Provider data is not copied merely because a connector is connected.
- On restart, expired OAuth sessions are swept, token cache is empty, encrypted tokens remain, and cards are re-probed. In-flight mutating calls are not blindly retried.

## Observability and audit

### Events

- `connector.profile_unavailable`
- `connector.oauth_started`
- `connector.oauth_denied`
- `connector.oauth_completed`
- `connector.oauth_failed`
- `connector.scope_changed`
- `connector.tool_contract_mismatch`
- `connector.token_refreshed`
- `connector.reconnect_required`
- `connector.rpc_started`
- `connector.rpc_completed`
- `connector.disconnected`
- `connector.token_deleted`

Events include verified org/user, connector/server/profile id, provider, release stage, requested/granted scope identifiers, tool name/risk, approval id, schema hash, duration, status, safe vendor error class, bytes, and correlation ids. They exclude auth URL query, code, state, verifier, client secret, access/refresh token, request/response body, email/document/issue content, and recipient/body fields.

### Metrics

- `backend_connector_availability{profile,status}`
- `backend_connector_oauth_total{profile,outcome}`
- `backend_connector_oauth_seconds{profile}`
- `backend_connector_refresh_total{profile,outcome}`
- `backend_connector_rpc_total{profile,tool,outcome}`
- `backend_connector_rpc_seconds{profile,tool}`
- `backend_connector_tool_contract_mismatch{profile}`
- `backend_connector_token_delete_total{profile,outcome}`

Audit answers who installed/approved/disconnected, which deployment profile/provider/site and provider permissions were involved, what tool ran, whether it read or changed data, the approval/correlation id, result, token lifecycle action, and deletion evidence. Audit payloads do not store content or credentials. External provider audit/SIEM completeness is a deployment control and must be documented per vendor.

## Acceptance criteria

- Desktop catalog resolves Gmail, Drive, Outlook, and existing Atlassian/Jira to installable or explicitly unavailable profile rows.
- Non-desktop catalogs and connector behavior are unchanged.
- The existing `seed:atlassian` row is reused with no duplicate.
- Endpoints, release stages, setup, permissions, and tool support match official vendor documentation cited below.
- Google/Microsoft preview connectors are disabled by default and fail with actionable status.
- Gmail requests `gmail.readonly` by default and `gmail.compose` only for draft scope.
- Drive requests `drive.readonly` by default and `drive.file` only for create/copy/upload.
- Sheets/Slides are described only as supported Drive file content/create types; unsupported edit operations are explicit.
- Microsoft uses tenant Work IQ Mail endpoint, discovered OAuth metadata, and the `WorkIQ-MailServer` admin permission without fabricating Graph scopes.
- Atlassian uses OAuth 2.1 and the current `/authv2` endpoint; product `read` is not mislabeled as an OAuth scope.
- OAuth uses system browser, PKCE, state, exact loopback/deep-link callback, owner match, and single-use completion.
- Tokens/client secrets exist only encrypted in backend `TokenVault`; secret-shaped tests find none in Electron, renderer, AI backend, logs, events, artifacts, or API responses.
- Renderer data calls traverse facade; agent calls traverse backend internal MCP proxy.
- Unknown/schema-drifted/destructive vendor tools are hidden, not auto-exposed.
- Disconnect prevents new calls and wipes local token envelopes even if provider revoke fails.
- The shared web `StartConnectorOAuthResponse`/`ConnectorOAuthCallbackRequest` are unchanged; desktop OAuth transport is confined to `connectors-desktop.ts`; `api-types` and `frontend` typechecks pass (proving "Web impact: none").

## Detailed test plan

### Catalog and contract

- Validate unique profile/slug/server ids, exact HTTPS endpoints/templates, `verified_at`, official references, release gate, permissions, risk, and schema hashes.
- Fail on unknown marketing slug, duplicate Jira seed, write tool without per-call approval, or preview profile without gate.
- Golden-test the projected public catalog for desktop and unchanged catalog for web profiles.
- Web-compat regression: assert the shared `connectors.ts` OAuth types are unchanged (byte-identical `StartConnectorOAuthResponse`/`ConnectorOAuthCallbackRequest`), desktop transport is only in `connectors-desktop.ts`, and `@0x-copilot/api-types` + `@0x-copilot/frontend` typechecks pass.
- Simulate missing preview enrollment, tenant, client, admin consent, API enablement, region, provider policy, and tool drift; assert stable status/reason.

### OAuth

- Success, denial, timeout, user cancel, error callback, app restart, second connect replacing first, loopback/deep-link race, and browser open failure.
- State mismatch, replay, expired state, wrong callback path/port, wrong callback kind, wrong user/org/server, code substitution, OAuth mix-up, duplicate callback, and malformed provider error.
- PKCE verifier/challenge test vector and proof that verifier never leaves backend.
- Google/Microsoft public-client and configured confidential-client token auth modes.
- Discovery with no registration endpoint but configured client; fail closed with neither.
- Assert callback API never returns provider tokens.

### Endpoint, proxy, and token security

- Reject HTTP, userinfo, private/link-local/loopback/metadata, DNS rebinding, cross-host metadata, issuer mismatch, redirect loops, cross-origin authorization/token/RPC redirect, and tenant-template injection.
- Verify outbound bearer audience/server/user and that it is attached only after final endpoint validation.
- Search Electron/renderer/AI process env, heap-safe test doubles, logs, events, traces, crash reports, API responses, and artifacts for seeded access/refresh/client secrets.
- Concurrent 401s perform one refresh; token replacement is atomic; disconnect racing a call blocks subsequent calls.
- Cross-user, cross-tenant, cross-server, and deleted grant return indistinguishable authorization failure.

### Tool policy and runtime

- Vendor adds an unknown destructive tool: it is absent.
- Known tool schema hash changes: profile reports mismatch.
- Google Gmail/Drive allowlists and optional scope transitions.
- Drive Sheets/Slides supported MIME fixture returns a text representation; cell/formula/slide editing remains unsupported.
- Microsoft send/delete/reply/update tools stay hidden.
- Atlassian Jira read tools load; write/API-token-only/attachment-command tools stay hidden.
- Calls pass through permission, approval, budget, citation, payload offload, and audit.
- Malicious email/document/issue instructions cannot request more scopes, reveal tokens, enable tools, or switch to browser without consent.

### UI and cross-platform

- Available/preview/admin setup/tenant disabled/unsupported/reconnect/connected states render on macOS and Windows.
- System browser opens exact authorization origin; renderer has no generic `openExternal` or URL parameter.
- Loopback binds IPv4 localhost only, exact path, random port, one response, five-minute timeout, and closes on every branch.
- Deep-link parser accepts only exact scheme/path and redacts query in logs.
- Restart recovers connected metadata and sweeps OAuth sessions without persisting code/state.

### Live compatibility

Opt-in, secret-isolated staging checks run outside PR CI against dedicated test tenants:

- Google Developer Preview Gmail and Drive read/draft profiles;
- Microsoft Work IQ Mail preview read/draft profile;
- Atlassian Rovo OAuth/Jira read profile.

They record endpoint, metadata issuer, scopes/permissions, tools-list schema hashes, and safe results. Production user data and credentials are forbidden.

## Rollout, migration, and backout

1. Land profile schema/loader, API contract, fake providers, and status UI with all profiles disabled.
2. Reconcile catalogs and migrate `seed:atlassian` in place; prove idempotence and no web diff.
3. Land generic desktop OAuth coordinator and backend binder against a fake OAuth/MCP server.
4. Enable Atlassian/Jira read-only for internal desktop users.
5. Enable Google Gmail/Drive Developer Preview for enrolled internal tenants; add optional drafts/Drive creation after scope and approval review.
6. Enable Microsoft Work IQ Mail preview only for configured internal tenant canaries.
7. AC10 controls broader rollout. Preview profiles cannot become default until vendor GA review updates release stage, schema hashes, and deployment documentation.

Stop conditions include token/client-secret exposure, callback mix-up, cross-user access, endpoint redirect with bearer, new unclassified tool, scope escalation, duplicate seed, inaccurate capability claim, unapproved write, or missing token-deletion evidence.

Normal backout disables profile visibility and new auth/RPC, closes OAuth sessions, and retains encrypted grants for a later fixed release; users can still explicitly disconnect. Security backout additionally revokes where supported, wipes all affected token envelopes, marks consumers reconnect-required, and records deletion evidence. Neither path moves tokens to Electron or a fallback adapter.

## Definition of done

- AC1 and AC9 are accepted.
- Desktop profile catalog, reconciliation/migration, real connector OAuth binder, callback owner validation, endpoint/tool policy, token lifecycle, facade forwarding, Electron coordinator, status UI, events, metrics, and audit are implemented.
- Google, Microsoft, and Atlassian official references are re-verified at implementation and release time; schema hashes are pinned from clean staging tenants.
- Unit, contract, OAuth, SSRF, token-secret, cross-tenant, tool-drift, cross-platform, migration, staging, and web-regression tests pass.
- Support/admin docs cover per-provider setup, preview limitations, disconnect/revoke, incident disable, and data processing.
- Evidence proves provider tokens/client secrets appear only as backend vault ciphertext at rest and transient backend memory for outbound calls.

## Why this is sane under SOLID, DRY, KISS, and single-source-of-truth

- **Single responsibility:** backend owns connector authority; main owns OS OAuth delivery; facade forwards; runtime invokes; renderer presents.
- **Open/closed:** another official MCP provider adds one validated profile and tool policy without a new token system.
- **Liskov substitution:** each remote server satisfies the existing MCP provider/client boundary and common contract tests.
- **Interface segregation:** Electron receives code/state only; AI receives cards/results only; neither receives vault operations.
- **Dependency inversion:** apps depend on facade contracts, AI depends on backend MCP port, and backend depends on `TokenVault`/store ports.
- **DRY:** current OAuth, registry, vault, proxy, middleware, loopback, deep-link, and connector read model are extended rather than copied.
- **KISS:** official endpoints only, three profile groups, system browser, one backend token authority, explicit preview gates, unknown tools hidden.
- **Single source of truth:** desktop overlay resolves marketing and MCP references server-side; `TokenVault` owns credentials; vendor `tools/list` intersected with pinned policy owns actual capability.

## Residual risks

- Google Workspace MCP and Microsoft Work IQ Mail are previews and may change or be withdrawn.
- Microsoft’s provider permission is broader than the initial product tool set; backend filtering limits 0xCopilot behavior but cannot narrow provider-side consent beyond Microsoft’s model.
- Email, documents, and issues are prompt-injection channels. Environmental permission/tool controls remain mandatory.
- Vendor revocation and audit behavior varies. Local token deletion is enforceable; provider-side deletion evidence may be incomplete.
- An install-local Fernet vault protects against casual disk access but not a compromised logged-in OS user or process with app secret access.

## References

### Repository

- [`services/backend/src/backend_app/mcp_catalog.py`](../../../../services/backend/src/backend_app/mcp_catalog.py)
- [`services/backend/src/backend_app/connectors/catalog.yaml`](../../../../services/backend/src/backend_app/connectors/catalog.yaml)
- [`services/backend/src/backend_app/mcp_oauth.py`](../../../../services/backend/src/backend_app/mcp_oauth.py)
- [`services/backend/src/backend_app/service.py`](../../../../services/backend/src/backend_app/service.py)
- [`services/backend/src/backend_app/token_vault.py`](../../../../services/backend/src/backend_app/token_vault.py)
- [`services/backend/src/backend_app/connectors/routes.py`](../../../../services/backend/src/backend_app/connectors/routes.py)
- [`services/backend-facade/src/backend_facade/connector_routes.py`](../../../../services/backend-facade/src/backend_facade/connector_routes.py)
- [`services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py`](../../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py)
- [`apps/desktop/main/auth/google-login.ts`](../../../../apps/desktop/main/auth/google-login.ts)
- [`apps/desktop/main/auth/loopback-server.ts`](../../../../apps/desktop/main/auth/loopback-server.ts)
- [`apps/desktop/main/deep-links.ts`](../../../../apps/desktop/main/deep-links.ts)
- [`packages/api-types/src/connectors.ts`](../../../../packages/api-types/src/connectors.ts)

### Official provider and protocol documentation

- [Google Workspace MCP configuration](https://developers.google.com/workspace/guides/configure-mcp-servers) — Developer Preview status, endpoints, setup, scopes, tools, and prompt-injection warning.
- [Gmail MCP reference](https://developers.google.com/workspace/gmail/api/reference/mcp) and [Gmail `create_draft`](https://developers.google.com/workspace/gmail/api/reference/mcp/tools_list/create_draft).
- [Drive MCP reference](https://developers.google.com/workspace/drive/api/reference/mcp), [`read_file_content`](https://developers.google.com/workspace/drive/api/reference/mcp/tools_list/read_file_content), and [`create_file`](https://developers.google.com/workspace/drive/api/reference/mcp/tools_list/create_file).
- [Work IQ Mail reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-mail-work-iq) — preview endpoint and Microsoft Graph Mail tools.
- [Work IQ MCP overview](https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview) — tenant endpoint/client setup and `WorkIQ-MailServer` permission.
- [Work IQ API permissions](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/permissions) — distinction between generic Work IQ API audience/scopes and Agent 365 Mail server setup.
- [Atlassian Rovo MCP getting started](https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started/), [authentication](https://developer.atlassian.com/cloud/rovo-mcp/guides/authentication-and-authorization/), and [supported tools](https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/).
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) — OAuth 2.1, protected resource metadata, resource indicators, and audience validation.
- [LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp) — client/tool-loading prior art; 0xCopilot retains its backend token proxy.
- [Cursor MCP](https://cursor.com/docs/context/mcp) — public client configuration behavior only; no claim about private credential storage or implementation.
