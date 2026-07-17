# PR 4.4 — MCP overlay flow + test-connection (5-step wizard)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4, PR 4.4 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** backend (test-connection probe + static catalog endpoint) · backend-facade (one proxy) · frontend (5-step wizard component) · design-system (`<Dialog>` primitive — shared with PR 4.2)
> **Size:** **M.** One probe endpoint that wraps the existing `mcp_oauth.py` discovery + an MCP `initialize` handshake. One static catalog JSON file. One wizard component composed of five small step bodies, all over existing `mcpApi.ts` calls. Adds `@radix-ui/react-dialog` to `packages/design-system/src/`.
> **Depends on:** Existing `mcp_servers` schema + CRUD (migration 0001) · existing `mcpApi.ts` (FE) · existing OAuth callback handler in `App.tsx` · PR 3.3 inline MCP discovery card (the wizard handles install; the discovery card handles in-run suggestion — different surfaces)
> **Reads alongside:** [`pr-3.3-mcp-discovery-approval-polish.md`](pr-3.3-mcp-discovery-approval-polish.md) (in-run discovery card; complements this wizard), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md)
> **Sibling docs (Wave 4):** [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) · [`pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) · [`pr-4.3-settings-ai-and-data.md`](pr-4.3-settings-ai-and-data.md) · [`pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md)

---

## 0 · TL;DR

A 5-step wizard the design doc inventories step-by-step. The 5 steps map onto data and endpoints we already have, plus **one** new probe endpoint that gets us "test connection" honestly.

| Step               | What the user does                                        | Endpoint                                                                              | New?                           |
| ------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------ |
| 1. Browse / search | pick a server from a static catalog or paste a custom URL | `GET /v1/mcp/catalog` (NEW, static JSON)                                              | YES (one route, one JSON file) |
| 2. Auth            | OAuth / API key / no auth — UI adapts                     | `POST /v1/mcp/servers` (existing) + `POST /v1/mcp/servers/{id}/auth/start` (existing) | NO                             |
| 3. Test connection | wizard shows reachability + handshake result              | `POST /v1/mcp/servers/{id}/test` (NEW)                                                | YES (one route)                |
| 4. Scope review    | per-server scope toggle + Read-only preset                | existing `mcp_servers.required_scopes` (per-server, not per-tool — see §1.3)          | NO                             |
| 5. Confirm         | summary card + "Add to workspace"                         | `PATCH /v1/mcp/servers/{id}` (existing) → flips `enabled=true`                        | NO                             |

The wizard is a **single React component** with five small step bodies. The persistence is `mcp_servers` from migration 0001 — which already carries `auth_state`, `oauth_client`, `required_scopes`, `enabled`. We don't migrate. Per-tool scope toggles (the design's "later" pill) are explicitly out of scope.

For the modal we adopt **`@radix-ui/react-dialog`** as a new design-system primitive. PR 4.2's invite modal and other future overlays share it.

LoC estimate: backend ≈ 220 (test-connection probe + catalog static + 1 audit action + tests) · backend-facade ≈ 30 (one proxy route) · frontend ≈ 580 (wizard + 5 steps + 2 hooks + tests) · design-system ≈ 60 (`<Dialog>` wrapper around Radix Dialog).

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (Settings → MCP overlay) inventories a 5-step wizard. Today the FE has:

| Today                                                                                                                           | Gap                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `SettingsScreen.tsx:215-365` — a flat form to add a custom URL connector + a list of installed servers + an authenticate button | No catalog browser; the user has to paste a URL                                                |
| `mcpApi.ts` — full CRUD + OAuth start/finalize                                                                                  | No test-connection step; install is "optimistic" — the design's TODO list calls this out as P1 |
| `mcp_oauth.py` — discovery + dynamic client registration + PKCE + token refresh                                                 | Reachable from the OAuth start path, but no probe endpoint                                     |
| `App.tsx:216-297` — OAuth callback handler                                                                                      | The 5-step wizard has nowhere to mount; the existing form is single-screen                     |

A second gap: the design wants an inline MCP discovery card during a run ("Connect Linear to fetch ticket statuses?"). That **already** lives in `ConnectorAuthTool` + PR 3.3's polish. **PR 4.4 is the install path**; PR 3.3 is the in-run discovery path. Both ship `mcp_servers` rows, but the wizard is the user-driven path that tests the connection before flipping `enabled=true`.

The "test-connection" requirement is the load-bearing piece. The design's words: _"currently optimistic; should ping the server before 'Add to workspace' enables."_ Today the only way to know if a server is real is to authenticate against it (which mints a row before any reachability is proven). We add a **probe** endpoint that:

