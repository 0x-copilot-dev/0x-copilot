# First-Run — user journeys (endpoint sequences)

Identity is server-owned: the ai-backend overrides `org_id`/`user_id` from trusted headers, so run/conversation bodies carry only content. All calls go through the **facade** (`:8200` / `app://` IPC), never `backend`/`ai-backend` directly.

## Precondition — sign in (unchanged)

Boot gate (`BootGate`) → `SignInGate` picker ("Welcome to 0xCopilot": wallet / Google / local). On `signed-in`, the new **first-run gate** checks the `firstRunStore` port: complete → mount shell; incomplete → FTUE.

## J1 — Local-first (privacy)

1. Gate → **Start download**. `GET /v1/local-models/status` (Ollama up? else show install steps).
2. SSE `POST /v1/local-models/pull {repo,quant}` (curated Qwen 3 4B preset) → progress `{bytes_total,completed,speed_bps,eta_seconds}` drives the model pill `Qwen 3 4B · N%`.
3. Composer shows immediately (State B). User types / picks a chip.
4. Send with download in flight → **Ack** _"Queued — starts when the model lands"_.
5. On 100%: `POST /v1/agent/conversations` → `POST /v1/agent/runs {conversation_id,user_input,model:"<qwen ollama id>"}` → SSE `…/stream`.
6. `firstRunStore.set(complete)` → navigate to workspace, run already streaming.

## J2 — BYOK (~30s)

1. Gate → **Add a key** → provider → paste `sk-…`.
2. `PUT /v1/settings/provider-keys/{provider} {api_key}` → format-gate → live-check (`passed`/`skipped_unreachable`/`api_key_rejected_by_provider`) → TokenVault-encrypt + audit; response `{key_hint,live_check}`.
3. Composer with the real model (`selectedModel` from `/v1/agent/models`).
4. Send → `POST /v1/agent/conversations` → `POST /v1/agent/runs {conversation_id,user_input,model}` → SSE.
5. **Ack** _"Starting your first run"_ → set flag → workspace.

## J3 — Trial (explore) — SHELVED (not in v1)

Dropped from v1. If revived, it is **not** an open no-key trial: eligibility requires the SIWE-verified wallet to hold **≥ 50k $CPILOT** (server-side on-chain balance check), then the same run-create path as J2 against an app-owned credit + ledger. See README §7.1 for the parked design.

## J4 — Skip

1. Top-bar **skip → workspace** → `firstRunStore.set(complete)` → mount shell.
2. Lands in `RunDestination` empty-state; if no model configured, the existing "Set up your model" CTA (#158) deep-links Settings → Provider keys / Local models.

## J5 — Returning user

Flag set → gate never renders → straight to `ChatShell` → `RunDestination`.

## Cross-cutting — Tools popover (any composer journey)

- **Web search** toggle → per-run context flag (P4 net-new) honored by `WebSearchToolRegistry`.
- **Connector 1-click** → `POST /v1/mcp/servers/install {slug}` (or `create_server` for custom). First agent use raises `mcp_auth_required` interrupt → `auth_url` → `GET /v1/mcp/oauth/callback` → `AUTHENTICATED`. Per-chat scope via `PATCH /v1/agent/conversations/{id}/connectors`.
- **Safe{Wallet}** (P6): agent _proposes_ a tx → `approval_requested` interrupt → `ConnectorConsentCard` (`per_call`) → user signs in-wallet (renderer EIP-1193). Agent never signs; human confirms every signature.

## Attachments

Client-inline base64 `data:` URL as a `RunContentPart{type:"file"}` in the run body (no upload endpoint). Verify CSV routes through the text/composite adapter (`features/chat/runtime/attachments/file.ts:15` accept list is office+pdf).
