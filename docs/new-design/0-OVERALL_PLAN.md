# Atlas — Enterprise Search · Implementation plan

## Context

We pulled the Anthropic Design handoff bundle for "Atlas — Enterprise Search" (`/tmp/design-doc/enterprise-search/`). The bundle's `Design Doc.html` is the authoritative spec: it inventories every page, flow, primitive, and decision. We need to land it incrementally against the existing monorepo (`apps/frontend`, `services/ai-backend`, `services/backend`, `services/backend-facade`, `packages/api-types`, `packages/design-system`) without breaking the existing assistant‑ui chat surface, SCIM/auth, MCP OAuth, or strict‑reads encryption already in production paths.

The design covers six surfaces (Login, Main app, Settings, MCP overlay, Usage overlay, Share popover) and five flows (Search & summarize, Launch full agent, Approval, Connector scoping, Sharing). Key wire requirements: live citation registry, per‑chat connector scope, draft artifact, two‑stage approvals, subagent discovery, sources‑restricted recipient view, and a workspace-pane right rail.

This is a multi-PR architecture plan. Each wave is independently shippable; later waves depend on earlier contract drops.

## Confirmed user decisions

| Decision                     | Choice                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Citation wire                | **Live registry + inline `[c…]` tokens** (Sources panel updates as docs arrive; chips render inline as text streams) |
| Login                        | **Full rebuild** matching email‑first / IdP‑discovery / MFA / workspace‑picker spec                                  |
| Brand accent                 | **Switch default to Atlas orange `#d97757`** + add 8‑swatch picker in Settings → Appearance                          |
| Per‑chat connector scope     | **New `agent_conversations.enabled_connectors` JSONB** + `PATCH /v1/agent/conversations/{id}/connectors`             |
| Workspace pane default state | **Auto‑open when there are sources/agents**, stay closed for tool‑free chats (already established)                   |

## What already exists vs. what's missing

Detailed inventory was produced by three parallel Explore agents. Bottom line:

- **Already shipped & re‑usable**: assistant‑ui chat shell, SSE stream + replay + cancel + sequence_no, approvals (single‑actor), ask‑a‑question, MCP discovery card (`MCP_AUTH_REQUIRED`), subagent events, presentation projection (`activity_kind`/`display_title`/`summary`/`status`), connector scopes header, SCIM provisioning, audit log + SIEM export, field‑level encryption, MCP OAuth + token vault, conversations + runs + messages persistence, Settings rail (5 sections), DetailsPanelHost overlays for `/context` and `/usage`.
- **Missing wire**: citations (any model), draft artifact event/endpoint, two‑stage approval chain, subagent discovery endpoint, per‑chat connector scope persistence, workspace defaults (model/connectors/retention) columns, sharing schema (zero today), conversation soft‑delete + retention column, per‑tool MCP scope toggles, audit‑log export endpoint surfaced in UI.
- **Missing UI**: topbar chrome (crumb, connectors pill, usage meter, share popover, panel toggle), sidebar enhancements (grouping, search, pulse, user/workspace card), workspace pane right rail with 5 tabs, citation chip primitive, login email‑first flow, 6 of 10 Settings sections, MCP overlay flow, Usage overlay refit, Share popover, hash routing in Settings, ⌘K/⌘\/⌘↩ keymap.

## Architecture principles for this work

1. **Service boundaries are hard** (per `CLAUDE.md`). Frontend never calls `backend` or `ai-backend` directly — only `backend-facade`. New facade routes proxy to the right owner.
2. **Public contracts move first.** Any new wire field lives in `packages/api-types` (TS) and the facade's pydantic schemas before producers/consumers ship.
3. **Encryption + RLS stay invariants.** Every new persisted column on tenant data goes through `FieldCodec` if it can hold user content, and through the existing RLS policies (migration 0008).
4. **Audit on every privileged write.** Sharing, approval forwarding, scope changes, MCP install, and connector toggles all emit audit events through the existing append‑only chain (backend `identity_audit_events` or ai‑backend `runtime_audit_log`).
5. **Per‑surface CLAUDE.md rules win** when they conflict with this plan (e.g. frontend must use Streamdown for assistant markdown, must use the projection fields, never reach below `backend-facade`).
6. **No duplicate primitives.** Re‑use design‑system `Button`/`Card`/`Field`/`Switch`/`Badge` and existing icon set. Add new primitives (`StatusPill`, `ConnectorChip`, `AppIcon`, `IconButton`, `ConnectorPopover`, `Menu`) into `packages/design-system` once and only once.

