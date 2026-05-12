# Public API — backend

All routes on `services/backend` that are exposed via `backend-facade` to browsers.
These routes are at the `/v1/*` prefix on the facade (`:8200`). The backend itself
runs on `:8100` and is never called directly by browsers.

See also:

- [reference/internal-api.md](internal-api.md) — `/internal/v1/*` routes (backend-to-backend)
- [architecture/01-request-lifecycle.md](../architecture/01-request-lifecycle.md) — auth path

---

## Health

| Method | Path         | Auth | Notes                  |
| ------ | ------------ | ---- | ---------------------- |
| `GET`  | `/v1/health` | None | Returns `{status: ok}` |
| `GET`  | `/healthz`   | None | Liveness probe         |
| `GET`  | `/readyz`    | None | Readiness probe        |

---

## MCP Registry

All require a valid session bearer (forwarded via facade with service headers).

| Method   | Path                                     | Scope       | Notes                                        |
| -------- | ---------------------------------------- | ----------- | -------------------------------------------- |
| `POST`   | `/v1/mcp/servers`                        | `mcp:write` | Register a custom MCP server                 |
| `GET`    | `/v1/mcp/servers`                        | `mcp:read`  | List user's registered servers               |
| `PATCH`  | `/v1/mcp/servers/{server_id}`            | `mcp:write` | Update display_name / enabled / oauth_client |
| `DELETE` | `/v1/mcp/servers/{server_id}`            | `mcp:write` | Remove a server; revokes tokens              |
| `GET`    | `/v1/mcp/catalog`                        | `mcp:read`  | List curated catalog entries                 |
| `POST`   | `/v1/mcp/servers/install`                | `mcp:write` | Install a catalog entry by slug              |
| `POST`   | `/v1/mcp/servers/{server_id}/auth/start` | `mcp:write` | Begin OAuth flow; returns `auth_url`         |
| `POST`   | `/v1/mcp/servers/{server_id}/auth/skip`  | `mcp:write` | Mark server as auth-not-needed               |
| `GET`    | `/v1/mcp/oauth/callback`                 | `mcp:write` | OAuth code exchange callback                 |

---

## Skills

| Method   | Path                    | Auth    | Notes                                                     |
| -------- | ----------------------- | ------- | --------------------------------------------------------- |
| `POST`   | `/v1/skills`            | Session | Create a user skill                                       |
| `GET`    | `/v1/skills`            | Session | List skills (facade merges with ai-backend system skills) |
| `GET`    | `/v1/skills/{skill_id}` | Session | Get a single skill                                        |
| `PUT`    | `/v1/skills/{skill_id}` | Session | Replace skill content                                     |
| `DELETE` | `/v1/skills/{skill_id}` | Session | Delete skill                                              |

---

## API Keys

| Method   | Path                           | Auth    | Notes                                      |
| -------- | ------------------------------ | ------- | ------------------------------------------ |
| `POST`   | `/v1/api-keys`                 | Session | Mint a new key; returns plaintext once     |
| `GET`    | `/v1/api-keys`                 | Session | List keys (no plaintext)                   |
| `DELETE` | `/v1/api-keys/{key_id}`        | Session | Revoke a key                               |
| `POST`   | `/v1/api-keys/{key_id}/rotate` | Session | Rotate; revokes old, returns new plaintext |

---

## Dev IdP (development only)

Registered only when `BACKEND_ENVIRONMENT=development`.

| Method | Path                    | Auth | Notes                                  |
| ------ | ----------------------- | ---- | -------------------------------------- |
| `GET`  | `/v1/dev/personas`      | None | List dev test personas                 |
| `POST` | `/v1/dev/identity/mint` | None | Mint a signed bearer for a dev persona |

---

## Response shapes

All routes return JSON. Error responses follow:

```json
{ "detail": "<human-readable message>" }
```

Successful list responses follow:

```json
{"servers": [...]}   // MCP servers
{"skills": [...]}    // Skills
{"keys": [...]}      // API keys
```

See [architecture/02-contracts.md](../architecture/02-contracts.md) for full Pydantic field tables.
