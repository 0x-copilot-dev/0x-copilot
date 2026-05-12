# Backend — Knowledge Base

Agent-first documentation for `services/backend`. Every node answers one question and links
to adjacent nodes. Read this file first; all other paths branch from here.

## What this service does

`backend` is the core product backend. It owns:

- MCP server registry (registration, OAuth, token vault)
- Skill registry (user and preloaded skills)
- Identity and authentication (sessions, OIDC, SAML, local passwords, SCIM, MFA, lockouts)
- API keys
- Audit events (four append-only chains)
- User profiles, preferences, avatars
- Policies (tool-use, MFA, privacy, workspace settings)
- Notification preferences
- SIEM export

It does **not** own AI orchestration, conversation state, run events, or billing UI —
those live in `ai-backend`. It exposes `/internal/v1/*` routes consumed only by
`ai-backend`; public `/v1/*` routes go through `backend-facade` at `:8200`.

## Navigation

| Question                                                 | Read                                                                         |
| -------------------------------------------------------- | ---------------------------------------------------------------------------- |
| How is the code organised? What does each module own?    | [architecture/00-system-map.md](architecture/00-system-map.md)               |
| How does a request travel from browser to handler?       | [architecture/01-request-lifecycle.md](architecture/01-request-lifecycle.md) |
| What Pydantic shapes and domain records exist?           | [architecture/02-contracts.md](architecture/02-contracts.md)                 |
| How do in-memory and Postgres stores differ?             | [architecture/03-stores.md](architecture/03-stores.md)                       |
| How does MCP server registration and OAuth work?         | [features/mcp-registry.md](features/mcp-registry.md)                         |
| How does the skill registry work?                        | [features/skills.md](features/skills.md)                                     |
| How do sessions, OIDC, SAML, passwords, and MFA work?    | [features/identity-auth.md](features/identity-auth.md)                       |
| How do API keys work?                                    | [features/api-keys.md](features/api-keys.md)                                 |
| How do audit events and SIEM export work?                | [features/audit.md](features/audit.md)                                       |
| How do tool-use policies, privacy, and MFA policy work?  | [features/policies.md](features/policies.md)                                 |
| How do notification preferences work?                    | [features/notifications.md](features/notifications.md)                       |
| How does RBAC enforcement work? What are all the scopes? | [features/identity-auth.md#rbac-a10](features/identity-auth.md)              |
| How do I add a new auth provider?                        | [guides/add-auth-provider.md](guides/add-auth-provider.md)                   |
| How do I add a new MCP catalog entry?                    | [guides/add-mcp-catalog-entry.md](guides/add-mcp-catalog-entry.md)           |
| All public `/v1/*` endpoints                             | [reference/public-api.md](reference/public-api.md)                           |
| All internal `/internal/v1/*` endpoints                  | [reference/internal-api.md](reference/internal-api.md)                       |
| Every environment variable                               | [reference/env-vars.md](reference/env-vars.md)                               |