1. Verifies reachability over the configured transport (HTTP / WebSocket).
2. Performs the MCP `initialize` handshake (servers must respond with their `server_info` + protocol version).
3. Lists advertised tools (the count, not the tools themselves — that's step 4 territory; v1 keeps it count-only).
4. Returns a normalised `{reachable, transport, server_info, tools_count, latency_ms, error?}`.

The probe runs against an already-persisted `mcp_servers` row (we need somewhere to keep the URL + auth config), so the wizard's flow is: insert a row in **draft** state, probe it, present results, on confirm flip to **enabled**.

### 1.2 Goals

1. **Catalog of well-known MCP servers** — a static JSON file backed by `GET /v1/mcp/catalog`. ~12-20 entries: Linear, Notion, Sentry, Asana, Jira, Figma, Slack-MCP, GitHub-MCP, GitLab-MCP, Salesforce, HubSpot, Zendesk, etc. Each entry: `{id, name, display_name, vendor, description, default_url, default_transport, default_auth_mode, logo_url, scopes_documentation_url, tags: ['ticketing'|'docs'|'crm'|...] }`.
2. **5-step wizard** with progress indicator. Steps are linear; the user can go back. Browse / Auth / Test / Scope / Confirm. Each step is a small subcomponent of `<McpOverlay>`.
3. **Test-connection** before "Add to workspace" enables. The button stays disabled until the probe returns `reachable: true`.
4. **Mount in two places**: from the topbar/Settings (Settings → Connectors → "Add MCP server") and from the chat surface (Settings → MCP servers link in the connectors popover). Both call the same `<McpOverlay open onClose>`.
5. **Adopt `@radix-ui/react-dialog`** as the design-system `<Dialog>` primitive — shared with PR 4.2's invite modal. **One install across PRs 4.2 and 4.4.**
6. **Streaming and runtime untouched.** The wizard is a Settings flow; the only service-side change is one read-only probe route.

### 1.3 Non-goals

- **Per-tool scope toggles + Read-only preset.** The design lists "test-connection" as P1 and per-tool scopes are part of an MCP-overlay overhaul that the plan defers (`Out of scope (this plan)` row in the wave plan). v1 keeps the existing per-server `required_scopes` list and a Read-only **server-level** preset (which sets `required_scopes` to the read-subset where the catalog entry annotates one).
- **Server health monitoring after install.** Design explicitly "later."
- **Dynamic client registration UI.** The existing `mcp_oauth.py` already does it transparently when supported; the wizard doesn't surface a separate step.
- **Per-tool quota / rate-limit indicators.** Design "later."
- **Editing an installed server's URL.** The wizard installs; editing remains the existing `PATCH /v1/mcp/servers/{id}` call from the Settings list view.
- **Supporting custom MCP transports beyond HTTP / SSE / WebSocket.** Existing `mcp_servers.transport` enum.
- **Catalog as a server-managed CRUD.** v1 ships static JSON + a `GET` route. A real catalog database is a future PR.
- **Real-time tool-list display in step 4.** The probe returns a count; the user reviews scopes (the granular tool list is a separate flow in the future MCP overhaul).
- **Auto-publishing the wizard logic to the in-run discovery card.** Different surfaces, same `mcp_servers` row.

### 1.4 Success criteria

- ✅ `GET /v1/mcp/catalog` returns the static catalog list in <30 ms p99.
- ✅ `POST /v1/mcp/servers/{id}/test` returns `{reachable: true, server_info: {name, version}, tools_count: N, latency_ms, transport}` for a real server in <1 s p99 (network-bound).
- ✅ Test result is **persisted** on the server row (`last_probe_at`, `last_probe_result_json`) so the catalog entry can show "last verified 2 minutes ago" without re-probing.
- ✅ Wizard mounts from Settings → Connectors → "Add MCP server" and from a future per-chat connectors popover.
- ✅ Wizard's "Add to workspace" button stays disabled until step 3 returns `reachable: true`.
- ✅ Cancelling the wizard mid-flow leaves a `mcp_servers` row in `draft` state with `enabled=false`; a reaper job (`runtime_worker/jobs/retention_sweeper.py` — extend by one TTL) deletes drafts after 24 hours.
- ✅ One audit row per privileged write (`mcp.server.test`, `mcp.server.install`).
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; backend pytest green; frontend typecheck + build green.

### 1.5 User stories

| #    | Persona         | Story                                                                                                                                                                                                         |
| ---- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Marcus (admin)  | I open Settings → Connectors → "Add MCP server". I see a grid of well-known servers (Linear, Notion, Sentry, …). I click Linear. The auth step shows OAuth / Continue to Linear. I click; OAuth tab opens.    |
| US-2 | Marcus          | OAuth completes; I'm back in the wizard at step 3. Atlas is testing the connection. Two seconds later: "Connected · 84 tools advertised · 192 ms".                                                            |
| US-3 | Marcus          | Step 4 shows the server's documented scope list ("Read issues, Read projects, Write comments"). I leave Read-only on (which restricts to `read:*`). Step 5 summary; I click Add. The server flips to enabled. |
| US-4 | Marcus (custom) | I have an internal MCP server. I paste a URL on step 1. Step 2 picks API key. I paste a key. Step 3 probes it: "Could not reach — connection refused (1.2 s)". The Add button stays disabled until I retry.   |
| US-5 | Marcus          | I cancel the wizard at step 3. Refresh. The server's not in my list. (24 h later the draft row gets reaped.)                                                                                                  |
| US-6 | Sarah           | In a chat, the connectors popover offers "+ Add MCP server" → opens the same wizard.                                                                                                                          |
| US-7 | Marcus          | An admin already installed Notion at the workspace level. I, a member, see Notion in the catalog tagged "Already installed by your workspace — Connect to authenticate" — clicking starts OAuth, no install.  |

---

## 2 · Spec

### 2.1 Wire — catalog

```
GET /v1/mcp/catalog
```

Returns:

```jsonc
{
  "catalog": [
    {
      "id": "linear",
      "display_name": "Linear",
      "vendor": "Linear, Inc.",
      "description": "Issues, projects, cycles.",
      "default_url": "https://mcp.linear.app/sse",
      "default_transport": "sse", // 'sse' | 'http' | 'ws'
      "default_auth_mode": "oauth2", // 'oauth2' | 'api_key' | 'none'
      "logo_url": "/static/connectors/linear.svg",
      "scopes_documentation_url": "https://linear.app/docs/mcp",
      "tags": ["ticketing", "engineering"],
      "read_only_scope_subset": [
        "read:issues",
        "read:projects",
        "read:comments",
      ],
    },
    { "id": "notion", "...": "..." },
    // … 10–18 entries
  ],
}
```

The catalog is a static JSON file at `services/backend/src/backend_app/mcp/catalog.json`. The route is a JSON read with a 5-minute HTTP cache header. Curating new entries is a code PR. We accept that.

### 2.2 Wire — test-connection

```
POST /v1/mcp/servers/{server_id}/test
```

Request body: `{}` (no parameters; the row's URL/transport/auth are persisted from the wizard's step 1+2).

Response (200):

```jsonc
{
  "reachable": true,
  "transport": "sse",
  "server_info": { "name": "linear-mcp", "version": "0.4.2" },
  "protocol_version": "2024-11-05",
  "tools_count": 84,
  "resources_count": 12,
  "prompts_count": 3,
  "latency_ms": 192,
  "error": null,
  "probed_at": "2026-05-05T16:01:14.220Z",
}
```

Failure (200 with `reachable=false` — we don't 4xx because the row exists; only the probe fails):

```jsonc
{
  "reachable": false,
  "transport": "http",
  "server_info": null,
  "tools_count": 0,
  "latency_ms": 1183,
  "error": {
    "kind": "connection_refused", // 'connection_refused' | 'tls_error' | 'auth_failed' | 'protocol_mismatch' | 'timeout' | 'unknown'
    "message": "ECONNREFUSED 192.0.2.1:443",
  },
  "probed_at": "…",
}
```

The probe is **idempotent** and **does not** trigger an OAuth flow. It calls `mcp_oauth.discover()` if the row is OAuth-mode (validates the discovery document only) and then performs a single `initialize` MCP handshake. If the row needs auth and the handshake errors with a 401, `error.kind = 'auth_failed'` and the wizard nudges the user back to step 2.

The result is persisted on the server row:

```sql
ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS last_probe_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_probe_result_json JSONB;
```

(Migration `0017_mcp_test_connection.sql`. Number coordinated with PR 4.2's invitations migration at merge time.)

### 2.3 Wire — install (existing routes; no change)

The wizard's step 1+2 calls existing `POST /v1/mcp/servers` to insert the row in **draft** state (`enabled=false`, `auth_state='unauthenticated'` or `auth_state='auth_pending'`). On step 5 confirm, it calls existing `PATCH /v1/mcp/servers/{id}` with `{ enabled: true }`. We don't add new routes for the install — only for the probe and the catalog read.

### 2.4 Persistence

```sql
-- 0017_mcp_test_connection.sql

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS last_probe_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_probe_result_json JSONB;

-- Reap orphan drafts (rows with enabled=false AND auth_state='unauthenticated' AND no probe in 24h).
-- Carried by the existing retention sweeper via a small extension; see §3.6.
```

The catalog itself is **not** in the database — it's a static JSON file in code. Argument for the DB table approach (catalog as data): admins could curate. Argument against (file): catalog edits are infrequent, they need code review (the catalog is a security surface — wrong URLs route OAuth to wrong endpoints), the file ships with the service, no DB writes, no migration. We pick the file.

### 2.5 Audit

Two new actions on `mcp_audit_events` (existing append-only chain):

| Action               | Metadata                                                                          |
| -------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | --------- |
| `mcp.server.test`    | `{ server_id, reachable, transport, latency_ms, error_kind?, probed_by_user_id }` |
| `mcp.server.install` | `{ server_id, source: 'catalog'                                                   | 'custom_url', catalog_id?, installed_by_user_id, scope_preset: 'read_only' | 'full' }` |

We deliberately log `mcp.server.test` because a probe that auths against a third-party endpoint is **the server hearing from us** — auditors want to see when. The audit metadata never includes the probe URL or any token.

### 2.6 Permissions

| Caller          | Read catalog | Probe | Install (PATCH enabled=true)                        |
| --------------- | ------------ | ----- | --------------------------------------------------- |
| Workspace admin | ✅           | ✅    | ✅                                                  |
| Member          | ✅           | ❌    | ❌ (per-user OAuth allowed; install requires admin) |

Wait — this is the three-layer model from the design. The admin **installs** (workspace-wide visibility); each member then **authenticates** (per-user OAuth). We honour that here:

- `POST /v1/mcp/servers` (insert draft) — admin only.
- `POST /v1/mcp/servers/{id}/test` — admin only (the probe is a workspace-level action).
- `PATCH /v1/mcp/servers/{id}` `{ enabled: true }` — admin only.
- `POST /v1/mcp/servers/{id}/auth/start` (OAuth) — **member or admin** (per-user auth).
- `POST /v1/mcp/servers/{id}/auth/complete` — same.

The wizard is the admin's flow. Members see the wizard read-only ("To add a server, ask your admin").

### 2.7 Error semantics

| Condition                                               | Status | Code                                    |
| ------------------------------------------------------- | ------ | --------------------------------------- |
| `POST /v1/mcp/servers/{id}/test` for foreign-org row    | 404    | `mcp_server_not_found`                  |
| Probe times out (>5 s)                                  | 200    | body `error.kind = 'timeout'`           |
| Probe handshake says wrong protocol version             | 200    | body `error.kind = 'protocol_mismatch'` |
| Catalog file missing (deploy regression)                | 503    | `catalog_unavailable`                   |
| Cancelled wizard leaves draft → reaped after 24 h       | n/a    | sweeper                                 |
| Custom URL not parseable                                | 422    | `invalid_url`                           |
| Custom URL is a private IP (RFC 1918) and SSRF guard on | 422    | `private_url_blocked`                   |
| Member calls `POST /v1/mcp/servers/{id}/test`           | 403    | `forbidden`                             |

The SSRF guard is the same one `services/backend/src/backend_app/http_client.py` uses for OAuth (existing — it blocks RFC 1918, link-local, and metadata IPs in production; admins set `MCP_ALLOW_PRIVATE_NETWORKS=true` for self-hosted scenarios).

### 2.8 Frontend contract (`@0x-copilot/api-types`)

```ts
// packages/api-types/src/index.ts

export interface McpCatalogEntry {
  id: string;
  display_name: string;
  vendor: string;
  description: string;
  default_url: string;
  default_transport: "sse" | "http" | "ws";
  default_auth_mode: "oauth2" | "api_key" | "none";
  logo_url: string;
  scopes_documentation_url: string | null;
  tags: string[];
  read_only_scope_subset: string[] | null;
}

export interface McpProbeResult {
  reachable: boolean;
  transport: "sse" | "http" | "ws";
  server_info: { name: string; version: string } | null;
  protocol_version: string | null;
  tools_count: number;
  resources_count: number;
  prompts_count: number;
  latency_ms: number;
  error: { kind: McpProbeErrorKind; message: string } | null;
  probed_at: string;
}

export type McpProbeErrorKind =
  | "connection_refused"
  | "tls_error"
  | "auth_failed"
  | "protocol_mismatch"
  | "timeout"
  | "unknown";
```

`McpServer` (existing) gains two optional fields:

```ts
export interface McpServer {
  // … existing fields
  last_probe_at: string | null;
  last_probe_result: McpProbeResult | null;
}
```

### 2.9 Frontend wiring — the wizard

Five components, one host:

```
apps/frontend/src/features/connectors/mcp/
├── McpOverlay.tsx              (host; manages step state + wizard primitives)
├── steps/
│   ├── BrowseStep.tsx          (catalog grid + search + custom URL)
│   ├── AuthStep.tsx            (OAuth / API key / no auth — UI adapts to row)
│   ├── TestStep.tsx            (probe results + Retry)
│   ├── ScopeStep.tsx           (scope checkboxes, Read-only preset)
│   └── ConfirmStep.tsx         (summary card + "Add to workspace")
└── useMcpInstallFlow.ts        (state machine: { step, draft, probeResult, scopePreset })
```

Wizard primitives:

| Primitive                  | Source                                                                           |
| -------------------------- | -------------------------------------------------------------------------------- |
| `<Dialog>` (host)          | `@0x-copilot/design-system` (NEW; wraps `@radix-ui/react-dialog`)                |
| Stepper indicator          | Local (~30 LOC; horizontal pills with active/done states)                        |
| Catalog grid               | Local (CSS grid + `<Card>` from design-system)                                   |
| Form fields (URL, API key) | Existing `<TextInput>`, `<Field>`                                                |
| OAuth button               | Existing `<Button>` triggering existing `connectors.authenticate(serverId)` path |
| Toggle scope               | Existing `<Switch>`                                                              |

State machine in `useMcpInstallFlow.ts`:

```
{ step: 'browse' }
   → user picks catalog entry / pastes URL
{ step: 'auth', catalogEntry?, customUrl?, draftRowId: null }
   → POST /v1/mcp/servers (creates draft row)
{ step: 'auth', draftRowId: <id> }
   → user picks auth mode + completes:
         oauth2 → existing OAuth flow (App.tsx callback handler resumes us)
         api_key → POST /v1/mcp/servers/{id}/auth/api-key (existing)
         none   → no-op
{ step: 'test', draftRowId }
   → POST /v1/mcp/servers/{id}/test
   → on reachable=false, "Retry" button OR back to step 2
{ step: 'scope', draftRowId, probeResult }
   → user toggles read-only / pick scopes
{ step: 'confirm', draftRowId, ... }
   → PATCH /v1/mcp/servers/{id} { enabled: true, required_scopes: [...] }
   → on success: show "Connected" + "Try in chat" / "View in Connectors" links
```

The OAuth callback re-entry is handled by the existing `App.tsx:216-297` handler: on `?state=…&code=…` callback, the wizard reopens at step 3 ("we just got back from OAuth — testing the connection"). State for the wizard is persisted in `sessionStorage` keyed by the OAuth `state` param.

### 2.10 Service path

```
backend-facade  /v1/mcp/catalog                        →  backend  /internal/v1/mcp/catalog
backend-facade  /v1/mcp/servers/{id}/test              →  backend  /internal/v1/mcp/servers/{id}/test

(existing routes for /v1/mcp/servers CRUD, /v1/mcp/servers/{id}/auth/* unchanged)
```

### 2.11 Design-system change — `<Dialog>`

```tsx
// packages/design-system/src/dialog.tsx (NEW)

import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { classNames } from "./classnames";

export function Dialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  children: ReactNode;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ds-dialog-overlay" />
        <RadixDialog.Content
          className="ds-dialog-content"
          aria-describedby={undefined}
        >
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export const DialogTitle = RadixDialog.Title;
export const DialogClose = RadixDialog.Close;
```

Plus ~50 LOC of CSS in `packages/design-system/src/styles.css` for the overlay + content (centred, max-width 720px, rounded, shadow). Per `packages/design-system/CLAUDE.md`, generic, reusable primitives belong here. PR 4.2's invite modal is the second consumer — exactly the threshold for promotion to design-system.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────┐                                            ┌──────────────────────┐
   │ apps/frontend  │ <McpOverlay open={…} onClose={…} />        │ backend-facade       │
   │ Settings →     │                                            │ proxy                │
   │ Connectors →   │                                            │                      │
   │ "Add MCP" CTA  │ GET /v1/mcp/catalog                        │                      │
   │                │ ─────────────────────────────────────────► │                      │
   │ Composer →     │ POST /v1/mcp/servers (existing)            │                      │
   │ Connector      │ ─────────────────────────────────────────► │                      │
   │ popover →      │ POST /v1/mcp/servers/{id}/auth/start (existing)                  │
   │ "+ Add MCP"    │ ─────────────────────────────────────────► │                      │
   │                │ POST /v1/mcp/servers/{id}/test (NEW)       │                      │
   │                │ ─────────────────────────────────────────► │                      │
   │ App.tsx OAuth  │ PATCH /v1/mcp/servers/{id} (existing)      │                      │
   │ callback re-   │ ─────────────────────────────────────────► │                      │
   │ enters wizard  │                                            └──────┬───────────────┘
   └────────────────┘                                                   │ /internal/v1/mcp/*
                                                                        ▼
                                                              ┌──────────────────────┐
                                                              │ services/backend     │
                                                              │ mcp_oauth.py (existing) │
                                                              │ mcp_probe.py (NEW)   │
                                                              │ catalog.json (NEW)   │
                                                              │ mcp_servers (existing)│
                                                              │ mcp_audit_events (existing) │
                                                              └──────────────────────┘
```

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                | Touched?                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events`, `RuntimeEventEnvelope` | No                                                                                                                        |
| SSE handshake                            | No                                                                                                                        |
| Worker job loop                          | No (except: retention sweeper extends with one new TTL — see §3.6)                                                        |
| Capabilities / tools / MCP loaders       | No (loaders read enabled `mcp_servers` rows; the wizard inserts and flips `enabled` — they pick up at run-start as today) |
| Citations, drafts, approvals, subagents  | No                                                                                                                        |
| Audit chain                              | Additive — two new `action` constants on `mcp_audit_events`                                                               |

### 3.3 Why the catalog is static JSON, not a DB table

| Argument                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------- |
| Catalog edits are infrequent (~weekly at most) and need code review (security surface — wrong URLs misroute OAuth).                     |
| The file ships with the service; no DB write, no migration, no admin UI to curate it.                                                   |
| ~12-20 entries × ~250 bytes each ≈ 5 KB; the route returns the file with a 5-minute cache header.                                       |
| When a real CRUD becomes necessary (admins curating their own, multi-tenant hosting), a future PR adds the table; v1 doesn't speculate. |

### 3.4 Why we adopt Radix Dialog now

| Need                                                  | Built-in option | Build vs. adopt                            |
| ----------------------------------------------------- | --------------- | ------------------------------------------ |
| Modal open / close state                              | useState        | trivial                                    |
| **Focus trap** (a11y; the modal must trap Tab inside) | not built-in    | non-trivial                                |
| Escape-to-close                                       | window listener | trivial                                    |
| Click-outside-to-close                                | event handler   | mostly trivial; subtle bugs around portals |
| `aria-modal`, `aria-labelledby`, role="dialog"        | manual          | tedious                                    |
| Restore focus on close                                | manual          | non-trivial                                |
| Body scroll lock                                      | manual          | flaky cross-browser                        |
| Portal so content escapes overflow:hidden parents     | `createPortal`  | works but fiddly                           |

Together, "build it ourselves" is ~150 LOC of accessibility-sensitive code that's already a solved problem. Radix Dialog is **2.5 KB gzipped** (the dialog primitive only), MIT, weekly downloads >2M, and is the de-facto choice in modern React. We adopt it as **`<Dialog>` in design-system**.

The same library family is used in PR 4.2 (`<DropdownMenu>`) and PR 4.5 (`<Popover>`). Treating Radix as our headless primitives baseline is the simplest answer the React ecosystem has converged on.

### 3.5 The probe — what it does and what it doesn't

**Does:**

1. Resolve the server's URL (catalog default OR custom).
2. If `auth_mode = oauth2` and OAuth is **not yet completed**, only validates the OAuth discovery document (`/.well-known/oauth-authorization-server`) — does not initiate the flow.
3. If OAuth is complete or auth_mode is `api_key` / `none`, opens an MCP-protocol connection (HTTP / SSE / WebSocket).
4. Sends `initialize` request with the project's protocol version.
5. Reads `server_info`, lists `tools` / `resources` / `prompts` (count only).
6. Closes the connection.
7. Persists `last_probe_at` + `last_probe_result_json`.
8. Audits.

**Does not:**

- Stream tokens, run a tool, or call any user-impacting verb.
- Cache results across orgs.
- Probe in the background (only on user request).

The probe lives in `services/backend/src/backend_app/mcp/probe.py`. It uses the project's existing async HTTP client (timeout = 5 s default; configurable via `MCP_PROBE_TIMEOUT_SECONDS`).

### 3.6 Draft-row reaping

The wizard inserts rows in draft state (`enabled=false`, `auth_state='unauthenticated'` or `auth_state='auth_pending'`). If the user cancels mid-flow, the row stays. We extend `runtime_worker/jobs/retention_sweeper.py` with one new sweep:

```python
# kind: 'mcp_drafts' (NEW kind)
# rule: DELETE FROM mcp_servers
#       WHERE enabled = false
#         AND last_probe_at IS NULL
#         AND created_at < NOW() - INTERVAL '24 hours';
```

The sweeper already iterates kinds; one more line. We add `mcp_drafts` to the `RetentionKind` enum (no migration) — the sweep ignores `retention_policies` for this kind because it's a fixed 24 h policy, not user-configurable.

### 3.7 DRY — what we reuse vs. what we add

| Concern                    | Reuse                                                                                                                                                       | Add                                             |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `mcp_servers` table        | Existing migration 0001                                                                                                                                     | One ALTER (two columns)                         |
| MCP CRUD routes            | Existing                                                                                                                                                    | One probe route                                 |
| OAuth flow                 | Existing `mcp_oauth.py`                                                                                                                                     | —                                               |
| OAuth callback handler     | Existing `App.tsx:216-297`                                                                                                                                  | One sessionStorage read on resume               |
| MCP `initialize` handshake | The python-mcp client logic already in `agent_runtime/capabilities/mcp/loader.py` (one helper extracted to `services/backend/src/backend_app/mcp/probe.py`) | A 60-line probe wrapper                         |
| SSRF guard                 | Existing `http_client.py` private-IP guard                                                                                                                  | —                                               |
| Audit chain                | `mcp_audit_events` (existing)                                                                                                                               | Two new `action` constants                      |
| Retention sweep            | `runtime_worker/jobs/retention_sweeper.py`                                                                                                                  | One `mcp_drafts` kind                           |
| FE catalog rendering       | Existing `<Card>`, `<Badge>`                                                                                                                                | A 70-line catalog grid component                |
| FE OAuth resume            | Existing OAuth callback handler                                                                                                                             | One sessionStorage check on mount               |
| Modal primitive            | `@radix-ui/react-dialog` (new dep, used by PR 4.2 too)                                                                                                      | One ~60 LOC `<Dialog>` wrapper in design-system |
| Stepper indicator          | None (specific to this flow)                                                                                                                                | ~30 LOC                                         |
| Wizard state machine       | None                                                                                                                                                        | ~120 LOC `useMcpInstallFlow`                    |

### 3.8 Pre-built libraries — what we considered, what we use

| Need                         | Considered                                                   | Decision                                                                                                                                                                       |
| ---------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Modal primitive              | `@radix-ui/react-dialog`, `@headlessui/react`, `react-modal` | **Radix Dialog.** Smallest, most-maintained, headless. PR 4.2 is the second consumer. ~2.5 KB.                                                                                 |
| Wizard / stepper             | `react-stepper-horizontal`, `@mui/material/Stepper`, build   | **Build (~30 LOC).** Steppers are tiny and the design wants very specific styling. MUI carries the whole library; Radix doesn't ship a stepper.                                |
| MCP client (server-side)     | `@modelcontextprotocol/python-sdk` / project-local helper    | **Reuse the existing project helper.** `agent_runtime/capabilities/mcp/loader.py` already wraps the client; we extract the handshake into `services/backend/.../mcp/probe.py`. |
| OAuth metadata discovery     | Existing `mcp_oauth.discover()`                              | **Reuse.**                                                                                                                                                                     |
| HTTP client w/ SSRF guard    | `httpx`, project's `http_client.py`                          | **Reuse the project client.**                                                                                                                                                  |
| URL parsing / validation     | stdlib `urllib.parse` + project regex                        | **Reuse stdlib.**                                                                                                                                                              |
| Form validation              | `react-hook-form`, `zod`                                     | **Skip.** Two text inputs and an enum; native state.                                                                                                                           |
| State machine                | `xstate`, `@xstate/react`                                    | **Skip.** Five linear steps; one `useReducer` is cheaper than introducing xstate.                                                                                              |
| Catalog search / fuzzy match | `fuse.js`, `match-sorter`                                    | **`match-sorter` if catalog grows >50; v1 with ~15 entries does substring match in 5 LOC.**                                                                                    |

### 3.9 Sequence — Marcus installs Linear

```
Marcus           FE (McpOverlay)               backend-facade           backend                       Linear MCP server
 │                  │                             │                       │                                 │
 │  click "+ Add"   │                             │                       │                                 │
 │ ──────────────► │ open <Dialog>               │                       │                                 │
 │                  │ GET /v1/mcp/catalog         │                       │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ read static catalog.json       │
 │                  │ ◄────────────────────────── │ ◄─────────────────── │                                 │
 │                  │ render grid                 │                       │                                 │
 │  pick Linear     │                             │                       │                                 │
 │  click Continue  │                             │                       │                                 │
 │                  │ POST /v1/mcp/servers        │                       │                                 │
 │                  │ {url, transport, auth_mode} │                       │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ INSERT mcp_servers (enabled=false, auth_state=unauthenticated)
 │                  │ ◄────────────────────────── │ ◄─────────────────── │ return server row             │
 │                  │ step 2: AuthStep — OAuth    │                       │                                 │
 │  click "Continue │                             │                       │                                 │
 │  to Linear"      │                             │                       │                                 │
 │                  │ POST /v1/mcp/servers/{id}/auth/start                │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ mcp_oauth.start_pkce          │
 │                  │ ◄────────────────────────── │ ◄─────────────────── │ return auth_url               │
 │                  │ window.location = auth_url  │                       │                                 │
 │                  │                             │                       │                                 │
 │  …Linear OAuth UI prompts → user grants → redirect to /mcp/oauth/callback?state&code                       │
 │                  │ App.tsx callback handler picks up state             │                                 │
 │                  │ POST /v1/mcp/servers/{id}/auth/finalize             │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ exchange code, persist tokens │
 │                  │                             │                       │                                 │
 │                  │ resume <McpOverlay> (sessionStorage), step = test │                                 │
 │                  │ POST /v1/mcp/servers/{id}/test                     │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ open SSE conn → initialize ───► │
 │                  │                             │                       │ ◄────────────────────────────── │
 │                  │                             │                       │ list tools count               │
 │                  │                             │                       │ persist last_probe_*           │
 │                  │                             │                       │ INSERT mcp_audit_events        │
 │                  │ ◄────────────────────────── │ ◄─────────────────── │ return probe result            │
 │                  │ step 3 → step 4 (scope)     │                       │                                 │
 │  toggle read-only│                             │                       │                                 │
 │  click Add       │                             │                       │                                 │
 │                  │ PATCH /v1/mcp/servers/{id}  │                       │                                 │
 │                  │ {enabled:true, required_scopes:[...]}               │                                 │
 │                  │ ──────────────────────────► │ ───────────────────► │ UPDATE mcp_servers             │
 │                  │                             │                       │ INSERT mcp_audit_events        │
 │                  │                             │                       │   (mcp.server.install)         │
 │                  │ ◄────────────────────────── │ ◄─────────────────── │ return updated row             │
 │                  │ "Connected · Try in chat"   │                       │                                 │
```

### 3.10 Edge cases

| Case                                                                                                           | Behaviour                                                                                                                                                             |
| -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User pastes a URL identical to a catalog entry's `default_url`                                                 | We collapse to the catalog entry (use catalog metadata for display) but still allow custom auth_mode.                                                                 |
| Catalog entry's `default_auth_mode = oauth2` but server's `/.well-known/oauth-authorization-server` is missing | Probe returns `error.kind = 'protocol_mismatch'` with a clear message. Wizard stays on step 3.                                                                        |
| User cancels the wizard at step 5 (after clicking Add but before the response)                                 | Server commits the install; client navigates away; row is `enabled=true`. Not a v1 problem (low likelihood; server-side request still completes).                     |
| OAuth step fails (Linear says no)                                                                              | The OAuth callback handler captures the error; wizard resumes at step 2 with an inline error.                                                                         |
| Token expires between OAuth completion and probe                                                               | Probe returns `error.kind = 'auth_failed'`; the wizard offers "Re-authenticate" (back to step 2).                                                                     |
| Two admins probe simultaneously                                                                                | Two independent probes; both succeed; `last_probe_*` is the latest. No locking needed (probe is a read).                                                              |
| Server advertises 0 tools                                                                                      | UI shows "No tools advertised — Linear MCP may not be enabled on the server side." Add button stays available; admins can install anyway (some servers gate by auth). |
| Catalog file ships with a deprecated entry                                                                     | Admins still see it; we add a `deprecated_at` field on catalog entries in a follow-up if it becomes a maintenance load.                                               |
| User has 200 servers already installed                                                                         | Catalog browse-vs-installed indicator: catalog tile shows "Installed by your workspace" for entries already present. (Same `mcp_servers` row, lookup by `url`.)       |
| Reaper runs while a wizard is mid-flow                                                                         | Sweeper's WHERE clause requires `created_at < NOW() - INTERVAL '24 hours'`; an in-flight draft is <1 hour old; safe.                                                  |
| Probe times out at exactly 5 s                                                                                 | `error.kind = 'timeout'`; latency rounded to 5000.                                                                                                                    |
| Customer is air-gapped and `MCP_ALLOW_PRIVATE_NETWORKS=true`                                                   | SSRF guard relaxes; private IPs allowed; the catalog's external entries are still external.                                                                           |

### 3.11 Test plan

**Backend (`services/backend/tests/`)**

- `unit/mcp/test_catalog_endpoint.py` — returns the static file shape; cache header set.
- `unit/mcp/test_probe_oauth_unauth.py` — discovery doc valid → returns `auth_pending` (no handshake attempted).
- `unit/mcp/test_probe_handshake.py` — mock MCP server → returns 200 / reachable / counts populated.
- `unit/mcp/test_probe_timeout.py` — fixture server that holds → `error.kind=timeout`.
- `unit/mcp/test_probe_audit_emission.py` — one row per probe; metadata correct.
- `unit/mcp/test_install_audit.py` — `mcp.server.install` row on enable=true flip.
- `unit/mcp/test_probe_admin_only.py` — member 403; admin 200.
- `unit/mcp/test_ssrf_guard.py` — private URL → 422; flag flips → 200.
- `integration/test_draft_reaper.py` — sweeper deletes drafts >24 h old; never deletes enabled rows.

**Frontend (`apps/frontend/src/features/connectors/mcp/`)**

- `useMcpInstallFlow.test.ts` — state machine transitions; OAuth-resume from sessionStorage.
- `McpOverlay.test.tsx` — happy path: catalog → OAuth (mocked) → probe (mocked) → scope → confirm → enabled.
- `McpOverlay.test.tsx` — probe failure → Add disabled; Retry behaviour.
- `BrowseStep.test.tsx` — catalog grid; installed indicator; custom URL field.
- `TestStep.test.tsx` — renders probe shape; retry button.

**Cross-service smoke (`make test`)** — one happy path through facade → backend → mock MCP fixture.

### 3.12 Rollout

- **Flag-free.** `mcp_servers` ALTER is additive; old rows have `last_probe_*` NULL.
- **Zero-downtime migration.** ADD COLUMN x2; metadata-only on Postgres 14+.
- **Backout.** Drop the two columns and the catalog route; the existing flat-form Settings panel remains usable.
- **New Radix dep.** `@radix-ui/react-dialog` adds ~2.5 KB gzipped to design-system bundle. Tracked.
- **Catalog evolution.** Adding entries is a one-line edit + PR; no migration.
- **Probe defaults.** Timeout = 5 s; bump via `MCP_PROBE_TIMEOUT_SECONDS` if tested servers need it.

### 3.13 Open questions

1. **Per-tool scope toggles + Read-only preset.** Design "later." Per-server scope is what we ship; per-tool needs a schema rework (`mcp_server_scopes` table with `(server_id, tool_name, enabled)`).
2. **Catalog as a CRUDable resource.** When the user count of "I want my own catalog" hits >2, we move to a DB table. Until then, file.
3. **Server health monitoring after install.** Design "later." A periodic sweep that re-probes each enabled server and surfaces health is its own PR.
4. **Multi-tenant catalog overlays.** Some workspaces want to _hide_ certain entries (e.g. the IT dept hides Slack-MCP). Defer.
5. **MCP transports beyond HTTP/SSE/WebSocket.** Schema-level enum; widening is one ALTER.

---

## 4 · Acceptance checklist

- [ ] Migration `0017_mcp_test_connection.sql` applies cleanly forward and rolls back.
- [ ] `services/backend/src/backend_app/mcp/catalog.json` ships with ≥12 well-known entries.
- [ ] `GET /v1/mcp/catalog` returns the file with a 5-minute cache header; one-shot test fixture verifies shape.
- [ ] `POST /v1/mcp/servers/{id}/test` returns the `McpProbeResult` shape; persists `last_probe_at` + `last_probe_result_json`; audits.
- [ ] Probe handles OAuth-unauth / OAuth-authed / api-key / none; timeouts at 5 s; SSRF guard active in production mode.
- [ ] Two new `mcp_audit_events.action` constants registered; chain verifier passes.
- [ ] Retention sweeper extended with `mcp_drafts` kind; reaps drafts >24 h.
- [ ] `<Dialog>` primitive added to `@0x-copilot/design-system` wrapping `@radix-ui/react-dialog`; CSS for overlay + content shipped.
- [ ] `<McpOverlay>` mounts from Settings → Connectors → "Add MCP server" CTA (PR 4.3) and from a future composer popover.
- [ ] Five step components (`<BrowseStep>`, `<AuthStep>`, `<TestStep>`, `<ScopeStep>`, `<ConfirmStep>`) render the right state.
- [ ] Wizard state survives OAuth round-trip via sessionStorage keyed by OAuth state param.
- [ ] "Add to workspace" button stays disabled until step 3 returns `reachable: true`.
- [ ] `@0x-copilot/api-types` exports `McpCatalogEntry`, `McpProbeResult`, `McpProbeErrorKind`; `McpServer` extended.
- [ ] Streaming handshake byte-identical pre/post merge.
- [ ] No new event types, no new wire variants, no LangGraph harness changes.
- [ ] `make test` green; backend pytest green; frontend typecheck + build green.

---

## 5 · References

- Design Doc · MCP overlay (5-step wizard) + the **Test connection step before confirm** P1 TODO — bundle at `/tmp/design-doc/0x-copilot/project/Design Doc.html` lines 571-595, 677.
- [`services/backend/migrations/0001_initial_mcp_skills.sql`](../../services/backend/migrations/0001_initial_mcp_skills.sql) — `mcp_servers`, `mcp_auth_sessions`, `mcp_auth_connections`, `mcp_audit_events`.
- [`services/backend/src/backend_app/mcp_oauth.py`](../../services/backend/src/backend_app/mcp_oauth.py) — discovery + PKCE the probe reuses.
- [`services/backend/src/backend_app/http_client.py`](../../services/backend/src/backend_app/http_client.py) — SSRF guard reused for the probe.
- [`apps/frontend/src/api/mcpApi.ts`](../../apps/frontend/src/api/mcpApi.ts) — existing CRUD the wizard calls.
- [`apps/frontend/src/app/App.tsx`](../../apps/frontend/src/app/App.tsx) — OAuth callback handler reused.
- [Model Context Protocol · Specification](https://spec.modelcontextprotocol.io/) — `initialize` handshake the probe performs.
- [Radix UI · Dialog](https://www.radix-ui.com/primitives/docs/components/dialog) — primitive adopted.
- [`docs/new-design/pr-3.3-mcp-discovery-approval-polish.md`](pr-3.3-mcp-discovery-approval-polish.md) — the in-run discovery card; complementary surface.
- [`docs/new-design/pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) — second consumer of `<Dialog>`.
- [`docs/new-design/pr-4.3-settings-ai-and-data.md`](pr-4.3-settings-ai-and-data.md) — Connectors section CTA opens this wizard.
