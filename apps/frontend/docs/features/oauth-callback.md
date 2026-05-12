# MCP OAuth callback

How the connector OAuth flow round-trips through the frontend back into
chat or settings.

See also:

- [../architecture/03-routing.md](../architecture/03-routing.md) — why this
  URL is handled as a side effect, not a route
- Backend docs: `services/backend/docs/features/mcp-registry.md` for the
  upstream OAuth + token vault side

Source: [`src/app/App.tsx`](../../src/app/App.tsx) — the `useEffect` keyed
on `pathname === "/mcp/oauth/callback"`,
[`src/api/mcpApi.ts`](../../src/api/mcpApi.ts) — `startMcpAuth`,
`completeMcpOAuth`, `skipMcpAuth`,
[`src/features/chat/mcpAuthAction.ts`](../../src/features/chat/mcpAuthAction.ts) —
the pending-action handoff

---

## The round trip

```
chat surface or settings/connectors
   │
   │ user clicks "Connect"
   ▼
startMcpAuth(serverId, identity)
   │   POST /v1/mcp/servers/{id}/auth/start
   │     body: { redirect_uri: <origin>/mcp/oauth/callback, … }
   │
   │   FE persists a pending action via writePendingMcpAuthAction(...)
   │   (key: server_id; value includes approvalId, returnScreen, kind)
   ▼
browser → upstream IdP (OAuth)
   │
   │   user consents
   ▼
upstream IdP → <origin>/mcp/oauth/callback?state=…&code=…
   │
   │   <EnterpriseSearchApp> useEffect fires
   │   ├── validate state / code / error / error_description
   │   ├── completeMcpOAuthOnce(state, code, error, error_description)
   │   │     → GET /v1/mcp/oauth/callback?state=…&code=…
   │   │     dedupes by JSON.stringify([state, code, error, errorDescription])
   │   │     so StrictMode's double-effect never double-completes a state
   │   │
   │   ├── readPendingMcpAuthAction(server_id)?
   │   │     yes → if not a discovery approval:
   │   │             POST /v1/agent/approvals/{approvalId}/decision approved
   │   │           clearPendingMcpAuthAction(); setCompletedMcpAuthAction(...)
   │   │           applyAppRoute({ screen: "chat" }, replace)
   │   │     no  → applyAppRoute({ screen: "settings", section: "connectors" }, replace)
   │   │
   │   └── connectors.refresh()
   ▼
chat surface or settings/connectors (with success banner)
```

The `replace` mode on `applyAppRoute` ensures the back button doesn't
return to the callback URL.

---

## Why `completeMcpOAuthOnce`

React StrictMode double-invokes `useEffect` cleanups + setups. Without
dedup, the second invocation would hit the facade with the same `state`
code, which has already been consumed — the second call 4xx's and looks
like a real error. `completeMcpOAuthOnce` maintains a module-level `Map`
keyed by `JSON.stringify([state, code, error, errorDescription])`; the
second call shares the original promise. On failure the entry is purged
so a retry can proceed.

---

## Discovery approvals are not real approvals

When chat shows a connector card during a run (e.g. "Connect Google Drive
to continue"), the approval id has the prefix `mcp_discovery:<run_id>:<server_id>`.
These IDs aren't persisted as `ApprovalRequest` rows by the runtime — the
backend never has anything to decide against, and a `POST /decision`
would 404. The OAuth completion **is** the resolution, so the FE skips
`decideApproval` for any approval id starting with `mcp_discovery:`.

---

## Pending action handoff

`mcpAuthAction.ts` stores **one** pending action per server in localStorage:

| Field          | Why                                                                      |
| -------------- | ------------------------------------------------------------------------ |
| `approvalId`   | If set, the callback POSTs the approval decision after OAuth completes.  |
| `serverId`     | Lookup key                                                               |
| `returnScreen` | Hint for the post-callback redirect (chat vs settings)                   |
| `kind`         | `"chat"` or `"settings"` — drives the success banner copy in the chat UI |
| `completedAt`  | Stamped on `setCompletedMcpAuthAction` so the chat surface can fade it   |

The key is the server id, not the OAuth state, because the state is opaque
to the FE and the server id is the natural identifier for "the thing the
user was trying to connect."

---

## Skip flow

`skipMcpAuth(serverId, identity)` is the explicit "I don't have credentials,
mark this connector as skipped" path. Same surface as `complete` but no
OAuth round-trip; the facade flags the server `auth_skipped=true` and chat
won't prompt again unless the user retries.
