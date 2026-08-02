# MCP Registry

How MCP servers are registered, managed, and authenticated. Covers the server catalog,
OAuth flow, token vault, and the internal API consumed by ai-backend.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `McpServerRecord`, `TokenEnvelope`
- [guides/add-mcp-catalog-entry.md](../guides/add-mcp-catalog-entry.md) — adding a curated catalog entry
- [reference/internal-api.md](../reference/internal-api.md) — internal routes for ai-backend
- [ai-backend MCP control-plane operations](../../../ai-backend/docs/runbooks/mcp-control-plane-operations.md) — revision-feed, session-pool, and rollback operations

---

## What it does

Backend owns the authoritative registry of MCP servers per (org, user). It stores server
metadata (URL, transport, auth mode), OAuth credentials (encrypted via `TokenVault`),
auth state, and brand metadata. It also serves as the OAuth relay — browser redirects
to `backend`'s callback endpoint, which exchanges the code for tokens and stores them.

The ai-backend calls `GET /internal/v1/mcp/servers` at run-start to get a filtered list
of server cards (no secrets), and `POST /internal/v1/mcp/servers/{id}/rpc` to proxy
JSON-RPC calls to the actual MCP server.

---

## Key modules

| File                         | Role                                                             |
| ---------------------------- | ---------------------------------------------------------------- |
| `backend_app/service.py`     | Domain orchestration: CRUD, OAuth flow, JSON-RPC proxy           |
| `backend_app/store.py`       | `McpServerStore`, `McpAuthSessionStore`, `McpTokenStore`         |
| `backend_app/mcp_oauth.py`   | OAuth discovery, DCR, authorization URL, token exchange, refresh |
| `backend_app/mcp_catalog.py` | Static curated catalog (`DEFAULT_CATALOG`; `CatalogEntry`)       |
| `backend_app/token_vault.py` | Encryption interface: `LocalTokenVault` / `AwsKmsTokenVault`     |
| `backend_app/contracts.py`   | `McpServerRecord`, `TokenEnvelope`, all request/response shapes  |

---

## Server registration flow

### Custom server (JSON URL input)

1. Client calls `POST /v1/mcp/servers` (via facade) with `url`, optional `display_name`, `transport`, `auth_mode`.
2. `service.py` normalizes `name` (slug from URL), validates the URL (public HTTPS only).
3. Creates `McpServerRecord` with `auth_state=unauthenticated`.
4. Stores via `McpServerStore.upsert()`.
5. Returns `McpServerResponse` (no secrets).

### Catalog install (`POST /v1/mcp/servers/install`)

1. Client sends `slug` + optional `oauth_client` (if `requires_pre_registered_client`).
2. Service resolves `slug` against `DEFAULT_CATALOG`.
3. Creates server with stable `server_id = "seed:" + slug` (idempotent).
4. Copies brand metadata from catalog entry to the record.
5. Returns the existing record if the server is already installed.

---

## OAuth flow (`mcp_oauth.py`)

### Server-supports-discovery path

1. `McpOAuthService.discover(url)` — fetches `/.well-known/oauth-authorization-server` or `/.well-known/openid-configuration` from the MCP server.
2. `dynamic_client_registration(discovery, redirect_uri)` — POST to `registration_endpoint` to obtain `client_id` / `client_secret`.
3. Result stored in `McpServerRecord.oauth_client` (secret encrypted by `TokenVault`).

### Pre-registered client path (when server doesn't support DCR)

Client supplies `client_id`, `client_secret`, `authorization_endpoint`, `token_endpoint`, `scope` in `CreateMcpServerRequest.oauth_client`. Service encrypts the secret and stores the `McpOAuthClientConfig`.

### Auth start (`POST /v1/mcp/servers/{id}/auth/start`)

1. Generates `code_verifier` (PKCE S256).
2. Builds the authorization URL with `state`, `nonce`, `code_challenge`.
3. Creates `McpAuthSessionRecord` (TTL ~10 min).
4. Returns `McpAuthStartResponse(auth_url, expires_at)`.

### Callback (`GET /v1/mcp/oauth/callback`)

1. Looks up `McpAuthSessionRecord` by `state`.
2. Exchanges `code` + `code_verifier` for tokens via `token_endpoint`.
3. Encrypts tokens with `TokenVault` → creates `TokenEnvelope`.
4. Updates `McpServerRecord.auth_state = authenticated`.
5. Logs an audit event.

### Token refresh

`mcp_oauth.py` — called by `service.py` before forwarding a JSON-RPC call when `expires_at < now + buffer`. Refreshes via `refresh_token`; stores new `TokenEnvelope`. On failure, sets `auth_state = auth_failed`.

---

## Token vault (`token_vault.py`)

| Adapter            | When used                                  | Backend                                                            |
| ------------------ | ------------------------------------------ | ------------------------------------------------------------------ |
| `LocalTokenVault`  | `MCP_TOKEN_VAULT_BACKEND=local` (dev only) | Fernet symmetric encryption                                        |
| `AwsKmsTokenVault` | `MCP_TOKEN_VAULT_BACKEND=aws_kms`          | AWS KMS envelope encryption; `kms_key_id` field on `TokenEnvelope` |

The vault wraps the raw access/refresh token bytes into an encrypted envelope. Callers
never see plaintext tokens at rest. The `credential_ref` field in `InternalMcpClientSession`
is the vault lookup key returned to ai-backend so it can decrypt for actual HTTP calls.

**Production invariant:** `require_kms_token_vault=True` in bank/government profiles causes
startup to fail if `LocalTokenVault` is used.

---

## Curated catalog (`mcp_catalog.py`)

`DEFAULT_CATALOG: list[CatalogEntry]` — static list of verified MCP servers. Each entry has:

