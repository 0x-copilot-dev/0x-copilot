# Connectors destination — sub-PRD (Phase 11)

**Status:** binding (drafted 2026-05-18, orchestrator)
**Master PRD:** [destinations-master-prd.md §5.8](../destinations-master-prd.md)
**Cross-audit:** [cross-audit.md](../cross-audit.md) (binding decisions §1–§5)
**Impl-plan slot:** [implementation-plan.md §2 Phase 11](../implementation-plan.md)
**Owner:** parth · **Phase:** 11

**Companion contracts:**

- `packages/api-types/src/connectors.ts` (NEW — this PRD; complements existing `packages/api-types/src/projects.ts` `ConnectorSlug`)
- `services/backend/src/backend_app/connectors/` (NEW — wraps existing `mcp_catalog.py` + `mcp_oauth.py` + `token_vault.py` with the destination-level API)
- `packages/chat-surface/src/destinations/connectors/` (EXISTS as stub — replaced)
- `apps/frontend/src/features/connectors/` (NEW)

**Binding cross-PRD inputs (recap):**

- `ItemRef` kind `connector` ([cross-audit.md §1.1](../cross-audit.md))
- `ConnectorId` brand in `packages/api-types/src/brands.ts`
- `ConnectorSlug` lives in `packages/api-types/src/projects.ts` (canonical site; do NOT duplicate)
- Project-scoped ACL: `is_project_member` ([cross-audit.md §1.3](../cross-audit.md))
- Filter axis OR ([cross-audit.md §1.5](../cross-audit.md))
- SP-1 primitives + SSE convention ([cross-audit.md §1.6 + §5.2](../cross-audit.md))
- TU-1 single-tracker invariant ([cross-audit.md §5.5](../cross-audit.md)) — Connectors do not call LLMs; not applicable.
- Routines §9.7 Q6: **HMAC-of-payload signature lands in Phase 11** as the consolidated webhook UX (deferred from Phase 5)

---

## §1 Premise

### 1.1 What a Connector is

A **Connector** is an authenticated bridge to an external SaaS source: Gmail, Outlook, Google Calendar, Slack, Salesforce, GitHub, Google Drive, Notion, etc. It is the _identity + token + scope_ pairing that lets Atlas (via Tools or built-in capabilities) read from or write to a third-party system on the user's behalf.

A Connector has:

- A **kind** (`gmail`, `gcal`, `slack`, `salesforce`, `github`, `gdrive`, `notion`, `outlook`, `ocal`, `custom_mcp`, …) — string `ConnectorSlug` (canonical site already lives in `packages/api-types/src/projects.ts`).
- A **transport** — OAuth 2.x flow (the existing MCP OAuth path; see `services/backend/src/backend_app/mcp_oauth.py`).
- A **token bundle** — access/refresh tokens stored in the existing `TokenVault` (encrypted at rest).
- A **scope** — the set of OAuth scopes the user granted (Atlas requests minimum; admin can add).
- A **status** — `connected` / `disconnected` / `error` / `expired`.
- An **owner** (the user who initiated the connection; not the tenant).
- A **per-chat / per-project allowlist** integration (existing — Phase 1 chat scope; Phase 6 project default_connector_allowlist).
- A **read audit log** — every connector-read by a tool gets one row (already exists via the ai-backend tool-invocation pipeline; Phase 11 just renders the lens).

### 1.2 Why a separate destination instead of "just OAuth in Settings"

1. **Scope review is a recurring action**, not one-time. When a SaaS app adds scopes Atlas can request, users want to grant them; when they decide to revoke Calendar but keep Mail, they want a focused flow.
2. **Per-chat audit lens.** "Which connectors did this conversation touch?" is the natural compliance question; the connector page is where it answers.
3. **Webhook security UX.** Routines §9.7 Q6 deferred HMAC-of-payload signature to Wave 5+. Phase 11 ships the consolidated webhook UX (HMAC config, rotating secret, IP allowlist) here — Routines provided the wire shape; Connectors provides the management UI.
4. **Connector marketplace.** Atlas-vetted + tenant-approved + community-published. Same approval gate as Tools (§10.2) but rendered here.

