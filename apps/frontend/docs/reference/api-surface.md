# API surface

Every `/v1/*` route the frontend calls, with the helper module that owns it.
Every route lands on **`backend-facade:8200`** — there are no direct
`backend:8100` or `ai-backend:8000` calls from this app.

See also:

- [../architecture/01-network-layer.md](../architecture/01-network-layer.md) —
  how every helper builds its headers + query
- The facade's surface doc:
  `services/backend-facade/docs/reference/api-surface.md`

Source: `src/api/*`

---

## Auth & identity

| Method | Route                               | Helper                              |
| ------ | ----------------------------------- | ----------------------------------- |
| POST   | `/v1/auth/discover`                 | `authApi.discoverWorkspaces`        |
| POST   | `/v1/auth/login`                    | `authApi.loginWithPassword`         |
| GET    | `/v1/auth/session`                  | `authApi.fetchCurrentSession`       |
| POST   | `/v1/auth/logout`                   | `authApi.logout`                    |
| POST   | `/v1/auth/sessions/select`          | `authApi.selectWorkspace`           |
| POST   | `/v1/auth/sessions`                 | (future) `authApi.rotateSession`    |
| POST   | `/v1/auth/magic-link/start`         | `authApi.startMagicLink`            |
| POST   | `/v1/auth/magic-link/callback`      | `authApi.consumeMagicLink`          |
| GET    | `/v1/auth/mfa/factors`              | `authApi.listMfaFactors` (admin/me) |
| POST   | `/v1/auth/mfa/factors/totp/enroll`  | `authApi.enrollTotp`                |
| POST   | `/v1/auth/mfa/factors/totp/confirm` | `authApi.confirmTotp`               |
| POST   | `/v1/auth/mfa/recovery/consume`     | `authApi.consumeRecoveryCode`       |
| POST   | `/v1/auth/mfa/challenge`            | `authApi.startMfaChallenge`         |
| POST   | `/v1/auth/mfa/verify`               | `authApi.verifyMfa`                 |

Dev-only (registered when `BACKEND_ENVIRONMENT=development`):

| Method | Route                   | Helper                   |
| ------ | ----------------------- | ------------------------ |
| GET    | `/v1/dev/personas`      | `devIdp.listDevPersonas` |
| POST   | `/v1/dev/identity/mint` | `devIdp.mintDevBearer`   |

---

## Profile, preferences, policies

| Method | Route                                         | Helper                              |
| ------ | --------------------------------------------- | ----------------------------------- |
| GET    | `/v1/me/profile`                              | `meApi.fetchMyProfile`              |
| PATCH  | `/v1/me/profile`                              | `meApi.updateMyProfile`             |
| GET    | `/v1/me/preferences`                          | `meApi.fetchMyPreferences`          |
| PATCH  | `/v1/me/preferences`                          | `meApi.updateMyPreferences`         |
| GET    | `/v1/me/policies/privacy`                     | `meApi.fetchPrivacyPolicy`          |
| GET    | `/v1/me/policies/tool-use`                    | `meApi.fetchToolUsePolicy`          |
| GET    | `/v1/me/notifications`                        | `meApi.fetchNotifications`          |
| PATCH  | `/v1/me/notifications`                        | `meApi.updateNotifications`         |
| GET    | `/v1/me/workspaces`                           | `meApi.listMyWorkspaces`            |
| GET    | `/v1/me/api-keys`                             | `meApi.listMyApiKeys`               |
| POST   | `/v1/me/api-keys`                             | `meApi.createMyApiKey`              |
| DELETE | `/v1/me/api-keys/{id}`                        | `meApi.revokeMyApiKey`              |
| POST   | `/v1/me/avatar` (multipart)                   | `avatarApi.uploadAvatar`            |
| GET    | `/v1/me/mfa/factors`                          | `mfaApi.listMfaFactors`             |
| POST   | `/v1/me/mfa/factors/totp/enroll`              | `mfaApi.enrollTotp`                 |
| POST   | `/v1/me/mfa/factors/totp/confirm`             | `mfaApi.confirmTotp`                |
| POST   | `/v1/me/mfa/factors/webauthn/register/start`  | `mfaApi.beginWebAuthnRegistration`  |
| POST   | `/v1/me/mfa/factors/webauthn/register/finish` | `mfaApi.finishWebAuthnRegistration` |