| Field                                                                      | Notes                                           |
| -------------------------------------------------------------------------- | ----------------------------------------------- |
| `slug`                                                                     | Stable identifier; `server_id = "seed:" + slug` |
| `url`, `transport`, `auth_mode`                                            | Connection details                              |
| `display_name`, `description`, `logo_url`, `brand_color`, `scopes_summary` | Brand metadata                                  |
| `requires_pre_registered_client`                                           | When True, client must supply OAuth credentials |
| `default_scopes`                                                           | Pre-populated scope hint for the OAuth request  |
| `discoverable`                                                             | Phase 2 progressive-discovery hint              |

Served at `GET /v1/mcp/catalog` (auth required, org-agnostic).

---

## Internal API (consumed by ai-backend)

| Route                                             | What it returns                                                                                            |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET /internal/v1/mcp/servers`                    | `InternalMcpServerListResponse` — cards filtered by org, with `auth_state`, `load_cost`, `required_scopes` |
| `POST /internal/v1/mcp/servers/{id}/auth/start`   | Initiates OAuth; returns `McpAuthStartResponse` (ai-backend triggers this during an approval interrupt)    |
| `GET /internal/v1/mcp/sessions/{id}`              | Returns `InternalMcpClientSession` with `credential_ref`                                                   |
| `POST /internal/v1/mcp/servers/{id}/rpc`          | JSON-RPC proxy: attaches bearer from vault → forwards to server URL                                        |
| `POST /internal/v1/mcp/servers/{id}/access-token` | `InternalMcpAccessToken` — a scoped, short-TTL bearer for a runtime that connects directly                 |

There are two credential topologies, and they differ in where the plaintext ends up.

**Proxied (`/rpc`).** The proxy injects the decrypted OAuth token into the upstream
request and returns only the JSON-RPC result. No plaintext credential crosses back to
ai-backend.

**Direct-connect (`/access-token`).** The runtime opens the MCP server itself, so it
needs a bearer. `McpRegistryService.mint_internal_access_token` runs the _same_ admission
sequence as the proxy — tenant scope → PRD-06 D3 access-mode gate (`_require_mintable_access_mode`,
which refuses before anything is decrypted) → liveness → `_require_valid_token`'s
refresh-on-expiry — and then answers with `{url, transport, access_token, expires_at,
scopes, access_mode}`. What stays behind: the refresh token (the response contract has no
field it could ride, and the contract forbids extras), the vault ciphertext, the OAuth
client secret, and configured header values. `expires_at` is the earlier of the stored
credential's own expiry and `McpRegistryService.ACCESS_TOKEN_MAX_TTL`, so the caller
re-mints on a bounded schedule and the gate is re-evaluated every time.

**The gate is stricter for a mint than for the proxy**, and the asymmetry is the whole
point. The proxy sees the call it is authorizing: under `ConnectorAccessMode.READ` it lets
reads through and classifies a `tools/call` against the server's advertised annotations,
refusing fail-closed anything it cannot show to be read-only. A mint names no method and
no tool; what it hands over is the provider's own bearer, and from the moment that
plaintext leaves the process there is no chokepoint left to classify anything, no way to
narrow the credential to reads, and no way to withdraw it before `ACCESS_TOKEN_MAX_TTL`
expires. The same fail-closed rule therefore lands on the mint as a whole:

| Access mode | Proxy (`/rpc`)                                      | Mint (`/access-token`)           |
| ----------- | --------------------------------------------------- | -------------------------------- |
| `off`       | `403 connector_access_off`                          | `403 connector_access_off`       |
| `read`      | reads pass; a non-read-only `tools/call` is refused | `403 connector_access_read_only` |
| `read_act`  | everything passes                                   | mints, `access_mode: "read_act"` |
| no row      | everything passes                                   | mints, `access_mode: null`       |

Consequences worth stating rather than discovering:

- A `read` connector — the default for a newly projected row — **cannot be
  direct-connected at all.** Its reads remain fully available through the proxy, which can
  police each one. Widening this is a product decision (a provider supporting downscoped
  token exchange, or a runtime decision point whose verdicts the backend can verify), not
  a default, and it must not be re-introduced as an unenforced note in a docstring.
- `access_mode` on the response is the mode the gate evaluated, not a second enforcement
  point — the mint already refused everything above it. It exists so the authority a
  credential was released under travels with the credential, is written to the
  `mcp_access_token_minted` audit row, and gives a holder that wants to be stricter than
  the backend what it needs to be. `null` means no connector row joins the server: nothing
  was configured, so nothing gated.
- A server with `auth_mode=none` (nothing to scope down) or a stdio server (no endpoint
  another service could use) is answered `409` with a stable reason code
  (`server_has_no_credential` / `server_has_no_endpoint`), not `403` — the caller is
  entitled to the server; its shape simply admits no bearer.

---

## Audit logging

Every significant MCP event appends to the MCP audit chain:

- Server created / updated / deleted
- OAuth session started
- Token exchanged / refreshed / revoked
- Auth state changed

Audit rows are immutable and chain-signed (`AuditChainSigner`).

---

## Auth state machine

```
UNAUTHENTICATED → (auth/start) → AUTH_PENDING
AUTH_PENDING → (callback success) → AUTHENTICATED
AUTH_PENDING → (callback error) → AUTH_FAILED
AUTHENTICATED → (token expired + refresh failed) → AUTH_FAILED
AUTH_FAILED → (auth/start again) → AUTH_PENDING
UNAUTHENTICATED → (auth/skip) → AUTH_SKIPPED
```

`AUTH_SKIPPED` — user confirmed the server needs no auth (e.g., a public MCP endpoint).