### 1.3 What Connectors is NOT

- **A tool catalog.** Tools (Phase 10) consume connectors; the tool catalog is one level up. A connector exposes _data + auth_; a tool exposes _callable methods_.
- **An OAuth server.** Atlas is always the OAuth _client_; users authenticate with the upstream provider. We never accept third-party OAuth tokens for inbound auth.
- **A directory sync.** SCIM-based user provisioning is a separate axis (Phase 12 Team destination / Wave 12 admin).
- **A connector-as-tool dispatcher.** A connector backs ZERO or MORE tools; the binding is `Tool.transport.connector_ref?` (Phase 10 §3.1).

### 1.4 User success states

- _"Connect Gmail."_ → `/connectors` → "Gmail" card → "Connect" → OAuth pop-out → consent → back to Atlas → card shows status `connected` + last-sync.
- _"Which agents use my Salesforce connector?"_ → `/connectors/salesforce_<id>` → "Used by" tab → list of tools + agents.
- _"Reduce Slack to read-only."_ → `/connectors/slack_<id>` → "Scope" tab → uncheck `chat:write` → re-OAuth to confirm scope downgrade → save.
- _"Disconnect, but keep audit history."_ → `/connectors/<id>` → "Disconnect" → confirmation → status flips to `disconnected`; token wiped; audit row preserved; consumers' grants show "needs reconnect" badge.
- _"Register an HMAC-signed webhook for my Routine."_ → `/connectors/webhooks` (Phase 11 sub-route) → "Add webhook" → enter URL + select rotating-secret strategy + (optional) IP allowlist + HMAC algo → save.

### 1.5 Relationship to other destinations

| Surface  | Connectors relationship                                                                                                                                  |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Chats    | Per-chat connector allowlist (existing; cross-chat PATCH already lands via facade). Connector detail "Used by" includes per-chat counts (admin-only).    |
| Agents   | An agent's `connector_refs[]` — Phase 8 already wires this in `Agent`. Phase 11 just renders the reverse: connector → agents.                            |
| Tools    | `Tool.transport.connector_ref?: ConnectorId` — Phase 10. Connector detail "Used by tools" tab lists them.                                                |
| Projects | `Project.default_connector_allowlist?: ConnectorSlug[]` (Phase 6 / §9.8 Q3 inheritance rule). Connector detail "Used by projects" tab lists them.        |
| Library  | Datasets `kind=dataset` may be sourced from a connector (CSV pull from Salesforce). Library page tag `source: connector_<id>`. Phase 7 already supports. |
| Inbox    | Tool calls that hit `auth_required` errors deliver an Inbox item kinded `connector_needs_reconnect` with `ItemRef.kind = "connector"`.                   |
| Home     | Triage strip "expired connectors" count.                                                                                                                 |
| Routines | Routines §9.7 Q6 HMAC-of-payload UX ships here. Routine `triggers[].webhook` → `/connectors/webhooks/<webhook_id>` for inspection / rotation.            |
| Memory   | Memory items are not sourced from connectors directly (Memory writes are agent-driven). No direct relationship.                                          |
| Team     | Person detail page: "connectors this user owns" + "shared connectors" they have access to.                                                               |

### 1.6 Status semantics

- `connected` — token valid; last read within window.
- `disconnected` — user revoked; token wiped; row preserved.
- `error` — provider returned 401/403 on last refresh; needs reconnect.
- `expired` — refresh token expired; needs full re-auth (provider-dependent).

State transitions are server-driven from the token-refresh worker; UI displays.

---

## §2 User journeys (the 6 concrete flows)

### U1. "Connect Gmail."

User opens `/connectors`. Sees a 3-tab `<FilterTabs>`: **Connected** / **Available** / **Custom**. On Available: Gmail card. Click. Popup tab to provider consent screen. Atlas's redirect URI handles the callback, exchanges code for tokens (existing `mcp_oauth.py` path), and writes a `connectors` row + `token_vault` entry. Tab closes; Atlas refreshes the destination; Gmail card moves to "Connected" with status pill `connected`.

### U2. "Review and shrink scope."