---

## Workspace admin

| Method | Route                            | Helper                            |
| ------ | -------------------------------- | --------------------------------- |
| GET    | `/v1/workspace`                  | `workspaceApi.fetchWorkspace`     |
| PATCH  | `/v1/workspace`                  | `workspaceApi.updateWorkspace`    |
| GET    | `/v1/workspace/billing`          | `workspaceApi.fetchBilling`       |
| GET    | `/v1/workspace/members`          | `workspaceApi.listMembers`        |
| PATCH  | `/v1/workspace/members/{id}`     | `workspaceApi.updateMember`       |
| DELETE | `/v1/workspace/members/{id}`     | `workspaceApi.removeMember`       |
| GET    | `/v1/workspace/invitations`      | `workspaceApi.listInvitations`    |
| POST   | `/v1/workspace/invitations`      | `workspaceApi.inviteMember`       |
| DELETE | `/v1/workspace/invitations/{id}` | `workspaceApi.revokeInvitation`   |
| GET    | `/v1/workspace/api-keys`         | `meApi.listWorkspaceApiKeys`      |
| GET    | `/v1/workspace/mfa-policy`       | `workspaceMfaApi.fetchMfaPolicy`  |
| PATCH  | `/v1/workspace/mfa-policy`       | `workspaceMfaApi.updateMfaPolicy` |

---

## MCP (connectors)

| Method | Route                                   | Helper                    |
| ------ | --------------------------------------- | ------------------------- |
| GET    | `/v1/mcp/catalog`                       | `mcpApi.listMcpCatalog`   |
| GET    | `/v1/mcp/servers`                       | `mcpApi.listMcpServers`   |
| POST   | `/v1/mcp/servers`                       | `mcpApi.createMcpServer`  |
| POST   | `/v1/mcp/servers/install`               | `mcpApi.installMcpServer` |
| PATCH  | `/v1/mcp/servers/{id}`                  | `mcpApi.updateMcpServer`  |
| DELETE | `/v1/mcp/servers/{id}`                  | `mcpApi.deleteMcpServer`  |
| POST   | `/v1/mcp/servers/{id}/auth/start`       | `mcpApi.startMcpAuth`     |
| POST   | `/v1/mcp/servers/{id}/auth/skip`        | `mcpApi.skipMcpAuth`      |
| GET    | `/v1/mcp/oauth/callback?state=…&code=…` | `mcpApi.completeMcpOAuth` |

---

## Agent runtime

