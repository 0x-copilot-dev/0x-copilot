# API Surface — backend-facade

All routes the facade exposes at `:8200`. These are the browser-callable `/v1/*` endpoints.
Every route requires an `Authorization: Bearer <token>` header unless marked "Public".

Upstream targets:

- **backend** → `BACKEND_URL` (default `:8100`)
- **ai_backend** → `AI_BACKEND_URL` (default `:8000`)
- **inline** → handled entirely in the facade, no upstream call

See also:

- [architecture/01-routing.md](../architecture/01-routing.md) — routing mechanics
- [architecture/02-auth-identity.md](../architecture/02-auth-identity.md) — bearer verification
- [backend reference/internal-api.md](../../../backend/docs/reference/internal-api.md) — what backend's internal routes look like

---

## Health

| Method | Path         | Auth   | Target | Notes                                                 |
| ------ | ------------ | ------ | ------ | ----------------------------------------------------- |
| `GET`  | `/v1/health` | Public | inline | `{service, deployment_profile, feature_toggles_hash}` |
| `GET`  | `/healthz`   | Public | inline | Liveness                                              |
| `GET`  | `/readyz`    | Public | inline | Readiness                                             |

---

## Session

| Method | Path          | Auth   | Target | Notes                                     |
| ------ | ------------- | ------ | ------ | ----------------------------------------- |
| `GET`  | `/v1/session` | Bearer | inline | Returns verified identity from the bearer |

---

## Auth (forwarded by `register_auth_routes`)

| Method     | Path                        | Auth   | Target  | Notes                                          |
| ---------- | --------------------------- | ------ | ------- | ---------------------------------------------- |
| `POST`     | `/v1/auth/login`            | Public | backend | Email+password login                           |
| `POST`     | `/v1/auth/logout`           | Bearer | backend | Revokes session; invalidates touch cache       |
| `GET`      | `/v1/auth/providers`        | Public | backend | Lists enabled IdPs                             |
| `GET/POST` | `/v1/auth/oidc/*`           | Mixed  | backend | OIDC authorize + callback                      |
| `GET/POST` | `/v1/auth/saml/*`           | Mixed  | backend | SAML authorize + ACS                           |
| `POST`     | `/v1/auth/magic-link/*`     | Mixed  | backend | Magic-link start + callback + workspace select |
| `POST`     | `/v1/auth/password-reset/*` | Public | backend | Password reset request + confirm               |

---

## Me (profile, forwarded by `register_me_routes`)

| Method    | Path                           | Auth   | Target  | Notes                        |
| --------- | ------------------------------ | ------ | ------- | ---------------------------- |
| `GET`     | `/v1/me/profile`               | Bearer | backend | Caller's display name, email |
| `PUT`     | `/v1/me/profile`               | Bearer | backend | Update display name          |
| `GET/PUT` | `/v1/me/avatar`                | Bearer | backend | Avatar image                 |
| `GET/PUT` | `/v1/me/preferences`           | Bearer | backend | User preferences             |
| `GET`     | `/v1/me/mfa`                   | Bearer | backend | MFA status                   |
| `GET`     | `/v1/me/sessions`              | Bearer | backend | Active sessions list         |
| `DELETE`  | `/v1/me/sessions/{session_id}` | Bearer | backend | Revoke a specific session    |

---

## MCP Registry