## PR matrix

Waves are roughly sequential; PRs inside a wave can land in parallel.

### Wave 0 — Foundations (1 small PR)

**PR 0.1 · Tokens, primitives, hash routing scaffold** (S)

- `packages/design-system/src/styles.css`: align dark default — `--color-accent` → `#d97757`, `--color-bg` → `#0f0f10` (warmer almost-black), `--color-surface` → `#1a1a1c`, status palette to `--success #6ec48c / --warn #d9a857 / --danger #d97777`. Keep gold + orange + 6 more in a swatch table consumed by Settings.
- New design-system primitives: `IconButton` (28×28 ghost), `StatusPill` (dot + label, three tones, accent‑pulse on running), `ConnectorChip` (app icon + name pill, four states), `AppIcon` (colored circle with brand letter), `Menu` (anchored dropdown w/ mousedown‑outside dismissal). Co‑locate stories + tests under `packages/design-system/src`.
- `apps/frontend/src/app/App.tsx`: extend the path router with hash sync (`/settings#connectors` etc.) — wire the listener once, expose `useSettingsSection()` to the Settings screen.

### Wave 1 — Backend wire & persistence enablers (5–6 PRs, can land in parallel after 0)

**PR 1.1 · Citations live registry** (M) ⚠ blocker for chat polish

- `packages/api-types/src/index.ts`: add `RuntimeCitation`, `CitationSourceRef`, new event variant `source_ingested` and a `citations: CitationSourceRef[]` array on `RuntimeFinalResponseEvent`. Mark `model_delta.text` as containing `[c<id>]` tokens.
- `services/ai-backend/src/runtime_api/schemas/common.py` + `schemas/events.py`: register new event type, projector emits `activity_kind=tool` w/ `summary` "Read 3 docs in Notion" — but also a structured `payload.citation_ref`. Update `presentation_templates.py` so source-bearing tool calls get the new payload shape.
- `services/ai-backend/src/agent_runtime/capabilities/tools/`: extend the search/read tools to register sources via a new `CitationLedger` ([persistence/records/citations.py]) keyed by `(run_id, citation_id)`. Each tool result emits one `source_ingested` per cited doc and a downstream `[c<id>]` token in the model's prompt context.
- `services/ai-backend/migrations/0014_runtime_citations.sql`: `runtime_citations(citation_id pk, run_id, conversation_id, org_id, source_connector, source_doc_id, source_url, title text encrypted v1, snippet text encrypted v1, freshness_at, created_at)` with RLS by org_id + index on `(conversation_id, created_at)`.
- `runtime_worker`: stream the new event before the `model_delta` chunk that first references it.

**PR 1.2 · Per‑chat connector scope persistence** (S)

- `services/ai-backend/migrations/0015_conversation_connector_scope.sql`: add `enabled_connectors JSONB DEFAULT '{}'::jsonb` and `scope_updated_at TIMESTAMPTZ` to `agent_conversations`.
- `services/ai-backend/src/runtime_api/http/routes.py` + `schemas/conversations.py`: new `PATCH /v1/agent/conversations/{id}/connectors` taking `{ "scopes": { connector_id: ["scope_a","scope_b"] | null /* paused */ } }`. Audit through `runtime_audit_log` (`action=conversation.connectors.update`).
- `services/ai-backend/src/runtime_api/http/runs.py`: when creating a run, if request omits `connector_scopes`, derive from the conversation's `enabled_connectors`. Header‑provided value still wins (existing override path stays).
- `services/backend-facade`: add the proxy route + identity headers.
- `packages/api-types`: `ConversationConnectorScope`, request/response shapes.

