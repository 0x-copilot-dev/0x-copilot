# MCP Registry

How MCP servers are registered, managed, and authenticated. Covers the server catalog,
OAuth flow, token vault, and the internal API consumed by ai-backend.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `McpServerRecord`, `TokenEnvelope`
- [guides/add-mcp-catalog-entry.md](../guides/add-mcp-catalog-entry.md) — adding a curated catalog entry
- [reference/internal-api.md](../reference/internal-api.md) — internal routes for ai-backend

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

| Route                                           | What it returns                                                                                            |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET /internal/v1/mcp/servers`                  | `InternalMcpServerListResponse` — cards filtered by org, with `auth_state`, `load_cost`, `required_scopes` |
| `POST /internal/v1/mcp/servers/{id}/auth/start` | Initiates OAuth; returns `McpAuthStartResponse` (ai-backend triggers this during an approval interrupt)    |
| `GET /internal/v1/mcp/sessions/{id}`            | Returns `InternalMcpClientSession` with `credential_ref`                                                   |
| `POST /internal/v1/mcp/servers/{id}/rpc`        | JSON-RPC proxy: attaches bearer from vault → forwards to server URL                                        |

The RPC proxy injects the decrypted OAuth token into the upstream request. It does NOT
return the plaintext token to ai-backend; the proxy call is the only path where tokens
are momentarily decrypted.

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