| Method   | Path                                     | Auth   | Target  | Notes                           |
| -------- | ---------------------------------------- | ------ | ------- | ------------------------------- |
| `POST`   | `/v1/mcp/servers`                        | Bearer | backend | Register custom server          |
| `GET`    | `/v1/mcp/servers`                        | Bearer | backend | List installed servers          |
| `PATCH`  | `/v1/mcp/servers/{server_id}`            | Bearer | backend | Update server                   |
| `DELETE` | `/v1/mcp/servers/{server_id}`            | Bearer | backend | Remove server; revokes tokens   |
| `GET`    | `/v1/mcp/catalog`                        | Bearer | backend | List curated catalog entries    |
| `POST`   | `/v1/mcp/servers/install`                | Bearer | backend | Install from catalog by slug    |
| `POST`   | `/v1/mcp/servers/{server_id}/auth/start` | Bearer | backend | Begin OAuth; returns `auth_url` |
| `POST`   | `/v1/mcp/servers/{server_id}/auth/skip`  | Bearer | backend | Mark auth not needed            |
| `GET`    | `/v1/mcp/oauth/callback`                 | Bearer | backend | OAuth code exchange callback    |

---

## Skills

| Method   | Path                    | Auth   | Target               | Notes                                                  |
| -------- | ----------------------- | ------ | -------------------- | ------------------------------------------------------ |
| `POST`   | `/v1/skills`            | Bearer | backend              | Create user skill                                      |
| `GET`    | `/v1/skills`            | Bearer | backend + ai_backend | Merged: system (ai-backend) + user/preloaded (backend) |
| `GET`    | `/v1/skills/{skill_id}` | Bearer | backend              | Get skill by ID                                        |
| `PUT`    | `/v1/skills/{skill_id}` | Bearer | backend              | Replace skill                                          |
| `DELETE` | `/v1/skills/{skill_id}` | Bearer | backend              | Delete skill                                           |

---

## API Keys

| Method   | Path                           | Auth   | Target  | Notes                                      |
| -------- | ------------------------------ | ------ | ------- | ------------------------------------------ |
| `POST`   | `/v1/api-keys`                 | Bearer | backend | Mint; returns plaintext once               |
| `GET`    | `/v1/api-keys`                 | Bearer | backend | List (no plaintext)                        |
| `DELETE` | `/v1/api-keys/{key_id}`        | Bearer | backend | Revoke                                     |
| `POST`   | `/v1/api-keys/{key_id}/rotate` | Bearer | backend | Rotate; revokes old, returns new plaintext |

---

## Conversations

| Method   | Path                                      | Auth   | Target     | Notes                             |
| -------- | ----------------------------------------- | ------ | ---------- | --------------------------------- |
| `POST`   | `/v1/agent/conversations`                 | Bearer | ai_backend | Create conversation               |
| `GET`    | `/v1/agent/conversations`                 | Bearer | ai_backend | List conversations                |
| `GET`    | `/v1/agent/conversations/{id}`            | Bearer | ai_backend | Get conversation                  |
| `PATCH`  | `/v1/agent/conversations/{id}`            | Bearer | ai_backend | Update title/folder/archived      |
| `DELETE` | `/v1/agent/conversations/{id}`            | Bearer | ai_backend | Soft-delete                       |
| `POST`   | `/v1/agent/conversations/{id}/restore`    | Bearer | ai_backend | Restore soft-deleted              |
| `GET`    | `/v1/agent/conversations/{id}/messages`   | Bearer | ai_backend | Message list                      |
| `GET`    | `/v1/agent/conversations/{id}/context`    | Bearer | ai_backend | Context window summary            |
| `PATCH`  | `/v1/agent/conversations/{id}/connectors` | Bearer | ai_backend | Per-chat connector scope override |
| `GET`    | `/v1/agent/conversations/{id}/subagents`  | Bearer | ai_backend | Subagent list                     |
| `GET`    | `/v1/agent/conversations/{id}/sources`    | Bearer | ai_backend | Source/citation list              |

---

## Runs and Events