| Method | Route                                         | Helper                                       |
| ------ | --------------------------------------------- | -------------------------------------------- |
| GET    | `/v1/agent/conversations`                     | `agentApi.listConversations`                 |
| POST   | `/v1/agent/conversations`                     | `agentApi.createConversation`                |
| GET    | `/v1/agent/conversations/{id}`                | `agentApi.getConversation`                   |
| PATCH  | `/v1/agent/conversations/{id}`                | `agentApi.updateConversation`                |
| DELETE | `/v1/agent/conversations/{id}`                | `agentApi.deleteConversation`                |
| GET    | `/v1/agent/conversations/{id}/messages`       | `agentApi.listMessages`                      |
| GET    | `/v1/agent/conversations/{id}/context`        | `agentApi.getConversationContext`            |
| GET    | `/v1/agent/conversations/{id}/subagents`      | `agentApi.listSubagents`                     |
| GET    | `/v1/agent/conversations/{id}/sources`        | `agentApi.listSources`                       |
| GET    | `/v1/agent/conversations/{id}/connectors`     | `agentApi.getConversationConnectorScopes`    |
| PATCH  | `/v1/agent/conversations/{id}/connectors`     | `agentApi.updateConversationConnectorScopes` |
| POST   | `/v1/agent/conversations/{id}/fork`           | `agentApi.forkConversationFromMessage`       |
| GET    | `/v1/agent/conversations/{id}/drafts`         | `agentApi.listDrafts`                        |
| GET    | `/v1/agent/drafts/{id}`                       | `agentApi.getDraft`                          |
| PATCH  | `/v1/agent/drafts/{id}`                       | `agentApi.patchDraft`                        |
| POST   | `/v1/agent/drafts/{id}/send`                  | `agentApi.sendDraft`                         |
| POST   | `/v1/agent/drafts/{id}/discard`               | `agentApi.discardDraft`                      |
| POST   | `/v1/agent/runs`                              | `agentApi.createRun`                         |
| POST   | `/v1/agent/runs/{id}/cancel`                  | `agentApi.cancelRun`                         |
| GET    | `/v1/agent/runs/{id}/events?after_sequence=N` | `agentApi.replayRunEvents`                   |
| GET    | `/v1/agent/runs/{id}/stream?after_sequence=N` | `agentApi.streamRunEvents` (SSE)             |
| GET    | `/v1/agent/approvals`                         | `agentApi.listAssignedApprovals`             |
| POST   | `/v1/agent/approvals/{id}/decision`           | `agentApi.decideApproval`                    |
| POST   | `/v1/agent/approvals/{id}/undo`               | `agentApi.requestApprovalUndo`               |
| GET    | `/v1/agent/me/inbox/stream?after_sequence=N`  | `agentApi.streamInboxEvents` (SSE)           |
| GET    | `/v1/agent/models`                            | `agentApi.listModels`                        |
| POST   | `/v1/agent/models/select`                     | `agentApi.selectModel`                       |
| GET    | `/v1/agent/workspace/data`                    | `agentApi.workspaceData`                     |
| GET    | `/v1/agent/workspace/defaults`                | `agentApi.fetchWorkspaceDefaults`            |
| PATCH  | `/v1/agent/workspace/defaults`                | `agentApi.updateWorkspaceDefaults`           |
| POST   | `/v1/agent/workspace/export`                  | `agentApi.exportWorkspace`                   |
| POST   | `/v1/agent/shares`                            | `agentApi.createShare`                       |
| GET    | `/v1/agent/shares`                            | `agentApi.listShares`                        |
| PATCH  | `/v1/agent/shares/{token}`                    | `agentApi.updateShare`                       |
| DELETE | `/v1/agent/shares/{token}`                    | `agentApi.revokeShare`                       |
| GET    | `/v1/agent/shares/{token}/view`               | `agentApi.fetchSharedView`                   |
| POST   | `/v1/agent/shares/{token}/fork`               | `agentApi.forkShare`                         |

---

## Audit, usage, retention, skills, telemetry

| Method | Route                          | Helper                                                                        |
| ------ | ------------------------------ | ----------------------------------------------------------------------------- |
| GET    | `/v1/audit`                    | `auditApi.listAuditEvents` (composite merge — see facade)                     |
| GET    | `/v1/usage/me`                 | `agentApi.fetchUsageMe`                                                       |
| GET    | `/v1/usage/me/conversations`   | `agentApi.fetchUsageMeConversations`                                          |
| GET    | `/v1/usage/org`                | `agentApi.fetchUsageOrg`                                                      |
| GET    | `/v1/budgets/me`               | `agentApi.fetchBudgetMe`                                                      |
| GET    | `/v1/retention/effective`      | `agentApi.fetchRetentionEffective`                                            |
| GET    | `/v1/skills`                   | `skillsApi.listSkills` (merged backend + ai-backend by facade)                |
| POST   | `/v1/telemetry/otlp/v1/traces` | OTLP exporter (see [features/observability.md](../features/observability.md)) |

---

## Hard rules

- Add a new helper module under `src/api/*` when a new route family
  appears. Do **not** spread route strings across feature files.
- Update [`packages/api-types`](../../../../packages/api-types) in the same
  change when a request or response shape changes — the facade's surface
  **is** the public contract.
- Never call `:8100` or `:8000` directly. The dev proxy (`vite.config.ts`)
  and the prod ingress both route `/v1/*` to the facade.
