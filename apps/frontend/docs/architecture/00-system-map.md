# System map

How the frontend is organised. Read this before adding a new folder or
moving a module across boundaries.

See also:

- [01-network-layer.md](01-network-layer.md) — how every API client attaches
  the bearer and a request id
- [02-auth-state.md](02-auth-state.md) — `<AuthProvider>` wires the bearer
  into the HTTP layer
- [04-streaming.md](04-streaming.md) — the chat surface drives runtime SSE
  through `src/api/agentApi.ts`

---

## Top-level layout

```
apps/frontend/
├── index.html                  # Vite entry
├── vite.config.ts              # dev proxy → 127.0.0.1:8200
├── nginx.conf                  # prod static-SPA only; no /v1 proxy
├── Dockerfile
├── src/
│   ├── main.tsx                # ReactDOM root + telemetry bootstrap
│   ├── app/                    # AppGate + route reducer + history wiring
│   ├── api/                    # HTTP + SSE clients (the only place)
│   ├── features/               # screens, hooks, chat reducers
│   ├── observability/          # OTEL + ErrorBoundary + global error handlers
│   ├── utils/                  # tiny shared primitives (useLocalStorageState,
│   │                           #   useViewportOverlay, etc.)
│   ├── test/                   # test helpers
│   └── styles.css              # global styles (assistant-markdown rules live here)
└── docs/                       # this knowledge base
```

---

## `src/app/` — the gate

| File                  | Owns                                                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `App.tsx`             | `<ThemeProvider>` → `<AuthProvider>` → `<AuthGate>` → `<EnterpriseSearchApp>`. Owns the route reducer (`routeFromLocation`, `applyAppRoute`). |
| `keymap.ts` / `.test` | Global keyboard map (Cmd-K, Cmd-/, Esc, etc.) registered via `tinykeys`.                                                                      |

`<AuthGate>` is what makes the rest of the app safe to assume `identity` is
non-null. `initial` / `loading` → spinner. `mfa_pending` → `<MfaPrompt>`.
`anonymous` / `error` / `workspace_pick` / magic-link callback URL →
`<LoginScreen>`. Only `authenticated` renders `<EnterpriseSearchApp>`.

---

## `src/api/` — the only HTTP layer

| File                 | Surface                                                                                                                                                                           |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `http.ts`            | `httpGet/Post/Patch/Put/Delete`, `correlationHeaders`, `assertOk`, bearer provider, 401 callback                                                                                  |
| `config.ts`          | `RequestIdentity` + `identityParams` (turns `{ orgId, userId }` into `?org_id=…&user_id=…`)                                                                                       |
| `agentApi.ts`        | Conversations, messages, runs, drafts, approvals, sources, subagents, shares, usage, retention, SSE streams (`/v1/agent/*` + `/v1/usage/*` + `/v1/budgets/*` + `/v1/retention/*`) |
| `authApi.ts`         | `/v1/auth/*` — discover, login, session, logout, MFA, magic-link, workspace pick                                                                                                  |
| `mcpApi.ts`          | `/v1/mcp/*` — servers, catalog, install, auth start/skip, OAuth callback                                                                                                          |
| `mcpErrors.ts`       | Shared 401/403 → `isOAuthSetupRequired` classifier                                                                                                                                |
| `mfaApi.ts`          | `/v1/me/mfa/factors*` — TOTP enroll/confirm, WebAuthn register, recovery codes                                                                                                    |
| `meApi.ts`           | `/v1/me/*` + `/v1/workspace/api-keys` — profile, preferences, policies, notifications, api keys, workspaces                                                                       |
| `avatarApi.ts`       | `/v1/me/avatar` — multipart upload                                                                                                                                                |
| `workspaceApi.ts`    | `/v1/workspace*` — billing, invitations, members                                                                                                                                  |
| `workspaceMfaApi.ts` | `/v1/workspace/mfa-policy`                                                                                                                                                        |
| `skillsApi.ts`       | `/v1/skills`                                                                                                                                                                      |
| `auditApi.ts`        | `/v1/audit` (composite merge — facade owns the merge logic)                                                                                                                       |
| `useResource.ts`     | Generic async-state hook for `useEffect`-based fetches                                                                                                                            |

Add a new module here when a new `/v1/*` family becomes part of the UI surface.
Do **not** add new feature callers to legacy root-level helper files —
features import from `src/api/*` only.

---

## `src/features/` — screens and reducers

```
src/features/
├── auth/              # AuthContext + login screen + MFA prompt + dev IdP
├── chat/              # ChatScreen + chatModel/ reducers + runtime/ + components/
│   ├── chatModel/     # pure event-projection reducers (see chatModel/README.md)
│   ├── runtime/       # assistant-ui adapters (composer, attachments, dictation)
│   ├── components/    # thread / messages / tools / workspace pane / sidebar / shell
│   ├── approval/      # approval focus context, approval cards
│   ├── prompts/       # ambient prompt suggestions
│   ├── sidebar/       # left rail
│   ├── utils/         # chat-only helpers
│   ├── ChatScreen.tsx, chatModel.ts, chatRunState.ts, depth.ts, mcpAuthAction.ts, …
├── connectors/        # MCP server list, popover, OAuth consent cards
├── settings/          # settings screen + sections (profile, connectors, skills, billing, members, audit, mfa)
├── share/             # ShareScreen (recipient view), SharePopover (creator view)
├── skills/            # useSkills hook + skill rendering
├── sources/           # source list rendering
├── workspace/         # MentionLabel + workspace member hook
└── me/                # profile + preferences + theme sync
```

Boundaries:

- **Chat reducers** live in `src/features/chat/chatModel/`. They take
  `RuntimeEventEnvelope` events from the SSE stream and return new state.
  Nothing in this folder reads React, the network, or `Date.now()`.
- **`runtime/`** is the assistant-ui glue (composer adapters, attachment
  adapters, dictation). Keep React-only state here, not in `chatModel/`.
- **Connectors / settings** own their own hooks; both call `mcpApi`.

---

## `src/observability/`

| File                     | Owns                                                                        |
| ------------------------ | --------------------------------------------------------------------------- |
| `otel.ts`                | `bootstrapTelemetry`, `appTracer`, `SafeAttributeSpanProcessor` (allowlist) |
| `globalErrorHandlers.ts` | `installGlobalErrorHandlers`, extension-vs-app classifier                   |
| `ErrorBoundary.tsx`      | React error boundary; emits one safe OTEL span                              |

See [features/observability.md](../features/observability.md) for the safe-attribute contract.

---

## Shared workspace packages

- `@enterprise-search/api-types` — public payload shapes. Update **here**
  (in `packages/api-types`) when a route's request/response changes.
- `@enterprise-search/design-system` — reusable UI primitives + tokens.
  Feature workflows stay in this app; only stable, reusable primitives
  graduate.

Do **not** import anything from `services/*`. Cross-service contact is
HTTP via the facade.