User opens `/connectors/<id>`. "Scope" tab lists OAuth scopes — granted ones checked, ungranted available to add. Removing a checked scope triggers a re-OAuth flow with the reduced scope set; provider asks user to confirm; Atlas writes the new token bundle.

### U3. "Disconnect."

User opens `/connectors/<id>` → "Disconnect" button. Confirmation dialog: "N agents, M chats, K tools will lose access. Reconnect later to restore." On confirm: server revokes the upstream token if the provider supports revocation; wipes `token_vault` entry; sets `status = "disconnected"`; writes audit row. Existing grants on agents/tools preserved (rendered as needs-reconnect).

### U4. "Per-chat override."

(Existing path; documented for completeness.) From the chat composer's ConnectorPopover, the user toggles connectors on/off for the current chat. Backend PATCH `/v1/conversations/<id>` writes `chat_connector_overrides`. Connector destination doesn't directly host this UI; it links back to the chat from "Used by chats" with admin perms.

### U5. "Register an HMAC-signed webhook for a Routine."

User opens `/connectors/webhooks`. "Add webhook" wizard:

1. URL — must be `https://`.
2. Secret strategy — `rotating` (Atlas generates + rotates every 90 days) vs `static` (user-provided).
3. HMAC algorithm — `hmac-sha256` (default; only option in v1).
4. IP allowlist — comma-separated CIDR; empty = any.
5. Test fire — Atlas sends a sample signed payload; user pastes back the received `X-Atlas-Routine-Signature` header for verification.

On save: webhook row inserted; secret saved in token_vault; the wizard returns the URL + initial secret (copy-once reveal — Phase 5 routines pattern). Routine wire `triggers[].webhook` references this row.

### U6. "Inspect read audit for compliance."

Tenant admin opens `/connectors/<id>` → "Audit" tab. Renders `<ActivityList>` of read events, paginated; each row: timestamp + caller (run_id ItemLinked) + endpoint + bytes-read summary. Filter by date, caller-kind, status. Export to CSV (background job; emails URL when ready).

---

## §3 Data shape

### 3.1 Canonical wire types (`packages/api-types/src/connectors.ts`)

```typescript
export type ConnectorStatus =
  | "connected"
  | "disconnected"
  | "error"
  | "expired";

export interface ConnectorScopeEntry {
  readonly scope: string; // provider-specific (e.g. "gmail.readonly")
  readonly granted: boolean;
  readonly description: string; // human-readable from provider catalog
}

export interface Connector {
  readonly id: ConnectorId;
  readonly tenant_id: TenantId;
  readonly slug: ConnectorSlug; // from projects.ts canonical site
  readonly display_name: string;
  readonly description: string;
  readonly status: ConnectorStatus;
  readonly status_reason?: string;
  readonly owner_user_id: UserId;
  /** Scopes are provider-dependent; the catalog reads from a config-file
   *  per slug at backend bootstrap. */
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
  readonly last_sync_at: string | null;
  readonly last_error_at?: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ConnectorListResponse {
  readonly connectors: ReadonlyArray<Connector>;
  /** Available-to-install catalog (no tenant row yet). Slug + display only. */
  readonly available: ReadonlyArray<{
    readonly slug: ConnectorSlug;
    readonly display_name: string;
    readonly description: string;
    readonly icon_hint?: string;
  }>;
  readonly next_cursor: string | null;
}

export interface ConnectorDetailResponse {
  readonly connector: Connector;
  readonly consumers: {
    readonly agents: ReadonlyArray<ItemRef>; // narrowed "agent"
    readonly tools: ReadonlyArray<ItemRef>; // narrowed "tool"
    readonly projects: ReadonlyArray<ItemRef>; // narrowed "project"
    readonly chats_with_grant: number; // count only
  };
}

export interface Webhook {
  readonly id: TriggerId; // re-uses brand from brands.ts
  readonly tenant_id: TenantId;
  readonly url: string;
  readonly secret_strategy: "rotating" | "static";
  readonly hmac_algo: "hmac-sha256";
  readonly ip_allowlist: ReadonlyArray<string>; // CIDR
  readonly status: "active" | "paused";
  readonly last_fire_at: string | null;
  readonly last_status_code?: number;
  /** Routine + tenant linkback. */
  readonly routine_ref?: ItemRef; // narrowed "routine"
  readonly created_at: string;
  readonly rotates_at: string | null;
}
```

