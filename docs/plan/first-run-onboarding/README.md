# First-Run Onboarding (FTUE) — Master Plan

**Status:** Planning · **Branch:** `claude/0xcopilot-first-run-onboarding-d7eb30` · **Target:** 1:1 parity with the `0xCopilot First Run` design + full backend support.

Design source: Claude Design project **`Copilot`** (`73f810d9-7b77-4849-9087-f7f8e366c48a`), files `0xCopilot First Run.html` / `copilot-firstrun.jsx` / `copilot-firstrun.css`. Verbatim copies are vendored under [`design-source/`](./design-source/) as the parity reference.

Scope decisions (locked with the requester):

1. **Hosted "25 free runs" trial lane — BUILD** (app-owned/proxied credits + free-run ledger + per-user default model).
2. **Safe{Wallet} + Google Sheets connectors — BUILD** (Safe MCP + wallet-signing path; Sheets read/write MCP).
3. **Faithful shared build** — the full 3-state FTUE lives in `packages/chat-surface` behind ports, mounted by both the desktop and web hosts (SSOT architecture per `packages/chat-surface/CLAUDE.md`).

---

## 1. What the screen is

One surface (`.fr`) with a persistent **top bar** (brand · live wallet chip · `skip → open the workspace`) and **footer** (`v2.1.0 · local build` · privacy line), cycling through three states:

### State A — the gate: "First, give it a model."

Sub: _"The only required choice — switch anytime."_ Two cards:

- **Download the local model** — `Qwen 3 4B · 5.6 GB · free forever`, "Runs on this machine. Nothing you send ever leaves it." → primary **Start download** (streams %, and _"type your first prompt while it downloads"_).
- **Bring your own key** — `Anthropic · OpenAI · OpenRouter`, "Frontier models, ready in ~30 seconds. Keys stay in your OS keychain." → **Add a key** reveals an inline form (provider tri-toggle → `sk-…` password → _"stored in your OS keychain — never uploaded"_ → **Connect**).
- Escape hatch: _"just exploring? hosted starter — 25 free runs, no key →"_.

### State B — the composer: "What should we run first?"

The real app composer (textarea · attach · **model pill** · **tools pill** · send · `⏎ send · ⇧⏎ line`) plus three **starter chips**:

- _Watch a wallet_ → "Watch 0x7f3C…a92C and alert me on any transfer over $500. Keep running in the background."
- _Draft a launch thread_ → "Draft a 6-post launch thread… Ask me 3 questions first, then write it."
- _Explain a CSV_ → "Explain this CSV… chart the top movers." (pre-attaches `airdrop-claims.csv`)

Popovers: **attach** (upload / screenshot / project files), **model** (Local · Haiku starter trial · BYO keys), **tools** (Web search toggle · Safe{Wallet}/Sheets/GitHub 1-click · Custom MCP), **key** (inline add-key).

### State C — the acknowledgment: "Starting your first run"

(or _"Queued — starts when the model lands"_ when the local model is still downloading). Echoes three lines — model · tools · privacy — then hands off to the workspace (`ChatShell` → `RunDestination`) after ~1.5s.

Full component/prop/copy inventory: [`design-source/SPEC.md`](./design-source/SPEC.md).

---

## 2. Token parity — a rename, not a re-theme

The design tokens are **byte-identical** to the shipped `packages/design-system/src/styles.css` "quiet v2" set. Parity = mapping the design's short names to the design-system's semantic names (NEVER hard-code hex — design-system is the SSOT):

| Design `copilot.css`                             | Design-system `styles.css`                                                  |
| ------------------------------------------------ | --------------------------------------------------------------------------- |
| `--ink · --ink2 · --panel · --panel2 · --panel3` | `--color-bg · -bg-elevated · -surface · -surface-muted · -surface-elevated` |
| `--tx · --tx2 · --mut · --mut2`                  | `--color-text · -text-strong · -text-muted · -text-subtle`                  |
| `--line · --line2 · --line3`                     | `--color-border · -border-strong · -border-stronger`                        |
| `--accent · --accent-hi · --accent-ink`          | `--color-accent · -accent-strong · -accent-contrast` (sky `#5fb2ec`)        |
| `--jade · --amber · --ember`                     | `--color-success · -warning · -danger`                                      |
| `--disp/--body · --mono`                         | `--font-display/--font-sans · --font-mono`                                  |