| Method | Path                                         | Auth   | Target     | Notes                           |
| ------ | -------------------------------------------- | ------ | ---------- | ------------------------------- |
| `POST` | `/v1/agent/runs`                             | Bearer | ai_backend | Submit a run                    |
| `GET`  | `/v1/agent/runs/{run_id}`                    | Bearer | ai_backend | Run state                       |
| `GET`  | `/v1/agent/runs/{run_id}/events`             | Bearer | ai_backend | Event replay (JSON)             |
| `GET`  | `/v1/agent/runs/{run_id}/stream`             | Bearer | ai_backend | SSE event stream (live)         |
| `POST` | `/v1/agent/runs/{run_id}/cancel`             | Bearer | ai_backend | Request cancellation            |
| `POST` | `/v1/agent/approvals/{approval_id}/decision` | Bearer | ai_backend | Approve/reject a tool interrupt |

---

## Workspace Defaults and Data Lifecycle

| Method    | Path                           | Auth   | Target     | Notes                                      |
| --------- | ------------------------------ | ------ | ---------- | ------------------------------------------ |
| `GET/PUT` | `/v1/agent/workspace/defaults` | Bearer | ai_backend | Model + connectors + retention defaults    |
| `POST`    | `/v1/agent/workspace/export`   | Bearer | ai_backend | Request workspace data export (202)        |
| `DELETE`  | `/v1/agent/workspace/data`     | Bearer | ai_backend | Delete workspace data (always 501; audits) |
| `GET`     | `/v1/retention/effective`      | Bearer | ai_backend | Effective retention TTL                    |

---

## Drafts

| Method  | Path                                  | Auth   | Target     | Notes                          |
| ------- | ------------------------------------- | ------ | ---------- | ------------------------------ |
| `GET`   | `/v1/agent/conversations/{id}/drafts` | Bearer | ai_backend | List drafts in a conversation  |
| `GET`   | `/v1/agent/drafts/{draft_id}`         | Bearer | ai_backend | Get draft                      |
| `PATCH` | `/v1/agent/drafts/{draft_id}`         | Bearer | ai_backend | Update draft                   |
| `POST`  | `/v1/agent/drafts/{draft_id}/send`    | Bearer | ai_backend | Send draft (requires approval) |
| `POST`  | `/v1/agent/drafts/{draft_id}/discard` | Bearer | ai_backend | Discard draft                  |

---

## Shares

| Method   | Path                                     | Auth   | Target     | Notes                                  |
| -------- | ---------------------------------------- | ------ | ---------- | -------------------------------------- |
| `POST`   | `/v1/agent/conversations/{id}/share`     | Bearer | ai_backend | Create share link                      |
| `GET`    | `/v1/agent/conversations/{id}/shares`    | Bearer | ai_backend | List shares                            |
| `PATCH`  | `/v1/agent/shares/{share_id}`            | Bearer | ai_backend | Update share settings                  |
| `DELETE` | `/v1/agent/shares/{share_id}`            | Bearer | ai_backend | Revoke share                           |
| `GET`    | `/v1/agent/shares/{share_token}`         | Bearer | ai_backend | Get shared conversation                |
| `GET`    | `/v1/agent/shares/{share_token}/preview` | Bearer | ai_backend | Preview before fork                    |
| `POST`   | `/v1/agent/shares/{share_token}/fork`    | Bearer | ai_backend | Fork shared chat to caller's workspace |

---

## Models

| Method | Path               | Auth   | Target     | Notes                    |
| ------ | ------------------ | ------ | ---------- | ------------------------ |
| `GET`  | `/v1/agent/models` | Bearer | ai_backend | List available AI models |

---

## Agent History

| Method   | Path                | Auth   | Target     | Notes              |
| -------- | ------------------- | ------ | ---------- | ------------------ |
| `DELETE` | `/v1/agent/history` | Bearer | ai_backend | Delete run history |

---

## Usage