### 3.2 Reuse — no parallel registry

The existing `mcp_catalog` + `mcp_oauth` + `token_vault` tables back the Connector destination. The new `connectors` table is a **view + denormalized read model** built from MCP registration rows + token vault metadata — NOT a parallel write model. Inserts/updates flow through the existing services; the Connector destination's writes call into them.

(Schema reuse is the DRY win: every existing OAuth flow keeps working; Connectors adds zero new auth code.)

---

## §4 Endpoints (`/v1/connectors/*` via facade)

### 4.1 `GET /v1/connectors` — list

Returns `ConnectorListResponse`. Query: `?status`, `?kind`, `?installed=true|false`, `?q`, `?cursor`, `?limit`.

### 4.2 `GET /v1/connectors/{id}` — detail

Returns `ConnectorDetailResponse`. 404 on out-of-tenant.

### 4.3 `POST /v1/connectors/{slug}/start-oauth` — begin connect

Returns `{ authorization_url, state }`. Owner = caller (rejects if a connected one of the same slug for this user already exists; admin can multi-install via separate slug suffix in Wave 12).

### 4.4 `POST /v1/connectors/oauth-callback` — complete

Existing path; renamed/aliased under `/v1/connectors/` for symmetry. Body: `{ code, state }`. Returns the newly-created `Connector`.

### 4.5 `POST /v1/connectors/{id}/refresh` — manual refresh

Forces a token-refresh now (otherwise the worker handles it on expiry). Owner OR tenant admin.

### 4.6 `POST /v1/connectors/{id}/disconnect`

Owner OR tenant admin. Revokes upstream token (best-effort), wipes vault, flips status. Audit row.

### 4.7 `PATCH /v1/connectors/{id}/scopes`

Owner OR tenant admin. Body: `{ scopes: ConnectorScopeEntry[] }` — the desired set. If scope shrinks, server triggers a re-OAuth flow (the response is `202 { reauth_url }`); if expands, server triggers an additive OAuth flow (same response shape).

### 4.8 `GET /v1/connectors/{id}/audit` — read audit log

Paginated. Query: `?after_id`, `?since_iso`, `?caller_kind`, `?limit`. Returns the read-event projection over `runtime_tool_invocations` (joined to the tools that use this connector) + per-connector direct-read rows from `connector_read_events` (already written by the existing connector dispatcher).

### 4.9 `GET /v1/connectors/stream` — SSE

Envelopes: `connector.created`, `connector.status_changed`, `connector.scope_changed`, `connector.error_threshold`, `connector.heartbeat`. `Last-Event-ID` resume.

### 4.10 Webhook endpoints

- `GET /v1/connectors/webhooks` — list per-tenant webhooks (owner = caller OR caller is admin).
- `POST /v1/connectors/webhooks` — create. Body matches §3.1 `Webhook` minus server-set fields. Returns the row + the initial secret (copy-once reveal — re-fetching returns a redacted view).
- `PATCH /v1/connectors/webhooks/{id}` — edit (url / ip_allowlist / status).
- `POST /v1/connectors/webhooks/{id}/rotate` — generate a new secret. Returns the new secret (copy-once).
- `DELETE /v1/connectors/webhooks/{id}` — delete (cascades to routines that referenced it — they go `errored`).
- `POST /v1/connectors/webhooks/{id}/test-fire` — admin / owner test path; sends a sample signed payload to the URL; returns response status.

### 4.11 Internal endpoints

- `GET /internal/v1/connectors/by_user/{user_id}/by_slug/{slug}` — used by ai-backend tool dispatcher to fetch the token bundle for a transport.
- `POST /internal/v1/connectors/{id}/touch` — increments `last_used_at` on tool invocation.

### 4.12 Filter / sort allowlist

- **Filter:** status, slug (one or more for OR), q, installed (boolean).
- **Sort:** display_name, last_sync_desc, created_at_desc.

---

## §5 Storage

### 5.1 `connectors` table