The design's per-provider dot colors (Anthropic `#d97757` etc.) are _data_, not the app accent — keep them as inline swatch values. One-accent discipline (sky only) holds.

---

## 3. Architecture

### 3.1 Where it mounts

- **Presentational surface (SSOT):** `packages/chat-surface/src/onboarding/` — a new `FirstRunSurface` (+ `Gate`, `KeyForm`, `OnboardingComposer`, `Acknowledgment`, `WalletChip`, `SuggestionChips`) built from design-system tokens/primitives and the existing composer. All I/O via **ports** (no bare `fetch`/`window`/`localStorage` — eslint-banned in this package).
- **Ports** (host-injected): `firstRunStore` (get/set completion flag), `providerKeys` (list/set/validate), `localModels` (status/pull SSE/list), `trial` (start/status), `runs` (createConversation/createRun/stream), `profile` (wallet chip), `connectors` (catalog/install/scope), `navigate`/`openSettings`/`complete`.
- **Desktop host:** `apps/desktop/renderer/` binds the ports to the facade + main-process IPC and mounts the gate at the seam in `bootstrap.tsx` (between `SignInGate` signed-in and `ChatShellForSession`), modeled on `BootGate`/`SignInGate`.
- **Web host:** `apps/frontend/src/features/onboarding/` binds the same ports to its API clients + `localStorage`.

### 3.2 The first-run flag (net-new)

No FTUE or completion flag exists today (desktop flow is Boot → Sign-in → Run cockpit). Add a per-user/per-install flag:

- **Desktop:** main-process JSON at `userData/settings/first-run.json` (versioned, chmod-600), modeled on `apps/desktop/main/services/secure-storage-policy.ts`, exposed over IPC (mirror `registerSecureStorageIpc`, `apps/desktop/main/index.ts:243`). Key it by `workspaceId`/account sub so it's per-user.
- **Web:** `KeyValueStore` (localStorage) namespaced by user id.
- **Gate condition:** show FTUE when `!firstRunComplete`. "Skip", finishing setup, or sending the first run all set it. Returning users bypass entirely.

### 3.3 The handoff target

"0xCopilot App v3.html" = the real `ChatShell` (`packages/chat-surface/src/shell/ChatShell.tsx`) mounted by `ChatShellForSession` (`apps/desktop/renderer/bootstrap.tsx:115`), default destination `run` → `RunDestination`. The ack screen calls the `complete`/`navigate` port; it does NOT hard-navigate to an HTML file.

---

## 4. Backend capability inventory (evidence-backed)

Legend: ✅ exists on `main` · 🟡 partial · ❌ net-new.