**PR 1.3 · Draft artifact** (M)

- `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/draft.py`: new tool `produce_draft(target_connector?, content, sections?, citations[])`. Emits new event `DRAFT_UPDATED` with `{draft_id, version, title, sections, citations, target_connector?, target_metadata?}`.
- Migration `0016_runtime_drafts.sql`: `runtime_drafts(draft_id pk, conversation_id, run_id, org_id, version int, title, content_json encrypted v1, target_connector, target_metadata, status enum(draft/sent/discarded), updated_at)`. Append-only; new versions insert.
- `runtime_api/http/routes.py`: `GET /v1/agent/conversations/{id}/drafts` (latest per draft_id) + `POST /v1/agent/drafts/{id}/send` (kicks the matching connector tool through approval). Mirror in `backend-facade`.
- `packages/api-types`: `Draft`, `DraftSection`, `DraftSendRequest`.

**PR 1.4 · Two‑stage approvals (forward chain)** (M)

- `services/ai-backend/migrations/0017_approval_forwarding.sql`: add `forward_to_user_id`, `forward_to_external_id`, `chain_parent_approval_id`, `forwarded_at`, `forwarded_decided_at` to `runtime_approval_requests`. Add CHECK preventing self‑forward.
- `runtime_api/schemas/approvals.py`: extend `ApprovalDecisionRequest` with optional `forward_to: { kind: "workspace_user", id }` (and `external_email` for v2). When set + decision=`approved`, create a child approval row, emit `APPROVAL_FORWARDED` event, leave run in `WAITING_FOR_APPROVAL`.
- `runtime_worker/handlers/approval.py`: on resolution of a forwarded approval, walk the chain and proceed only when leaf is approved.
- Notifications: piggy‑back on the existing notification adapter (Settings → Notifications matrix; ship as no‑op if matrix isn't built yet).

**PR 1.5 · Subagent discovery + workspace pane data feeds** (S)

- `runtime_api/http/routes.py`: `GET /v1/agent/conversations/{id}/subagents?status=running|recent` — drives the Workspace pane Agents tab. Reads from `runtime_subagent_results` + open `agent_runs` with `parent_run_id` (already persisted by `runtime_worker/stream_subagents.py`).
- `GET /v1/agent/conversations/{id}/sources?run_id?` — derived from `runtime_citations` for the Sources tab when no live registry is active (post‑run reads).
- `packages/api-types`: `SubagentSummary`, `SourceCard`.

**PR 1.6 · Workspace defaults + conversation lifecycle** (S)

- `services/backend/migrations/0017_workspace_defaults.sql`: `workspace_defaults(org_id pk, default_model, default_connectors jsonb, retention_days int, updated_at)`. Read/write via `/internal/v1/workspace/defaults`. Surface through facade `/v1/workspace/defaults` (admin‑only).
- `services/ai-backend/migrations/0018_conversation_lifecycle.sql`: add `deleted_at`, `retention_days_override`, `folder text`, `parent_conversation_id` (forward‑declared for sharing fork lineage; nullable until Wave 6).
- Conversation list endpoint groups by `Today / Yesterday / Earlier` — implement on the facade so the FE doesn't need timezone math (use `X-Enterprise-Timezone` from session).

### Wave 2 — Main app shell (FE) (3 medium PRs, parallelizable)

**PR 2.1 · Topbar chrome + status pill + thinking‑depth** (M)

- New components in `apps/frontend/src/features/chat/components/shell/`: `TopbarCrumb`, `TopbarStatusPill` (uses design‑system `StatusPill`), `ConnectorsPill`, `UsageMeter`, `ShareButton` (slot‑only here; wired in PR 4.4), `PanelToggle`, `ModelPill`, `ThinkingDepthControl` (Fast / Balanced / Deep).
- Replace the assistant‑ui default header with a custom layout that consumes existing `AssistantThread` props + `runUiState`.
- Connect `ConnectorsPill` to the new `useConversationConnectors()` hook (reads `enabled_connectors` via PR 1.2; shows up to 4 app icons + count caret).
- Topbar model pill addresses the design's P0 TODO ("Topbar model pill").

**PR 2.2 · Sidebar + user card + workspace switcher + ⌘K/⌘N/⌘\/⌘↩** (M)

- `AssistantThreadList` wrapper: render groups (Today/Yesterday/Earlier — driven by facade grouping from PR 1.6), search input filtering by title, live "pulse" badge for active run thread.
- New `apps/frontend/src/features/chat/components/sidebar/UserCard.tsx`: avatar + name + workspace · role + chevron → popover with workspace switch / settings / sign out (wires through `apps/frontend/src/features/auth`).
- Global `useKeymap()` hook in `apps/frontend/src/app/keymap.ts`: ⌘N (new chat — already has handler; rebind), ⌘K (focus chat search input), ⌘\ (toggle sidebar), ⌘↩ (approve when an `ApprovalTool` is the focused/visible card — emits a synthetic click on its primary button).
- Auto‑collapse: sidebar at <820px, workspace pane at <1100px (already wired for sidebar via App.tsx; extend for workspace pane).

**PR 2.3 · Welcome state + thread polish** (S)

- `ThreadWelcome.tsx`: time‑of‑day greeting (Good morning / afternoon / evening / Working late), 4 suggestion cards with category eyebrows (DRAFT / SUMMARIZE / FIND / COMPARE) — copy in `prompts.ts`. Drop the connectors strip per design.
- Polish: `AssistantMessage` strips bubble (flush left, paragraph rhythm); `UserMessage` keeps bubble; activity rendering already uses `presentationHelpers` — confirm no event‑name‑prefix derivation (per frontend CLAUDE.md).

### Wave 3 — Chat semantics (FE) (4 PRs)

**PR 3.1 · Citation chips + Sources tab** (M) — depends on PR 1.1

- New `apps/frontend/src/features/chat/components/citations/`: `CitationChip` (superscript number + connector glyph; hover tooltip; click → opens Workspace pane Sources tab and scrolls to row), `CitationRegistry` (run‑scoped store fed by `source_ingested`), Streamdown plugin that turns `[c<id>]` tokens into `<CitationChip>` while preserving streaming partial tokens.
- `chatModel/eventReducer.ts`: handle `source_ingested`, build per‑run citation state.
- `MarkdownText.tsx`: integrate the plugin without breaking link handling.

**PR 3.2 · Workspace pane right rail (Sources / Agents / Draft / Approvals / Skills)** (L)

- New `apps/frontend/src/features/chat/components/workspace/`:
  - `WorkspacePane.tsx` (host, tabbed, collapsible, auto‑open when sources/agents present per user decision).
  - `SourcesTab.tsx` (driven by citation registry + `GET .../sources` for archive reads).
  - `AgentsTab.tsx` (driven by `GET .../subagents` from PR 1.5; running progress bars, recent collapsed).
  - `DraftTab.tsx` (driven by `GET .../drafts` from PR 1.3; edit‑in‑place via contenteditable; "Send to {connector}" calls `POST /drafts/{id}/send`).
  - `ApprovalsTab.tsx` (queue of pending approvals across this chat; clicking jumps to inline card in thread).
  - `SkillsTab.tsx` (consumes existing `useSkills()` from `features/skills/`; user can click a skill → composer's `/skill` insertion).
- Replace `DetailsPanelHost` for `/context` and `/usage` with workspace‑pane host or keep them as sibling overlays — pane tabs are the new default.

**PR 3.3 · Inline MCP discovery card variant + two‑stage approval UI** (M) — depends on PR 1.4

- Today `ConnectorAuthTool` renders MCP‑auth as an approval card. Add a second variant `McpDiscoveryCard` that fires when the model proactively suggests a not‑yet‑authorized server (per design Flow — Launch step 3): "Connect Linear to fetch ticket statuses?" with Connect / Skip. New event‑payload field `discovery_reason` distinguishes proactive discovery from blocking auth.
- `ApprovalTool.tsx`: add forward‑to UI when target uses two‑stage flow. Renders "Approve & forward to @marcus" with workspace‑user picker. After local approval, the card transforms into "Waiting on @marcus" pill; on leaf approval, transforms into "Approved by Marcus at 10:45 · Posted to #announcements" record.
- `chatModel/eventReducer.ts`: handle `APPROVAL_FORWARDED`.

**PR 3.4 · Per‑chat connector toggle UI + ConnectorPopover** (M) — depends on PR 1.2

- Extract `ConnectorPopover` to design‑system (or `apps/frontend/src/features/connectors/`) — used by topbar `ConnectorsPill` and composer `ConnectorsButton` (auto‑flip placement; mousedown‑outside dismissal).
- States vocabulary: Active(solid) / Paused(grey + dot) / Disconnected(dashed + Connect) / Workspace‑off(Enable, admin‑only). Toggle call → `PATCH .../connectors`.
- Composer `ConnectorsButton` shows count badge; clicking opens popover anchored above.

### Wave 4 — Surfaces beyond chat (5 PRs, parallelizable)

**PR 4.1 · Settings expansion · "You" group** (M)

- New sections in `features/settings/sections/`:
  - `Profile.tsx` — avatar upload, name, email (verified badge), title, timezone, locale, working hours. Reads/writes `/v1/me/profile` (add through facade if missing).
  - `Appearance.tsx` — theme (system/light/dark, persists in localStorage + Sessions if logged in), accent color (8 swatches from PR 0.1), density (comfortable/compact), reduce‑motion toggle.
  - `Shortcuts.tsx` — editable keymap (consumes `useKeymap()` registry); persist overrides per user.
  - `Notifications.tsx` — matrix Email/Slack/Desktop × event type. Backed by `/v1/me/notifications` (new minimal endpoint; events: mention, approval_needed, run_finished, weekly_digest).

**PR 4.2 · Settings expansion · "Workspace" group** (M)

- `WorkspaceSettings.tsx` — name, slug, logo, default model, default connectors, retention policy (uses PR 1.6 endpoint), danger zone.
- `Members.tsx` — role table (Admin/Member/Viewer), invite link generation, pending invites, audit‑log shortcut. Backed by existing SCIM + new invite endpoint (small: `invitations(invite_id, org_id, email, role_id, token_hash, expires_at, accepted_at, created_by_user_id)` migration on backend).
- `Billing.tsx` — plan card, usage chart (reads from existing `usage_daily_rollups` migration 0007), seats, payment method (placeholder), invoices (placeholder).

**PR 4.3 · Settings expansion · "AI & data" group + hash routing wired** (M)

- `ModelAndBehavior.tsx` — default model, default reasoning depth, system‑prompt override, temperature, citation density toggle, refusal behavior. Persists via `workspace_defaults` (PR 1.6) + per‑user override row.
- `Connectors.tsx` (extends existing) — workspace‑installed grid + "Add MCP server" CTA opening the MCP overlay (PR 4.5). Uses `ConnectorChip` from PR 0.1.
- `PrivacyAndData.tsx` — training opt‑out (workspace + per‑user), data residency (read‑only display from deploy config), retention summary, export, delete‑all‑data (audit‑logged, two‑step confirm).
- Hash routing: `useSettingsSection()` (from PR 0.1) drives the rail's active section; `Manage` links from popovers go to `/settings#connectors`.

**PR 4.4 · MCP overlay flow + test‑connection** (M)

- New `apps/frontend/src/features/connectors/mcp/McpOverlay.tsx` 5‑step wizard: Browse/search → Auth (OAuth/API key/no auth) → Scope review (per‑server scope toggles + Read‑only preset) → Confirm → Connected. Replaces today's inline catalog flow. Mounted from main app and from Settings → Connectors.
- Test‑connection step: facade `POST /v1/mcp/servers/{id}/test` calls backend's existing MCP probe; surface result in the wizard before "Add to workspace" enables.
- Schema: leave per‑tool scope toggles for a follow‑up; ship workspace‑level scopes from `mcp_servers.required_scopes` for v1.

**PR 4.5 · Usage overlay refit + Share popover** (M)

- `UsagePanel.tsx`: extend with two views — "This conversation" (existing token‑by‑message + context window + cost + model) and "Workspace" (30‑day stacked area chart by user, seats, plan‑limit overlay) consuming `usage_daily_rollups` (already exists). Trigger from new topbar usage meter (PR 2.1).
- New `ShareButton` popover: copy link, share to Slack, share to email, "view access" radio (anyone in workspace / specific people), "sources visible to viewer" toggle. Wires to **stub** sharing API for v1 — full sharing schema lands in Wave 6. v1 supports copy‑link only with workspace‑scope; deeper recipient flow in Wave 6.

### Wave 5 — Login (1 large PR)

**PR 5.1 · Email‑first login + IdP discovery + workspace picker** (L)

- Replace `apps/frontend/src/features/auth/LoginScreen.tsx`:
  - Hero email field, autofocus with `preventScroll: true`.
  - Debounced 450ms IdP discovery → calls new facade `GET /v1/auth/discover?email=` → backend reads `auth_providers` for the email's domain. Returns `{provider, display_name, member_count, sso_enforced}` or `{kind: "magic_link"}` for unknown.
  - Adaptive primary button label, three branches (SSO redirect / magic‑link / unknown‑domain hint).
  - Provider tiles collapsed; expand on "Use a different sign‑in method".
  - Right brand pane: eyebrow + headline + lede + compliance row (SOC2 / HIPAA / ISO 27001).
  - Body opt‑out CSS for the app‑level scroll lock (`html.login-html, body.login-body { overflow:auto; height:auto }`).
- Keep `MfaPrompt.tsx` but mount it as a step inside the new flow.
- New `WorkspacePicker.tsx`: list workspaces with member counts and last‑active timestamps. Single‑workspace users skip.
- Backend: `GET /internal/v1/auth/discover` endpoint; reuse `auth_providers` + `organizations`. Identity audit event `auth.discovery` on lookup (rate‑limited).

### Wave 6 — Sharing recipient + fork lineage (2 PRs)

**PR 6.1 · Sharing schema + create flow** (L)

- `services/backend/migrations/0018_conversation_sharing.sql`:
  - `conversation_shares(share_id pk, org_id, conversation_id, created_by_user_id, view_access enum(workspace|specific), sources_visible_to_viewer bool, share_token_hash, expires_at, revoked_at)`
  - `conversation_share_recipients(share_id, user_id pk, granted_at)` — for `specific` view_access
  - Append‑only audit on the existing chain.
- Facade `POST/GET/DELETE /v1/conversations/{id}/share`. `POST /v1/shares/{token}/accept` resolves to a read‑only conversation snapshot.
- Recipient view UI: read‑only thread renderer; citation chips show "Source restricted" tooltip (no snippet) when recipient lacks an authenticated connector for the source.

**PR 6.2 · Fork mechanic** (M)

- `agent_conversations.parent_conversation_id` already exists from PR 1.6; add a `POST /v1/conversations/{id}/fork` endpoint that snapshots message history and starts a new conversation owned by the recipient, using the recipient's connector set going forward (per Flow — Share step 3).
- Frontend "Fork to my chat" button on the recipient view.

### Wave 7 — Polish + audit log surface + per‑connector usage (2 small PRs)

**PR 7.1 · Audit log section** (S)

- Surface existing `identity_audit_events` + `runtime_audit_log` through a paginated facade endpoint `GET /v1/audit?since&limit&actor&action`. Render as a table in Settings → Members → Audit log. Already SIEM‑exportable; this is just the in‑product table.

**PR 7.2 · Per‑connector token attribution** (S)

- ai-backend: extend `runtime_model_call_usage` with `connector_id` (best‑effort attribution from active tool call when the model burns tokens). Aggregate into the Workspace usage view as a stacked layer.

## Out of scope (this plan)

These are explicitly deferred per the design's "later" pills or to keep the plan tractable:

- Per‑tool MCP scope toggles + read‑only preset (catalog needs a re‑model — own future PR).
- Tweaks panel (design notes "not shipped").
- Branching from a message (P1 in design).
- Inline edit of user message with re‑run (P1).
- Multi‑select on chat list (P1).
- Pin / drag‑reorder chats (P2).
- Passkey / WebAuthn (P3).
- API keys / webhooks Settings (P3).
- Light‑mode color tuning (P3).
- Voice mode.
- Multimodal source citation (Figma frames, Loom timestamps, Excel cells).

## Critical files index

Most of the work concentrates in these files; new files noted with `(new)`.

**Frontend**

- `apps/frontend/src/app/App.tsx` — router + hash sync (W0)
- `apps/frontend/src/app/keymap.ts` (new) — global keymap (W2)
- `apps/frontend/src/features/auth/LoginScreen.tsx` — full rebuild (W5)
- `apps/frontend/src/features/auth/WorkspacePicker.tsx` (new) (W5)
- `apps/frontend/src/features/chat/ChatScreen.tsx` — wire workspace pane host, topbar chrome (W2, W3)
- `apps/frontend/src/features/chat/components/shell/` (new) — Topbar parts (W2)
- `apps/frontend/src/features/chat/components/sidebar/UserCard.tsx` (new) (W2)
- `apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx` — greeting + cards (W2)
- `apps/frontend/src/features/chat/components/citations/` (new) (W3)
- `apps/frontend/src/features/chat/components/workspace/` (new) (W3)
- `apps/frontend/src/features/chat/components/tools/ConnectorAuthTool.tsx` — discovery variant (W3)
- `apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx` — forward UI (W3)
- `apps/frontend/src/features/chat/chatModel/eventReducer.ts` — new event variants (W3)
- `apps/frontend/src/features/chat/components/markdown/MarkdownText.tsx` — citation plugin (W3)
- `apps/frontend/src/features/connectors/mcp/McpOverlay.tsx` (new) (W4)
- `apps/frontend/src/features/connectors/ConnectorPopover.tsx` (new) (W3)
- `apps/frontend/src/features/settings/SettingsScreen.tsx` — section host + hash routing (W4)
- `apps/frontend/src/features/settings/sections/` (new — 9 sections) (W4)
- `apps/frontend/src/features/share/ShareButton.tsx` (new) (W4, W6)

**Design system**

- `packages/design-system/src/styles.css` — token alignment + 8 swatches (W0)
- `packages/design-system/src/primitives/` — `IconButton`, `StatusPill`, `ConnectorChip`, `AppIcon`, `Menu` (W0)

**API types**

- `packages/api-types/src/index.ts` — `RuntimeCitation`, `Draft`, `ConversationConnectorScope`, `SubagentSummary`, `ConversationShare`, approval forwarding fields (W1, W6)

**ai‑backend**

- `services/ai-backend/migrations/0014_runtime_citations.sql` (new) (W1)
- `services/ai-backend/migrations/0015_conversation_connector_scope.sql` (new) (W1)
- `services/ai-backend/migrations/0016_runtime_drafts.sql` (new) (W1)
- `services/ai-backend/migrations/0017_approval_forwarding.sql` (new) (W1)
- `services/ai-backend/migrations/0018_conversation_lifecycle.sql` (new) (W1)
- `services/ai-backend/src/agent_runtime/persistence/records/citations.py` (new), `drafts.py` (new) (W1)
- `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/draft.py` (new) (W1)
- `services/ai-backend/src/runtime_api/schemas/{events.py,common.py,conversations.py,approvals.py,drafts.py}` (W1)
- `services/ai-backend/src/runtime_api/http/routes.py` — new endpoints (W1)
- `services/ai-backend/src/runtime_worker/handlers/approval.py` — chain walk (W1)

**backend**

- `services/backend/migrations/0017_workspace_defaults.sql` (new) (W1)
- `services/backend/migrations/0018_conversation_sharing.sql` (new) (W6)
- `services/backend/migrations/0019_invitations.sql` (new) (W4)
- `services/backend/src/backend_app/routes/auth_discover.py` (new) (W5)
- `services/backend/src/backend_app/routes/audit_export.py` — read endpoint for in‑product table (W7)

**backend‑facade**

- New proxy routes for every new ai‑backend / backend endpoint above. Identity headers preserved, never expose `/internal/v1/*`.

## Verification

For each wave we verify in this order:

1. **Unit + service tests** in the changed component:
   - ai‑backend: `cd services/ai-backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest`
   - backend: same pattern
   - frontend: `npm run typecheck --workspace @enterprise-search/frontend && npm run build --workspace @enterprise-search/frontend`
2. **Cross‑service smoke**: `make test`.
3. **Live stack walk‑through**: `make dev`, then for each PR walk the relevant flow end‑to‑end:
   - **W1.1 citations**: trigger search&summarize prompt; observe Sources tab populates as docs ingest, chips render inline as text streams, click chip scrolls to source row.
   - **W1.2 connector scope**: pause Slack in chat A's `ConnectorPopover`; start a run; verify the model can't call `slack_*` tools (audit event present); switch to chat B; Slack still active there.
   - **W1.3 drafts**: launch‑announcement scenario; observe Draft tab populates with editable content; "Send to Slack" routes through approval; on approve, draft status flips to `sent`.
   - **W1.4 two‑stage approval**: trigger Slack post requiring approval + forward to a second user; verify chain rows in `runtime_approval_requests`; resolve leaf; observe inline card transform.
   - **W2 shell**: ⌘N opens fresh chat; ⌘K focuses sidebar search; ⌘\ toggles sidebar; ⌘↩ approves the focused approval card.
   - **W3 workspace pane**: open chat with sources → pane auto‑opens; chat without tools → pane closed.
   - **W4 settings**: navigate `/settings#connectors` directly → lands on Connectors section; change accent → updates live; MCP overlay completes a fake server install through all 5 steps incl. test‑connection.
   - **W4 usage**: usage meter in topbar opens overlay; both views render; numbers reconcile against `usage_daily_rollups`.
   - **W5 login**: type `you@gmail.com` → magic‑link branch; type `you@acme.com` (seeded SSO) → "Continue with Okta"; type unknown domain → magic‑link with hint; MFA + workspace picker complete.
   - **W6 share**: create a workspace‑scope share; open as a different workspace user → read‑only thread; cite a source the recipient lacks → "Source restricted"; click Fork → new chat with recipient's connector set.
4. **Compliance gate before merging W1, W4, W6**: `make prod` build (validates required secrets) + a manual pass through the CLAUDE.md compliance checklist for any new sensitive workflow (who can do it, who approved it, what changed, where it is logged, retention, deletion).
5. **Telemetry gate**: confirm `pg_stat_statements` (already wired by commit `94e230e`) shows the new endpoints and that none of them issue cross‑org reads.

## Sequencing summary

```
W0  →  W1 (parallel internally)  →  W2 (parallel)  →  W3 (depends on W1)  →  W4 (parallel; depends on W0,W1)  →  W5  →  W6 (depends on W1,W2,W3)  →  W7
```

Approximate PR count: **22 PRs** over the seven waves. Most PRs are S/M and shippable in 2–4 days each; the four marked L (W3.2 workspace pane, W4.1+W4.2+W4.3 settings group, W5.1 login, W6.1 sharing schema) are the long poles.