Mostly a denormalized read model. Server inserts/updates flow through the existing MCP/OAuth path; this table is updated by trigger / service-layer write-through.

| Column                                                      | Type                                        |
| ----------------------------------------------------------- | ------------------------------------------- |
| `id` (PK), `tenant_id`, `slug`, `display_name`              | text NN                                     |
| `description`                                               | text                                        |
| `status`, `status_reason`                                   | text                                        |
| `owner_user_id`                                             | text NN                                     |
| `scopes`                                                    | jsonb NN (array of `ConnectorScopeEntry`)   |
| `last_sync_at`, `last_error_at`, `created_at`, `updated_at` | timestamptz                                 |
| `vault_ref`                                                 | text NN — opaque pointer into `token_vault` |

Indexes: `(tenant_id, status, slug)`, `(tenant_id, owner_user_id)`, `(slug)` for the available-catalog lookup.

### 5.2 `webhooks` table

| Column                                             | Type                                            |
| -------------------------------------------------- | ----------------------------------------------- |
| `id` (PK, `trig_<ulid>` — re-uses TriggerId brand) | text                                            |
| `tenant_id`, `url`, `hmac_algo`                    | text NN                                         |
| `secret_strategy`                                  | text NN — `rotating` or `static`                |
| `ip_allowlist`                                     | inet[] NN default `{}`                          |
| `status`                                           | text NN — `active` or `paused`                  |
| `last_fire_at`, `last_status_code`                 | timestamptz, int                                |
| `routine_id` (FK)                                  | text                                            |
| `vault_ref`                                        | text NN — points at the secret in `token_vault` |
| `rotates_at`                                       | timestamptz                                     |
| `created_at`, `updated_at`                         | timestamptz                                     |

Indexes: `(tenant_id, status, rotates_at)` (rotation worker), `(tenant_id, routine_id)`.

### 5.3 `connector_read_events`

Already exists per Phase 5 routines + Phase 7 library + Phase 10 tools. Schema unchanged.

### 5.4 Retention

- `connectors.status = disconnected` rows: retained 30 days (master §5.8), then hard-deleted with cascade to audit-redaction.
- `webhooks`: retained until deleted by owner; status = `paused` preserved indefinitely (admin choice).
- `token_vault`: existing 30-day post-disconnect retention.
- `connector_read_events`: 365 days, per master §3.3.

---

## §6 ACL + audit

### 6.1 ACL

- Read (`GET /v1/connectors`, `GET /v1/connectors/{id}`): tenant member. 404 on out-of-tenant.
- Detail with consumer counts: tenant member (owner sees their own; admin sees all).
- Write (start-oauth, disconnect, refresh, scope patch): owner OR tenant admin.
- Webhooks: tenant admin OR routine-owner.

### 6.2 Audit

Every state-changing action writes through the canonical audit helper:

- `connector.connected` / `connector.disconnected` / `connector.expired`
- `connector.scope_added` / `connector.scope_removed`
- `connector.error` (auto)
- `connector.token_refreshed` (recurring; sampled at 1/N or admin-toggleable)
- `webhook.created` / `webhook.rotated` / `webhook.deleted` / `webhook.test_fired`

Read events flow through the existing `connector_read_events` write path — no parallel audit table.

### 6.3 Inbox routing

- `connector.expired` → Inbox item kinded `connector_needs_reconnect` to the connector owner.
- `connector.error` (auto) at threshold → Inbox item; admins also notified if tenant policy `connector_error_admin_notify=true` (Wave 12 toggle).
- `webhook.delivery_failed` past 5 consecutive failures → Inbox item to the routine owner.

---

## §7 Frontend surface

### 7.1 Route map

- `/connectors` — catalog (default tab: Connected)
- `/connectors/<id>` — detail
- `/connectors/<id>/scope` — scope review (also accessible as a tab)
- `/connectors/<id>/audit` — read audit log (admin)
- `/connectors/<id>/consumers` — used-by lens
- `/connectors/webhooks` — webhook manager
- `/connectors/webhooks/<id>` — webhook detail (rotate, test-fire)

### 7.2 Destination components (`packages/chat-surface/src/destinations/connectors/`)

