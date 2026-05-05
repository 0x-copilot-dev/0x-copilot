# 06. MCP server installed but not authenticated, user invokes it

> Status: documented · Layers: fe / facade / ai-backend / backend / db / token vault · Related: 02, 04, 05

## Trigger

The assistant calls `auth_mcp` (or invokes `call_mcp_tool` against an MCP server) whose registry record's `auth_state` is one of `unauthenticated`, `auth_pending`, or `auth_failed`. The runtime parks the run on a `MCP_AUTH_REQUIRED` interrupt; the user must finish an OAuth round-trip in a separate window before the run can resume.

## Preconditions

- Server registered with `auth_mode = oauth2`, `enabled = true`, and effective `auth_state ∉ {authenticated, auth_skipped}`.
- A run is in flight; the LangGraph node has reached `auth_mcp` directly or because `BackendMcpClient.connect` raised `McpAuthError`.
- `ENTERPRISE_SERVICE_TOKEN` is configured so ai-backend can call backend `/internal/v1/*`.
- The OAuth callback returns to the same origin; the SPA recovers the parked run via `sessionStorage` + `useConnectors`.

## Sequence diagram — auth interrupt

```mermaid
sequenceDiagram
    participant Worker as runtime_worker (LangGraph)
    participant Tool as auth_mcp tool
    participant AIProv as BackendMcpProvider
    participant Backend as backend (MCP registry)
    participant Vault as TokenVault
    participant Store as ai-backend event_store
    participant FE as Browser (ChatScreen)

    Worker->>Tool: ainvoke({server_id|server_name})
    Tool->>AIProv: create_auth_session(server_id, runtime_context)
    AIProv->>Backend: POST /internal/v1/mcp/servers/{server_id}/auth/start
    Backend->>Backend: start_auth — generate PKCE verifier+challenge, run OIDC discovery / DCR
    Backend->>Vault: persist auth session (state + code_verifier)
    Backend->>Backend: auth_state → auth_pending; audit mcp_auth_started
    Backend-->>AIProv: McpAuthStartResponse {auth_url, expires_at}
    AIProv-->>Tool: McpAuthSession
    Tool->>Worker: langgraph_interrupt(payload)  — graph parks
    Worker->>Store: append MCP_AUTH_REQUIRED; run.status → WAITING_FOR_APPROVAL
    Store-->>FE: SSE MCP_AUTH_REQUIRED
    FE->>FE: upsertMcpAuthPart — assistant message status = requires-action
```

## Sequence diagram — OAuth round-trip + resume

```mermaid
sequenceDiagram
    actor User
    participant FE as Browser (ChatScreen)
    participant SS as sessionStorage
    participant Conn as useConnectors
    participant Facade as backend-facade
    participant Backend as backend (MCP registry)
    participant Vault as TokenVault
    participant IdP as OAuth Provider
    participant AI as ai-backend

    User->>FE: Click "Connect"
    FE->>SS: rememberPendingMcpAuthAction({approvalId, serverId, runId})
    FE->>Conn: connectors.authenticate(serverId)
    Conn->>Facade: POST /v1/mcp/servers/{server_id}/auth/start
    Facade->>Backend: forward
    Backend-->>FE: {auth_url, expires_at}  (fresh PKCE state)
    FE->>IdP: window.location = auth_url
    User->>IdP: consent
    IdP->>FE: redirect → /v1/mcp/oauth/callback?state=…&code=…
    FE->>Facade: GET callback
    Facade->>Backend: forward
    Backend->>Backend: complete_auth — pop_auth_session(state), validate ttl
    Backend->>IdP: exchange code+verifier for tokens
    Backend->>Vault: encrypt(access+refresh) → put_token
    Backend->>Backend: auth_state → AUTHENTICATED; audit mcp_auth_completed
    Backend-->>FE: McpServerResponse
    FE->>FE: completedMcpAuthAction effect — replayRunEvents(runId)
    FE->>AI: GET /v1/agent/runs/{run_id}/events
    AI-->>FE: events (incl. parked MCP_AUTH_REQUIRED)
    FE->>FE: resolveAuthenticatedMcpServers — auto-approve parked card
    FE->>AI: SSE /stream?after_sequence — resume; worker continues parked node
```

