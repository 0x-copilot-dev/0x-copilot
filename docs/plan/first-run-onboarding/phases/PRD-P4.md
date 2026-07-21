# P4 — Wallet chip + connector-aware Tools popover + per-run web-search toggle

**Phase P4 · size M.** Depends on P0 (shipped), P1 (`FirstRunSurface` + `walletChipSlot`), P3 (`OnboardingComposer` + `FirstRunRunsPort`). **Blocks P6a/P6b** — their 1-click connector rows have no FTUE entry point until this popover exists. Sequence: `P0 → P1 → (P2 ∥ P3) → **P4** → (P6a ∥ P6b) → P7`. (Re-run of the design-pass agent that timed out.)

## Goal

Three shared, port-fed pieces in `packages/chat-surface` (eslint bans window/fetch/localStorage + apps/\* imports — everything via host-injected ports with web + desktop binders that can't share code):

1. **WalletChip** — top-bar `0x7f3C…a92C` + jade dot from a `profile` port over `GET /v1/me/profile`. Server returns the **full** EIP-55 address (`me_profile.py:506,527` uses `display_address`, not the existing `truncated_display_address`), so **truncation lives in the component**.
2. **Connector-aware Tools popover** — replaces the flat `ToolPicker` toggle list (`composer/ToolPicker.tsx:40-55`) with: Web-search toggle · connected connectors (per-chat active/paused) · installable 1-click rows (Safe/Sheets/GitHub) · Custom MCP — reusing the existing MCP substrate (`/v1/mcp/catalog`, `/v1/mcp/servers/install`, per-chat `PATCH …/connectors`, the `mcp_auth_required` run-time consent interrupt — NOT rebuilt).
3. **Per-run web-search toggle** — the popover's toggle (SPEC `webOn`, default true) threaded through `FirstRunRunsPort.createFirstRun` into `request_context.web_search_enabled` (coordinates with the `ftue/backend-prereqs` flag).

## Files

**Create** (`packages/chat-surface/src/onboarding/`): `ports/FirstRunProfilePort.ts`, `ports/FirstRunConnectorsPort.ts`, `providers/FirstRunProfileProvider.tsx`, `providers/FirstRunConnectorsProvider.tsx`, `WalletChip.tsx` (+ `truncateAddress`), `ToolsPopover.tsx`, `ComposerToolsButton.tsx`, `projectFirstRunConnectors.ts` (chat-surface copy of `projectChatConnectors` logic — can't import apps/\*), + `.test.tsx` for each; append classes to `onboarding.css` (P1 owns the file).
**Edit**: `composer/AssistantComposer.tsx` (+additive `toolsTrigger?: ReactNode` slot at :408, no behavior change when unset); `shell/Topbar.tsx` (+additive `walletChip?: ReactNode` between title + ⌘K); `shell/ChatShell.tsx` (thread `walletChip`); `src/index.ts` (barrel block); `api-types` `RuntimeRequestContext` (+`web_search_enabled?: boolean`, mirror the backend field); P3's `FirstRunRunsPort` (+`webSearchEnabled`, `connectorScopes`); P1's `FirstRunSurface` (fill `walletChipSlot`; mount tools button+popover; own `webOn`+`activeConnectorIds`; pass into `createFirstRun`).
**Host binders (can't share code)**: web `apps/frontend/src/features/onboarding/{firstRunProfilePort,firstRunConnectorsPort}.ts` (via `meApi`/`mcpApi`; `beginAuth` = `location.href = auth_url`); desktop `apps/desktop/renderer/onboarding/{…}.ts` (via `transport.request` to the same paths; `beginAuth` opens the OAuth URL in the **external browser** via main `openExternal` — never navigate the Electron renderer); `App.tsx` + `bootstrap.tsx` pass `walletChip` into ChatShell.

## Key signatures

- `truncateAddress(addr) => addr.slice(0,6)+"…"+addr.slice(-4)`. `WalletChip({address, chainName?, connected?})` → renders `null` when `address===null` (email/Google users; chip is SIWE-only).
- `FirstRunProfilePort.get(): Promise<WalletProfileView{walletAddress|null, chainId, chainName, authMethod, emailIsPlaceholder}>`.
- `FirstRunConnectorsPort { listServers, listCatalog, installFromCatalog(slug, oauthClient?), addCustomServer(url, oauthClient?), beginAuth(serverId) }` (typed on api-types `McpServer`/`McpCatalogEntry`/`McpOAuthClientConfigRequest`).
- `ToolsPopover({ open, onClose, webSearchEnabled, onToggleWebSearch, activeConnectorIds, onToggleConnector, onConnectCatalog, onAddCustom, portalTarget? })` (host-owned portal — package has no `document`).
- `createFirstRun` (P3, extended) `{ userInput, model?, attachments?, webSearchEnabled, connectorScopes? }`.

## Wiring notes

- **No conversation at toggle time** — FTUE has no `conversation_id` until send. Hold connector selection in composer `activeConnectorIds`; on send, `createFirstRun` receives `connectorScopes` (active ids → `default_scopes`) applied via PATCH `…/connectors` on the freshly-created conversation (or seeded into `request_context.connector_scopes`). Do NOT fabricate a conversation just to PATCH.
- **1-click connect** mirrors `ChatScreen.onMcpInstallCatalog` (`:1317-1375`): `requires_pre_registered_client` → open the custom-config form (keyless install 422s); else `installFromCatalog(slug)` → `beginAuth`. First-use tool consent stays the run-time `mcp_auth_required` HITL card — the popover "connect" is workspace-authorize only.
- **Web-search default TRUE everywhere** — the flag only _disables_ for that run (no regression to today's always-on).
- Additive `Topbar.walletChip`/`AssistantComposer.toolsTrigger` slots: absent ⇒ existing layout byte-identical (snapshot-guard).

## Acceptance

Top bar shows `0x{4}…{4}` + jade dot for SIWE accounts, nothing for email/Google. Tools pill opens the connector-aware popover with correct SPEC copy (`1-click connect · you approve first use`, `{n} on · none required`). 1-click installs + starts OAuth (web redirect / desktop external browser); pre-registered vendors route to the config form. Web-search OFF disables the tool for that run; P3 ack line truthful. No window/fetch/localStorage/apps/\* import in any onboarding file (eslint green); both host binders exist, don't share code. chat-surface + api-types + frontend + desktop typecheck green.

## Open questions

1. `web_search_enabled` field shape — confirm `request_context.web_search_enabled: bool` (recommended) vs top-level vs `feature_flags`; keep the mapping in the host binder, default true.
2. Custom-MCP entry — inline URL+optional-OAuth form (recommended, reuse `addCustomServer`) vs route to Settings → Connectors.
3. Persistent-shell wallet chip — P4 builds the additive `Topbar.walletChip` slot but leaves host wiring optional; confirm both hosts light it in P4 or defer the workspace chip.
4. "Featured" 1-click set — all installable catalog entries vs a curated Safe/Sheets/GitHub allowlist (recommended: featured subset + "more in Settings").