- `ConnectorsDestination.tsx` — shell with `<PageHeader>` + `<FilterTabs>` (Connected / Available / Custom) + `<CardGrid>`.
- `ConnectorsPanel.tsx` — left rail filters.
- `ConnectorCard.tsx` — icon + name + status pill + last-sync.
- `ConnectorDetailView.tsx` — tabs (Overview / Scope / Consumers / Audit / Settings).
- `ScopeReviewTab.tsx` — checkbox list of `ConnectorScopeEntry`; submit triggers re-OAuth.
- `ConsumersTab.tsx` — three `ActivityList`s (Agents / Tools / Projects) with `ItemLink` rows.
- `ReadAuditTab.tsx` — paginated activity list; admin-only.
- `WebhooksDestination.tsx` (sub-route shell) — list + create wizard.
- `WebhookCard.tsx` / `WebhookDetailView.tsx`.
- `WebhookCreateWizard.tsx` — step machine; copy-once-reveal of the secret on save.

### 7.3 SP-1 primitives

All views use `<PageHeader>` / `<FilterTabs>` / `<EmptyState>` / `<CardGrid>` / `<DocList>` / `<ActivityList>` / `<StatusPill>` / `<ItemLink>` / `formatRelativeTime`. Copy-once-reveal pattern is lifted from Routines (P5-B3) — same `RevealOnce` component.

### 7.4 Empty / error states

- Catalog empty (no installed): "Connect your first SaaS source" + four featured cards (Gmail / GCal / Slack / Salesforce).
- Disconnected card: "Reconnect" CTA.
- Error card: tone red, "Resolve" CTA linking to scope review or full re-OAuth (whichever applies).

---

## §8 Cross-destination linking

- Composer ConnectorPopover already exists. No new UI; the popover reads from `GET /v1/connectors?installed=true` and writes to `chat_connector_overrides`.
- Tool detail "Transport" tab shows `transport.connector_ref` as an `<ItemLink ref={{ kind: "connector", id }}>`.
- Agent detail "Connectors" tab shows agent's `connector_refs[]` as `<ItemLink>`s.
- Project detail "Connectors" tab — Phase 6 `default_connector_allowlist` lists slugs; Phase 11 enriches each chip with a deep-link to `/connectors?slug=<x>`.
- Inbox `connector_needs_reconnect` row → connector detail.
- Home triage strip "expired connectors" tile → `/connectors?status=expired`.

---

## §9 Webhook security (Routines §9.7 Q6 — UX lands here)

### 9.1 HMAC signature

Every outbound webhook fire (routine-triggered) carries:

- Header `X-Atlas-Routine-Signature: hmac-sha256=<hex>` where the body is HMAC'd with the webhook's secret.
- Header `X-Atlas-Signature-Timestamp: <unix-seconds>` — receivers MUST reject if `|now - ts| > 300`.

The pattern matches Stripe / GitHub webhook signing; well-documented; no novelty.

### 9.2 Secret rotation

- `secret_strategy: "rotating"` — Atlas generates a new secret every 90 days; previous secret remains valid for 14 days after rotation to ease receiver upgrade.
- `secret_strategy: "static"` — user-provided; never rotated by Atlas. User accepts the responsibility.

### 9.3 IP allowlist

If `ip_allowlist` is non-empty, Atlas refuses to send to URLs whose DNS resolution falls outside the listed CIDRs. (Defense-in-depth — for receivers that pin their origin IP.)

### 9.4 Verification snippet (rendered on the wizard's "Verify" step)