## McpAuthState transitions

| from                             | event                              | to                                       | source                                                                            |
| -------------------------------- | ---------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------- |
| `unauthenticated`                | `start_auth` (oauth2)              | `auth_pending`                           | [service.py:308-312](../../services/backend/src/backend_app/service.py#L308-L312) |
| `auth_pending`                   | OAuth callback success             | `authenticated`                          | [service.py:368-371](../../services/backend/src/backend_app/service.py#L368-L371) |
| `auth_pending`                   | callback `error` param             | `auth_failed`                            | [service.py:337-344](../../services/backend/src/backend_app/service.py#L337-L344) |
| `auth_failed`                    | user retries `start_auth`          | `auth_pending`                           | [service.py:267-326](../../services/backend/src/backend_app/service.py#L267-L326) |
| any                              | `start_auth` against non-OAuth2    | `auth_unsupported` (raises 400)          | [service.py:278-286](../../services/backend/src/backend_app/service.py#L278-L286) |
| any                              | user clicks **Skip** → `skip_auth` | `auth_skipped`                           | [service.py:254-265](../../services/backend/src/backend_app/service.py#L254-L265) |
| `authenticated` (existing token) | next `start_auth`                  | re-confirms `authenticated` (idempotent) | [service.py:308-312](../../services/backend/src/backend_app/service.py#L308-L312) |

`McpAuthState` enum: [contracts.py:160-166](../../services/backend/src/backend_app/contracts.py#L160-L166).

## Function trace

### Auth interrupt (ai-backend → backend)

1. `BackendMcpClient.connect` — [backend_provider.py:130-156](../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py#L130-L156) — requires `card.auth_state ∈ {AUTHENTICATED, AUTH_SKIPPED}`, else raises `McpAuthError`. Middleware exposes `auth_mcp` for recovery.
2. `AuthMcpTool.ainvoke` — [middleware/auth_mcp.py:53-86](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py#L53-L86) — calls `BackendMcpProvider.create_auth_session` ([backend_provider.py:74-100](../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py#L74-L100)) which POSTs `/internal/v1/mcp/servers/{server_id}/auth/start` with service-auth headers, then builds the interrupt payload (`approval_id = mcp_auth:{run_id}:{server_id}`, `approval_kind`, `server_id`, `server_name`, `display_name`, `auth_url`, `expires_at`, `message`) and calls `langgraph_interrupt(payload)`. Resume values are decoded by `_resume_result`.
3. `RuntimeRunHandler` projection — [run.py:63-219](../../services/ai-backend/src/runtime_worker/handlers/run.py#L63-L219) — `stream_event_mapper.append_native_interrupt_events` projects the interrupt into a persisted `MCP_AUTH_REQUIRED` event, then `update_run_status(WAITING_FOR_APPROVAL)`.
4. Event projector — [events.py:421-439](../../services/ai-backend/src/runtime_api/schemas/events.py#L421-L439) — strips the persisted payload to the safe client surface (`approval_id`, `action_id`, `approval_kind`, `server_id`, `server_name`, `display_name`, `auth_url`, `expires_at`, `message`, `status`, `source_tool_call_id`); `activity_kind = mcp_auth`.
5. Facade `start_mcp_auth` — [backend_facade/app.py:196-207](../../services/backend-facade/src/backend_facade/app.py#L196-L207) — authenticates and forwards with `identity.scoped_payload(payload)`. Backend `start_auth` route — [backend/app.py:351-368](../../services/backend/src/backend_app/app.py#L351-L368) — re-validates identity.
6. `McpRegistryService.start_auth` — [service.py:267-326](../../services/backend/src/backend_app/service.py#L267-L326) — rejects non-OAuth2 (`AUTH_UNSUPPORTED`); generates a fresh PKCE verifier ([\_pkce.py:28-35](../../services/backend/src/backend_app/identity/_pkce.py#L28-L35)); persists `McpAuthSessionRecord` with `state`, `code_verifier`, `redirect_uri`, `expires_at = now + auth_session_ttl` (15 min default); `oauth_client.authorization(...)` runs OIDC discovery + Dynamic Client Registration when supported, else uses pre-registered `client_id`/`client_secret`/`scope`/`authorization_endpoint`/`token_endpoint`. `code_challenge = compute_challenge(verifier)` (S256, [\_pkce.py:38-42](../../services/backend/src/backend_app/identity/_pkce.py#L38-L42)). State advances to `AUTH_PENDING`. Audits `mcp_auth_started`.

### Frontend Connect → OAuth → Callback

7. Event rendering — [eventReducer.ts:51-56](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L51-L56) routes `MCP_AUTH_REQUIRED` to `upsertMcpAuthPart` ([contentBuilders.ts:132-148](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L132-L148)) → `mcpAuthPart` ([partFactories.ts:202-218](../../apps/frontend/src/features/chat/chatModel/partFactories.ts#L202-L218)) which builds a tool-call part with `toolName="mcp_auth_required"` and `toolCallId = approval_id`. Message status → `{ type: "requires-action", reason: "interrupt" }`.
8. `onMcpAuthConnect` — [ChatScreen.tsx:606-630](../../apps/frontend/src/features/chat/ChatScreen.tsx#L606-L630) — calls `rememberPendingMcpAuthAction({approvalId, serverId})` ([mcpAuthAction.ts:14-29](../../apps/frontend/src/features/chat/mcpAuthAction.ts#L14-L29)) which writes `{approvalId, serverId, runId, createdAt}` to `sessionStorage`; `runIdFromMcpAuthApprovalId` ([mcpAuthAction.ts:55-60](../../apps/frontend/src/features/chat/mcpAuthAction.ts#L55-L60)) parses the `mcp_auth:{run_id}:{server_id}` shape. Then `connectors.authenticate(serverId)` POSTs facade `start_mcp_auth` and navigates to `auth_url`.
9. Facade `mcp_oauth_callback` — [backend_facade/app.py:223-245](../../services/backend-facade/src/backend_facade/app.py#L223-L245) — IdP redirect lands here; forwards `state`/`code`/`error` to backend. Returns JSON; the SPA owns navigation back into chat. Backend route — [backend/app.py:387-404](../../services/backend/src/backend_app/app.py#L387-L404) — builds `McpAuthCallbackRequest` ([contracts.py:587-609](../../services/backend/src/backend_app/contracts.py#L587-L609)) which enforces "code OR error".
10. `McpRegistryService.complete_auth` — [service.py:328-372](../../services/backend/src/backend_app/service.py#L328-L372) — `pop_auth_session(state=…)` (single-use), validates non-expiry; on IdP error → `AUTH_FAILED` + `mcp_auth_failed` audit; else `token_exchanger.exchange_code(record, session, code, token_vault)`. Tokens encrypted via the configured TokenVault adapter (see [token_vault.py](../../services/backend/src/backend_app/token_vault.py)) before `put_token`. `auth_state → AUTHENTICATED`, audit `mcp_auth_completed`.

### Resume / skip

11. `completedMcpAuthAction` effect — [ChatScreen.tsx:303-378](../../apps/frontend/src/features/chat/ChatScreen.tsx#L303-L378) — when `useConnectors` surfaces a `CompletedMcpAuthAction`, sets status `Resuming after connector auth…`, calls `replayRunEvents(runId, identity)` ([agentApi.ts:227-237](../../apps/frontend/src/api/agentApi.ts#L227-L237)), replays events through `applyRuntimeEvent`, then `startEventStream(runId, latestSequence)` (or closes if terminal).
12. `resolveAuthenticatedMcpServers` — [chatModel/mcpAuth.ts:19-57](../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts#L19-L57) — for connectors with `auth_state == "authenticated"`, mutates parked parts' `args.status → "approved"`, synthesizes `result: {decision: "approved"}`, and flips the message status from `requires-action` back to `running` if no other pending action remains. `removeRedundantMcpAuthWrappers` ([chatModel/mcpAuth.ts:59-79](../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts#L59-L79)) drops the underlying `auth_mcp` wrapper part.
13. Worker resume — once `auth_state = authenticated`, the next `BackendMcpClient.connect` succeeds. The parked interrupt unwinds when `onMcpAuthDecision` ([ChatScreen.tsx:657-682](../../apps/frontend/src/features/chat/ChatScreen.tsx#L657-L682)) calls `decideApproval(approvalId, "approved", …, "mcp_auth_resolved")`, enqueuing an `APPROVAL_RESOLVED` command the worker consumes.
14. Skip — `onMcpAuthSkip` ([ChatScreen.tsx:632-655](../../apps/frontend/src/features/chat/ChatScreen.tsx#L632-L655)) calls `connectors.skipAuth(serverId)` (facade `/v1/mcp/servers/{server_id}/auth/skip` → backend `skip_auth` → `AUTH_SKIPPED` + `mcp_auth_skipped` audit). `resolveMcpAuthSkip` / `resolveMcpAuthDecision` ([chatModel/mcpAuth.ts:12-17](../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts#L12-L17), [150-197](../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts#L150-L197)) flip the local part to `"skipped"`/`"rejected"`. The same flow calls `decideApproval(approvalId, "rejected", …)` so the worker resumes with `decision: rejected` and the tool returns `{ok: false, status: "skipped"}`.

## Runtime events emitted

| Sequence | Event type          | Activity kind | Payload highlights                                                                                                                                                                     |
| -------- | ------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| K        | `mcp_auth_required` | `mcp_auth`    | `approval_id` (= `mcp_auth:{run_id}:{server_id}`), `approval_kind: "mcp_auth"`, `server_id`, `server_name`, `display_name`, `auth_url`, `expires_at`, `message`, `source_tool_call_id` |
| (later)  | `approval_resolved` | `approval`    | `approval_id`, `decision: "approved"                                                                                                                                                   | "rejected"` (only when user resolves via the approval-decision endpoint) |

Run status transitions to `WAITING_FOR_APPROVAL` between K and resume; no event accompanies that transition.

## State changes

- **Backend `mcp_servers`** — `auth_state` per the table; `last_discovery`/`required_scopes` refreshed on each `start_auth`.
- **Backend `mcp_auth_sessions`** — one row per attempt keyed by `state`, single-use via `pop_auth_session`.
- **Backend `mcp_tokens`** — one envelope per `(server_id, org_id, user_id)` with `encrypted_access_token`, optional `encrypted_refresh_token`, `token_type`, `expires_at`. Ciphertexts produced by the configured `TokenVault.encrypt`.
- **Backend audit log** — `mcp_auth_started`, then exactly one of `mcp_auth_completed` / `mcp_auth_failed` / `mcp_auth_unsupported` / `mcp_auth_skipped`.
- **AI-backend run** — `status → waiting_for_approval`; `latest_sequence_no` advances by one. One `MCP_AUTH_REQUIRED` event (`visibility = user`); on resume, one `APPROVAL_RESOLVED`. Approval record created with `approval_kind = "mcp_auth"`, `approval_id = mcp_auth:{run_id}:{server_id}`.
- **Frontend** — `sessionStorage["enterprise-search.pending-mcp-auth-action"]` set on Connect, consumed/cleared by resume. Assistant message gains an `mcp_auth_required` tool-call part with `status: "waiting"`; message status `requires-action`. After OAuth, `resolveAuthenticatedMcpServers` flips to `approved` and message status back to `running`. Banner cycles `Working… → Resuming after connector auth… → Working… → Ready`.

## Edge cases handled

- **OAuth session expiry / replay** — `complete_auth` rejects sessions older than `auth_session_ttl` (15 min); `pop_auth_session` is single-use. CSRF bound by 32-byte `state` from `secrets.token_urlsafe` ([\_pkce.py:45-48](../../services/backend/src/backend_app/identity/_pkce.py#L45-L48)).
- **IdP returns `error`** — `complete_auth` writes `AUTH_FAILED` + `mcp_auth_failed` before raising 400. Parked card stays in `waiting` until the user retries.
- **Pre-registered without DCR** — `oauth_client.authorization` falls back to the per-server fields on `McpServerRecord.oauth_client`.
- **Auth mode not OAuth2** — flips to `AUTH_UNSUPPORTED`, audits, returns 400. Defensive only; the FE should never offer Connect.
- **Tab refresh during OAuth** — parked run is durable; `sessionStorage` survives soft reloads. On mount, `ChatScreen` re-attaches via the pending-action effect ([ChatScreen.tsx:280-301](../../apps/frontend/src/features/chat/ChatScreen.tsx#L280-L301)).
- **Callback after SPA tab closed** — `complete_auth` still completes server-side; `resolveAuthenticatedMcpServers` auto-resolves the parked card on the next conversation open.
- **Duplicate `MCP_AUTH_REQUIRED` events** — `replaceToolCallPart` ([contentBuilders.ts:150-187](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L150-L187)) matches on `toolCallId = approval_id` and replaces; `mcpAuthMatchesWrapper` ([chatModel/mcpAuth.ts:229-237](../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts#L229-L237)) handles wrapper-to-card transition.
- **Already-authenticated server** — `start_auth` short-circuits via `_has_usable_token`; the FE connector list refresh resolves the parked card without navigation.
- **Caller-supplied identity** — `McpAuthStartRequest` is overridden by the facade and re-validated by the backend ([backend/app.py:354-362](../../services/backend/src/backend_app/app.py#L354-L362)).

## Known gaps / TODOs

- **Skip-then-callback race** — there is no compare-and-swap on `auth_state` during `complete_auth`; a late OAuth callback can promote a skipped server back to `authenticated`. Consider gating on the prior state at the store layer.
- **TokenVault production injection** — the local default adapter is dev-only. Production must inject a managed adapter (e.g. KMS-backed) and a persistent MCP registry store; see [services/backend/CLAUDE.md](../../services/backend/CLAUDE.md). Deployment control, not product control.
- **No GC for `mcp_auth_sessions`** — `pop_auth_session` removes on success/expiry-check, but a server-side reaper for orphaned sessions is not yet documented.
- **Resume coupling is implicit** — if the user closes the chat after OAuth completes but before `decideApproval` is called, no `APPROVAL_RESOLVED` is enqueued and the run remains in `waiting_for_approval` until the next interaction. A reconciler that detects authenticated MCP servers and completes the corresponding parked approvals would close this gap.
- **No `Retry-After` on `auth_failed`** — the FE shows an error toast but offers no automatic backoff.

## References

- AI backend MCP middleware: [middleware/auth_mcp.py](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py), [middleware/call_tool.py](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py), [backend_provider.py](../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py).
- Backend MCP service: [service.py](../../services/backend/src/backend_app/service.py); contracts: [contracts.py](../../services/backend/src/backend_app/contracts.py).
- PKCE: [\_pkce.py](../../services/backend/src/backend_app/identity/_pkce.py).
- Event projector: [events.py:421-439](../../services/ai-backend/src/runtime_api/schemas/events.py#L421-L439).
- Frontend chat model: [chatModel/](../../apps/frontend/src/features/chat/chatModel/), [mcpAuthAction.ts](../../apps/frontend/src/features/chat/mcpAuthAction.ts), [ChatScreen.tsx](../../apps/frontend/src/features/chat/ChatScreen.tsx).