| Capability                                                                            | State                                            | Anchor                                                                                       |
| ------------------------------------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| BYOK keys (openai/anthropic/google/openrouter) + TokenVault + `key_hint` + live-check | ✅                                               | `services/backend/.../provider_keys/{store,service,routes}.py`; `/v1/settings/provider-keys` |
| Run model resolution + credential gate → `CONFIGURATION_ERROR` CTA (#158)             | ✅                                               | `agent_runtime/execution/models.py:47-127`; `RunComposer.tsx`                                |
| Local-model pipeline (HF GGUF → Ollama, SSE pull w/ bytes/speed/ETA, delete)          | ✅ (gated `enable_local_models=false`)           | `runtime_api/local_models/*`; `/v1/local-models/*`; facade proxy                             |
| On-device inference (Ollama, keyless, gate-bypass)                                    | ✅                                               | `execution/openai_compat.py:122`; `models.py:80`                                             |
| models.dev catalog SSOT `/v1/agent/models`                                            | ✅ (client still has ~8 hardcoded lists)         | `agent_runtime/api/model_catalog.py`                                                         |
| MCP registry: catalog, install, OAuth (discovery/DCR/PKCE), TokenVault                | ✅                                               | `backend_app/mcp_catalog.py`, `mcp_oauth.py`, `service.py`; `/v1/mcp/*`                      |
| Per-chat connector scope (`PATCH …/connectors`, `paused_connectors` gate)             | ✅                                               | facade `app.py:477`; `capabilities/mcp/permissions.py`                                       |
| HITL: `mcp_auth_required` interrupt + approvals API + decision/resume                 | ✅                                               | `capabilities/mcp/middleware/auth_mcp.py`; `/v1/agent/approvals/*`                           |
| Run creation + SSE (`conversations` → `runs` → `stream`)                              | ✅                                               | `/v1/agent/{conversations,runs,runs/{id}/stream}`                                            |
| SIWE wallet identity; address on `/v1/me/profile`                                     | ✅                                               | `identity/siwe.py`; `routes/me_profile.py`                                                   |
| Composer (model pill / tools pill / attach / send) as props                           | ✅                                               | `packages/chat-surface/src/composer/AssistantComposer.tsx`                                   |
| Attachments (inline data-URL, no server upload)                                       | ✅                                               | `features/chat/runtime/attachments/*` — verify CSV accept path                               |
| **First-run flag / gate**                                                             | ❌                                               | —                                                                                            |
| **Curated "Qwen 3 4B" download preset**                                               | ❌ (pipeline exists; catalog host-injected `[]`) | `LocalModelsPage.tsx:99`                                                                     |
| **Hosted trial lane** (credits + ledger + per-user default)                           | ❌                                               | —                                                                                            |
| **Safe{Wallet} MCP + signing path**                                                   | ❌                                               | only SIWE login exists                                                                       |
| **Google Sheets read/write MCP**                                                      | ❌ (gdrive excludes cell edits)                  | `connectors/desktop_profiles.yaml:118`                                                       |
| **Per-run web-search toggle**                                                         | ❌ (tool is always-on)                           | `runtime_worker/dependencies.py:53`                                                          |
| **Top-bar wallet chip**                                                               | ❌ (data real; no chip)                          | `Topbar.tsx`, `AppRail.tsx`                                                                  |
| **Shared suggestion-chips component**                                                 | ❌ (web-only data)                               | `features/chat/prompts/index.ts`                                                             |

---

## 5. User journeys

1. **Local-first (privacy):** launch → sign in → gate → _Start download_ (Qwen streams %) → composer appears immediately (model pill "Qwen 3 4B · 41%") → pick chip / type → send → ack _"Queued — starts when the model lands"_ → workspace; run auto-fires at 100%.
2. **BYOK (~30s):** gate → _Add a key_ → provider → paste `sk-…` → live-validate → composer with real model → send → ack _"Starting your first run"_ → workspace.
3. **Trial (explore):** gate → _25 free runs_ → composer "Haiku starter" → send → ack → workspace; each run decrements the free-run ledger; exhaustion routes to the gate's BYOK/local paths.
4. **Skip:** _skip → workspace_ → flag set → Run cockpit's existing "Set up your model" empty-state.
5. **Returning:** flag set → gate skipped → straight to workspace.

Full step/endpoint sequences per journey: [`JOURNEYS.md`](./JOURNEYS.md).

---

## 6. Phased roadmap (PR-sized)

Each phase = its own PRD (written at execution time) + `STATUS.md` tick. Frontend phases are substrate-shared (chat-surface) with desktop+web host bindings.

| Phase  | Title                                                             | Size | Gates                                                                                       |
| ------ | ----------------------------------------------------------------- | ---- | ------------------------------------------------------------------------------------------- |
| **P0** | First-run flag + gate seam + skip                                 | S    | main-process `first-run.json` + IPC; web KV; `bootstrap.tsx` gate; skip path                |
| **P1** | Gate surface + BYOK card + inline key form                        | M    | wired to `/v1/settings/provider-keys`; token-mapped CSS; `FirstRunSurface` scaffold         |
| **P2** | Local-model card + curated Qwen 3 4B preset                       | M    | curated preset config; `enable_local_models` desktop-default decision; SSE progress in-gate |
| **P3** | Onboarding composer + suggestion chips + first-run creation + ack | M    | mount `AssistantComposer`; shared `SuggestionChips`; two-step create; ack + handoff         |
| **P4** | Wallet chip + Tools popover parity + web-search toggle            | M    | `/v1/me/profile` chip; reuse `ToolPicker`; new per-run web-search context flag              |
| **P5** | **Hosted trial lane**                                             | L    | credits source + free-run ledger + enforcement + per-user default model — see §7.1          |
| **P6** | **Safe{Wallet} + Sheets connectors**                              | L    | Safe MCP + approval-gated signing; Sheets read/write MCP — see §7.2 / §7.3                  |
| **P7** | E2E parity pass + verification                                    | M    | per-journey live-stack tests; ui-design-reviewer parity audit vs the mock                   |

**P0–P4 delivers a faithful, fully-working FTUE for local + BYOK + skip.** P5/P6 add the heavy, decision-gated subsystems.

---

## 7. Net-new backend design sketches

### 7.1 Hosted trial lane (P5) — highest risk

Goal: keyless users get N (=25) real runs on an app-owned "Haiku starter" model, then convert to BYOK/local.

- **Credits source (DECISION):** app-owned provider key held server-side (an operator secret, never per-user) OR a metered proxy. Must never be exposed to the client or a run body.
- **Ledger:** `trial_run_ledger(org_id,user_id,used,limit,reset_policy)` — a new backend store + migration. Decrement on run-create for the trial model; enforce atomically to prevent races/abuse.
- **Enforcement point:** extend the credential gate (`execution/models.py`) so provider `= trial` resolves the app credential _iff_ ledger allows; on exhaustion raise a typed `TRIAL_EXHAUSTED` error the composer renders as "add a key / download local".
- **Per-user default model:** new persisted per-user default (none exists server-side today) so the trial model sticks across the session.
- **Abuse:** per-account + per-install rate limits; tie to a verified session (never anonymous). **Product/billing sign-off required** (this spends real inference money).
- Facade: `POST /v1/trial/start`, `GET /v1/trial/status`.

### 7.2 Safe{Wallet} connector (P6) — highest risk

Goal: "propose & sign transactions". Compose over the existing MCP substrate + SIWE wallet.

- **Server:** register a Safe MCP server via `create_server`/a new catalog entry (`mcp_catalog.py`); tools = read Safe state + **propose** a transaction (build calldata) — NOT auto-execute.
- **Signing path (net-new):** signing routes through the user's wallet in the renderer (extend the SIWE/EIP-1193 provider used by `WalletSignIn.tsx`); the agent only _proposes_. Every signature is an explicit user action.
- **Approval gate:** wire proposals through the existing `approval_requested` HITL interrupt + `ConnectorConsentCard`, `approval: per_call`. **No transaction is ever signed without an explicit in-UI user confirmation.** (Claude never executes trades/transfers itself — the product surfaces a proposal; the human signs.)
- **Security sign-off required:** chain allowlist reuse (`SIWE_ALLOWED_CHAIN_IDS`), amount/destination review UI, simulation before sign.

### 7.3 Google Sheets connector (P6)

Real read/write Sheets MCP (gdrive explicitly excludes cell edits). OAuth via existing MCP DCR/pre-registered flow; scopes for read+write; `approval: session` for reads, `per_call` for writes.

### 7.4 Web-search per-run toggle (P4)

Add a run-context flag the always-on `WebSearchToolRegistry` (`runtime_worker/dependencies.py:53`) honors, threaded from the composer tools popover through the run request.

### 7.5 Curated Qwen 3 4B preset (P2)

Config-only over the built pipeline: host-inject `availableModels` (`LocalModelsPage.tsx:99`) with the curated card (repo/quant/size). Decide the desktop `enable_local_models` default (currently off).

### 7.6 GitHub 1-click (P4/P6)

Existing entry needs a pre-registered OAuth app (`requires_pre_registered_client=true`, read-only today) to be genuinely 1-click; add write scopes if the FTUE promises them.

---

## 8. Open decisions / risks (need sign-off before the gated phases)

- **P5:** source of hosted credits (app key vs proxy), free-run limit + reset policy, billing owner, abuse controls. _(Business + security.)_
- **P6 Safe:** signing UX, simulation, chain/amount guardrails. _(Security.)_ Principle: propose-only agent, human-signs, per-call approval.
- **P2:** desktop default for `enable_local_models`; whether Qwen 3 4B (or a smaller default) is the shipped preset.
- **Parity vs. reality:** connector _names_ in the mock (Safe/Sheets/GitHub) are being built to match; confirm no other mock connector is implied.
- **Verify at impl:** CSV attachment accept path (`features/chat/runtime/attachments/file.ts:15`); finish the two-tier model picker (`ModelPicker.tsx` still hardcodes 3 models) so the gate's model list is catalog-driven.

## 9. Verification

Per-journey tests on the live desktop stack (hermetic real-graph run→stream, per `docs/plan/verification/`), plus a `ui-design-reviewer` parity audit of each state against `design-source/`. Never mark a phase done until code + wiring + tests + STATUS all agree.