| Method | Path                           | Auth           | Target     | Notes                           |
| ------ | ------------------------------ | -------------- | ---------- | ------------------------------- |
| `GET`  | `/v1/usage/me`                 | Bearer         | ai_backend | Per-user usage summary          |
| `GET`  | `/v1/usage/me/conversations`   | Bearer         | ai_backend | Top conversations by token cost |
| `GET`  | `/v1/usage/runs/{run_id}`      | Bearer         | ai_backend | Per-run token usage             |
| `GET`  | `/v1/usage/conversations/{id}` | Bearer         | ai_backend | Per-conversation usage          |
| `GET`  | `/v1/usage/org`                | Bearer (admin) | ai_backend | Org-wide usage summary          |
| `GET`  | `/v1/usage/org/subagents`      | Bearer (admin) | ai_backend | Subagent usage breakdown        |
| `GET`  | `/v1/usage/org/purpose`        | Bearer (admin) | ai_backend | Usage by purpose                |

---

## Budgets

| Method   | Path                      | Auth   | Target     | Notes                 |
| -------- | ------------------------- | ------ | ---------- | --------------------- |
| `GET`    | `/v1/budgets`             | Bearer | ai_backend | List org budgets      |
| `POST`   | `/v1/budgets`             | Bearer | ai_backend | Create budget         |
| `GET`    | `/v1/budgets/me`          | Bearer | ai_backend | Caller's budget state |
| `PATCH`  | `/v1/budgets/{budget_id}` | Bearer | ai_backend | Update budget         |
| `DELETE` | `/v1/budgets/{budget_id}` | Bearer | ai_backend | Delete budget         |

---

## Audit (forwarded by `register_audit_routes`)

| Method | Path        | Auth                | Target  | Notes                  |
| ------ | ----------- | ------------------- | ------- | ---------------------- |
| `GET`  | `/v1/audit` | Bearer (audit:read) | backend | Cursor-paged audit log |

---

## SCIM (forwarded by `register_scim_routes`)

| Method                 | Path                   | Auth        | Target  | Notes                   |
| ---------------------- | ---------------------- | ----------- | ------- | ----------------------- |
| `GET/POST`             | `/scim/v2/Users`       | SCIM bearer | backend | SCIM User provisioning  |
| `GET/PUT/PATCH/DELETE` | `/scim/v2/Users/{id}`  | SCIM bearer | backend | SCIM User detail        |
| `GET/POST`             | `/scim/v2/Groups`      | SCIM bearer | backend | SCIM Group provisioning |
| `GET/PUT/PATCH/DELETE` | `/scim/v2/Groups/{id}` | SCIM bearer | backend | SCIM Group detail       |

---

## Workspace Admin (forwarded by `register_workspace_routes`)

| Method    | Path                          | Auth                 | Target  | Notes                    |
| --------- | ----------------------------- | -------------------- | ------- | ------------------------ |
| `GET/PUT` | `/v1/workspace`               | Bearer               | backend | Org-level settings       |
| `GET/PUT` | `/v1/workspace/mfa-policy`    | Bearer (admin:users) | backend | MFA policy               |
| `GET/PUT` | `/v1/workspace/providers`     | Bearer (admin)       | backend | Auth provider config     |
| `GET/PUT` | `/v1/workspace/notifications` | Bearer               | backend | Notification prefs       |
| `GET/PUT` | `/v1/workspace/privacy`       | Bearer               | backend | Privacy / data residency |

---

## Telemetry

| Method | Path                           | Auth   | Target                    | Notes                                                     |
| ------ | ------------------------------ | ------ | ------------------------- | --------------------------------------------------------- |
| `POST` | `/v1/telemetry/otlp/v1/traces` | Bearer | `OTEL_COLLECTOR_HTTP_URL` | Browser OTEL trace relay; 204 if collector not configured |

---

## Dev IdP (development only)

Registered only when `FACADE_ENVIRONMENT=development` and `dev_auth_bypass_allowed=True`.

| Method | Path                    | Auth   | Target  | Notes                |
| ------ | ----------------------- | ------ | ------- | -------------------- |
| `GET`  | `/v1/dev/personas`      | Public | backend | List dev personas    |
| `POST` | `/v1/dev/identity/mint` | Public | backend | Mint a signed bearer |