```python
# Receiver-side verification (Python)
import hmac, hashlib, time
def verify(body: bytes, sig_header: str, ts_header: str, secret: bytes) -> bool:
    if abs(time.time() - int(ts_header)) > 300:
        return False
    algo, signature = sig_header.split("=", 1)
    if algo != "hmac-sha256":
        return False
    expected = hmac.new(secret, body + ts_header.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

(Wizard renders this verbatim with a "copy" button.)

---

## §10 Open questions

1. **Per-connector rate limits.** Same axis as Tools §10.8. **Recommend:** push rate-limit responsibility to the upstream provider (most have their own); Atlas exposes the 429 errors in the read audit.
2. **Shared connectors across users.** Tenant admin connects "the Salesforce workspace" and team members consume? **Recommend:** Phase 11 ships USER-owned connectors only; tenant-owned ("workspace connectors") deferred to Wave 12 admin destination — design notes in §1.1.
3. **Webhook test-fire body.** Free-form vs strict JSON template? **Recommend:** strict template (matches what routines actually send). Avoids template drift.
4. **Provider catalog data.** Hard-code in a backend YAML, or fetch from a remote registry on bootstrap? **Recommend:** YAML in Phase 11; remote registry in Wave 13 connector marketplace.
5. **Connect-from-onboarding.** Wave 1 onboarding wizard already touches Gmail / GCal. **Recommend:** the onboarding wizard calls the same `/v1/connectors/{slug}/start-oauth` — single OAuth code path. No duplication.
6. **Token vault adapter for production.** Local-only adapter unsuitable for prod. **Recommend:** ships unchanged — Atlas docs already gate this behind a managed-adapter contract. Phase 11 doesn't block on it.
7. **Disconnect → cascade delete of tools.** When a connector is disconnected, dependent tools become `error`. Should we also auto-disable them? **Recommend:** yes — flip dependent tools to `disabled` (NOT delete); preserves the agent grants but rejects new calls until reconnect.

---

## §11 Phasing within Phase 11 (P11-A/B/C sub-phases)

| Sub-phase | Scope                                                                                                      | Estimated LOC | Worktree-able in parallel? |
| --------- | ---------------------------------------------------------------------------------------------------------- | ------------- | -------------------------- |
| P11-A1    | api-types/connectors.ts canonical wire                                                                     | ~300          | Yes (independent)          |
| P11-A2    | services/backend/src/backend_app/connectors/ — schema + service + store + routes wrapping existing OAuth   | ~700          | Yes (after A1)             |
| P11-A3    | services/backend/src/backend_app/webhooks/ — webhook manager + secret rotation worker                      | ~500          | Yes (independent)          |
| P11-A4    | facade connector*routes.py — proxy /v1/connectors/* + /v1/connectors/webhooks/\_ + stream                  | ~250          | Yes (after A2 + A3)        |
| P11-B1    | chat-surface destinations/connectors/ — ConnectorsDestination + ConnectorCard + ConnectorsPanel + filter   | ~600          | Yes (after A1)             |
| P11-B2    | chat-surface ConnectorDetailView + ScopeReviewTab + ConsumersTab + ReadAuditTab                            | ~700          | Yes (after B1)             |
| P11-B3    | chat-surface WebhooksDestination + WebhookCreateWizard + WebhookCard + RevealOnce reuse                    | ~600          | Yes (after B1)             |
| P11-C     | apps/frontend/src/features/connectors/ — route + adapters + tests; replace existing DestinationPlaceholder | ~500          | Yes (after A2 + B1)        |

Total: ~4150 LOC. Same dispatch pattern as Phase 10.

---

## §12 Done definition

- Every endpoint in §4 implemented + tested.
- Every component in §7 rendered + tested.
- A user can OAuth-connect Gmail end-to-end via the wizard.
- HMAC signature verification snippet works against a real local receiver (manually verified).
- Webhook secret rotation worker rotates a test webhook on schedule.
- All audit rows write through the canonical helper.
- `is_project_member` used (when project_id is present in the surface).
- SP-1 primitives used; no inline color / spacing.
- TU-1 invariant preserved (Connectors don't call LLMs; no parallel tracker introduced).

---

## §13 References

- [destinations-master-prd.md §5.8](../destinations-master-prd.md)
- [cross-audit.md](../cross-audit.md) §1.1 / §1.3 / §1.5 / §1.6 / §5.2 / §9.7 Q6 (HMAC routines)
- [implementation-plan.md §2 Phase 11](../implementation-plan.md)
- [Routines PRD §3.7 webhook trigger](routines-prd.md)
- [Tools PRD §3.1 ToolTransport.connector_ref](tools-prd.md)
- [Projects PRD §5.1 default_connector_allowlist](projects-prd.md)
