# Team + Memory + ⌘K palette + polish — sub-PRD (Phase 12)

**Status:** binding (drafted 2026-05-18, orchestrator)
**Master PRD:** [destinations-master-prd.md §5.9 Team / §5.10 Memory](../destinations-master-prd.md)
**Cross-audit:** [cross-audit.md](../cross-audit.md) (binding decisions §1–§5)
**Impl-plan slot:** [implementation-plan.md §2 Phase 12](../implementation-plan.md)
**Owner:** parth · **Phase:** 12 (ship-everything-else)

**Companion contracts:**

- `packages/api-types/src/team.ts` (NEW)
- `packages/api-types/src/memory.ts` (NEW)
- `packages/api-types/src/palette.ts` (NEW)
- `services/backend/src/backend_app/team/` (NEW — wraps existing `users` + `tenant_memberships`)
- `services/backend/src/backend_app/memory/` (NEW — embeddings reuse Library's pgvector path)
- `services/backend/src/backend_app/palette/` (NEW — read-only suggestion projection)
- `packages/chat-surface/src/destinations/team/` (EXISTS as stub — replaced)
- `packages/chat-surface/src/destinations/memory/` (EXISTS as stub — replaced)
- `packages/chat-surface/src/shell/CommandPalette.tsx` (NEW — substrate-shared ⌘K)
- `apps/frontend/src/features/{team,memory,palette}/` (NEW)

**Binding cross-PRD inputs (recap):**

- `ItemRef` kinds `person` / `memory` already in the canonical union ([cross-audit.md §1.1](../cross-audit.md))
- `UserId` / `MemoryItemId` brands in `packages/api-types/src/brands.ts`
- Project-scoped ACL: `is_project_member` ([cross-audit.md §1.3](../cross-audit.md))
- Filter axis OR + SP-1 primitives + SSE convention ([cross-audit.md §1.5/§1.6/§5.2](../cross-audit.md))
- TU-1 single-tracker invariant ([cross-audit.md §5.5](../cross-audit.md)) — Memory retrieval / palette ranking go through `build_chat_model` with new `Purpose.MEMORY_RETRIEVAL` / `Purpose.PALETTE_RANKING` enum values
- Routines §9.7 Q9/Q10/Q12/Q14 — Atlas-proposed cron suggestions + "Make this a routine?" CTA + admin force-reassign re-evaluation + Settings UI for notification defaults — ALL land here

---

## §1 Premise

Phase 12 is the ship-everything-else phase. Three destinations + a palette + a Settings polish all roll together because:

1. They are smaller individually than Library / Agents / Tools / Connectors.
2. They share the same primitives (people picker + memory items both index into the palette).
3. The palette unifies them — once Team and Memory exist, ⌘K can route everywhere from one keystroke.

### 1.1 Team — what it is

The **Team** destination is the catalog of people in the workspace + their agents + their recent activity. It's where you find _who owns what_, _who's online_, _who to ask_. It builds on the existing `users` + `tenant_memberships` tables — no new identity. The destination adds: presence, agent ownership lens, invite workflow, role management, person-detail audit (admin).

### 1.2 Memory — what it is

The **Memory** destination is what Atlas knows about you and your team — skills, facts, preferences. Long-term context that persists across chats. Each item is `{ scope, kind, title, body }` with provenance (who created it, when last used). Memories are read by the runtime at chat / run start (via `Purpose.MEMORY_RETRIEVAL` over an embedding index that reuses Library's pgvector infra). Editable; auditable; scoped (user vs workspace).

### 1.3 ⌘K palette — what it is

A global command palette (keyboard ⌘K / Ctrl+K) that searches every destination + offers context-aware quick actions. Type a few characters → fuzzy match across:

- Conversations + projects (by name)
- Library files / pages / datasets (by title; full-text fallback)
- Agents / tools / connectors (by name)
- People (by name / email)
- Memories (by title)
- Quick-action suggestions ("Make this a routine?", "Onboard a calendar", "Create a project from this chat")

The palette is **substrate-shared** — same component renders in web, Mac, Windows; the host wires a `PaletteSearchPort` so each substrate can decide where to send the query (web → facade; desktop → IPC to a local index for offline-first results).

### 1.4 Polish — what it is

The "everything else" bucket — items deferred across earlier phases:

- Routines §9.7 Q9: Atlas-proposed cron suggestions (palette + Routines editor)
- Routines §9.7 Q10: "Make this a routine?" CTA on the chat canvas
- Routines §9.7 Q14: Settings UI for tenant/user notification defaults
- Routines §9.7 Q12: admin force-reassign — re-evaluated; **stays deferred** per [cross-audit §9.8 Q1](../cross-audit.md)
- A `/settings` route (Settings is NOT a destination per master PRD §3.5; it lives off the profile menu)
- README pass + final cross-audit reconciliation

### 1.5 What this phase is NOT

- A new identity provider — `services/backend/src/backend_app/identity/` already owns IdP integration.
- A new audit pipeline — every state-change goes through the existing canonical helper.
- A new vector store — memory embeddings live in `library_embeddings` with `target_kind = "memory"` (reuse Phase 7 / 7.5 infra).
- A new LLM call path — palette ranking + memory retrieval extend the existing `Purpose` enum.

---

## §2 User journeys (across the three sub-destinations)

### Team journeys

- **U-T1.** "Who owns the Salesforce agent?" → `/team` → search "Salesforce" → person card → click → person detail with their agents listed.
- **U-T2.** "Invite Sarah." → `/team` → "Invite" → email → role pick → send. Sarah receives a magic-link IdP invite (re-uses existing identity invite path).
- **U-T3.** "Demote Marcus from admin to member." → `/team/<id>` → "Role" → "member" → confirm → audit.
- **U-T4.** Admin: "Who has accessed Project Acme this week?" → `/team/<id>` → "Activity" (admin-only) → filter by project. Linked back to `runtime_run_usage` / `runtime_tool_invocations` joined by user_id.
- **U-T5.** "Hand off when someone leaves." → `/team/<id>` → "Offboard" wizard (admin) → choose new owner for their projects / agents / tools / connectors → confirm → audit. (Q12 re-evaluation: this is the controlled-handoff workflow; the naive admin-force-transfer endpoint is NOT shipped — handoff requires explicit per-asset reassign.)

### Memory journeys

- **U-M1.** "Atlas: 'I notice you always sign off with "Best, Parth" — save as preference?'" → toast → accept → memory `kind=preference, scope=user, body="..."` created.
- **U-M2.** "Show me what Atlas knows about my work." → `/memory` → filter `kind=fact` + `scope=user` → list.
- **U-M3.** "Make this team-shared." → memory detail → "Scope" → workspace → confirm → audit. (Other tenant members now read it.)
- **U-M4.** "This is wrong — Atlas thinks I'm a Java developer; I'm Python." → memory detail → "Edit" → fix → save → embedding re-computed in background.
- **U-M5.** "Forget about Project X." → memory list → bulk-select by tag → "Delete" → soft-delete 90d.

### Palette journeys

- **U-P1.** "Open the Acme Q1 plan." → ⌘K → type "acme q1" → top hit = library page → enter → navigate.
- **U-P2.** "Make this a routine." (cursor in a chat) → ⌘K → "make this a routine" → suggestion lifts the current chat as a routine draft → opens `/routines/new?from_chat=<id>`. (Routines Q10 lands here.)
- **U-P3.** "Show me errored routines." → ⌘K → "errored routines" → palette runs the filter on `/routines?status=errored`.
- **U-P4.** "Connect a calendar." (no calendar connected) → ⌘K → palette surfaces it as a contextual hint when matching no other results.
- **U-P5.** "Search my memories." → ⌘K → query in scope. Same result list as `/memory` semantic search.

### Settings journeys

- **U-S1.** "Mute Inbox notifications between 8pm and 8am." → `/settings/notifications` → quiet-hours editor → save.
- **U-S2.** "Change the Inbox notification default for everyone in our workspace." → `/settings/notifications` → "Workspace defaults" (admin) → set.
- **U-S3.** "Enable HMAC signing for all webhooks tenant-wide." → `/settings/security/webhooks` → toggle → audit. (Phase 11 Connectors webhooks read this default at create time.)

---

## §3 Data shape

### 3.1 Team — `packages/api-types/src/team.ts`

```typescript
export type TeamRole = "owner" | "admin" | "member" | "guest";
export type Presence = "active" | "away" | "in_meeting" | "offline";

export interface Person {
  readonly id: UserId;
  readonly tenant_id: TenantId;
  readonly display_name: string;
  readonly email: string;
  readonly avatar_url?: string;
  readonly role: TeamRole;
  readonly presence: Presence;
  readonly last_seen_at: string | null;
  readonly joined_at: string;
  readonly agents_count: number; // projection
  readonly projects_count: number; // projection
  readonly is_self: boolean; // server hint
}

export interface TeamListResponse {
  readonly people: ReadonlyArray<Person>;
  readonly next_cursor: string | null;
}

export interface PersonDetailResponse {
  readonly person: Person;
  readonly agents: ReadonlyArray<ItemRef>; // narrowed "agent"
  readonly projects: ReadonlyArray<ItemRef>; // narrowed "project"
  /** Admin-only — empty array for non-admin callers. */
  readonly recent_activity: ReadonlyArray<{
    readonly at: string;
    readonly summary: string;
    readonly target: ItemRef;
  }>;
}

export interface InviteRequest {
  readonly email: string;
  readonly role: TeamRole;
  /** Optional welcome note shown in the invite email. */
  readonly note?: string;
}

export interface OffboardingRequest {
  readonly target_user_id: UserId;
  readonly reassignments: ReadonlyArray<{
    readonly asset: ItemRef; // narrowed "agent" | "project" | "tool" | "connector"
    readonly new_owner_user_id: UserId;
  }>;
}
```

### 3.2 Memory — `packages/api-types/src/memory.ts`

```typescript
export type MemoryScope = "user" | "workspace";
export type MemoryKind = "skill" | "fact" | "preference";

export interface MemoryItem {
  readonly id: MemoryItemId;
  readonly tenant_id: TenantId;
  readonly scope: MemoryScope;
  readonly kind: MemoryKind;
  readonly title: string;
  readonly body: string;
  readonly tags: ReadonlyArray<string>;
  readonly created_by: { readonly kind: "user" | "agent"; readonly id: string };
  readonly last_used_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  /** Project scoping — same ACL rule as Phase 6 (cross-audit §1.3). */
  readonly project_id?: ProjectId | null;
}

export interface MemoryListResponse {
  readonly items: ReadonlyArray<MemoryItem>;
  readonly next_cursor: string | null;
}

export interface MemoryProposal {
  /** Server-generated proposal from runtime auto-extraction. */
  readonly id: string;
  readonly proposed_at: string;
  readonly proposed_kind: MemoryKind;
  readonly proposed_title: string;
  readonly proposed_body: string;
  /** The chat / run that produced this proposal. */
  readonly source: ItemRef;
  readonly status: "pending" | "accepted" | "rejected" | "snoozed";
}
```

### 3.3 Palette — `packages/api-types/src/palette.ts`

```typescript
export type PaletteHitKind =
  | "navigation" // jump-to-route
  | "entity" // jump-to-item (chat / agent / library / project / tool / connector / person / memory / routine)
  | "action" // do-something ("make this a routine", "onboard calendar")
  | "command"; // run-a-command (e.g. "/help")

export interface PaletteHit {
  readonly id: string; // hit_<ulid>
  readonly kind: PaletteHitKind;
  readonly title: string;
  readonly subtitle?: string;
  readonly icon_hint?: string;
  /** When kind="entity": the ItemRef target. */
  readonly target?: ItemRef;
  /** When kind="navigation": the route to navigate to. */
  readonly route?: string;
  /** When kind="action" / "command": the action token. */
  readonly action_token?: string;
  /** Server score; 0-1. Used for tiebreak between substrates. */
  readonly score: number;
}

export interface PaletteSearchRequest {
  readonly q: string;
  /** Hint from the host so the server can rank context-aware suggestions. */
  readonly context?: {
    readonly current_route?: string;
    readonly current_chat_id?: ConversationId;
    readonly current_project_id?: ProjectId;
  };
  readonly limit?: number;
}

export interface PaletteSearchResponse {
  readonly hits: ReadonlyArray<PaletteHit>;
  readonly took_ms: number;
}
```

### 3.4 Routines §9.7 Q9/Q10 — Atlas-proposed suggestions + "Make this a routine?" CTA

These ride on the existing `MemoryProposal` mechanic (auto-extracted hints; same Inbox routing). At runtime exit, the post-run extractor emits:

- A `MemoryProposal` (if the run revealed a fact / preference).
- A `RoutineProposal` (if the run looks repeatable — heuristic: same agent + similar prompt 3+ times in the last 30 days). RoutineProposal lives in `packages/api-types/src/routines.ts` and references the chat as a `from_chat` seed.
- An `AtlasCronSuggestion` (palette-surfaced): when the user has 5+ runs of the same shape spaced regularly, the palette ranks "Schedule this as a routine?" at the top when the user opens ⌘K from that chat.

All three are read-only projections in the palette; the user accepts/rejects via the canonical paths (memory accept = `POST /v1/memory`, routine accept = `POST /v1/routines`).

---

## §4 Endpoints

### 4.1 Team

- `GET /v1/team` — list (filter: `?role`, `?presence`, `?q`; sort: `display_name`, `last_seen_desc`, `joined_at_desc`)
- `GET /v1/team/{id}` — detail (admin sees `recent_activity` projection)
- `POST /v1/team/invite` — invite (admin) — `InviteRequest`
- `PATCH /v1/team/{id}/role` — change role (admin; cannot demote self; cannot remove sole owner)
- `POST /v1/team/{id}/offboard` — offboarding wizard (admin) — `OffboardingRequest` — cascades reassignments + revokes connectors + soft-deletes private memories per tenant policy
- `GET /v1/team/stream` — SSE for presence + role-change envelopes

### 4.2 Memory

- `GET /v1/memory` — list (filter: `?scope`, `?kind`, `?tag`, `?project_id`, `?q`; sort: `last_used_desc`, `created_at_desc`)
- `GET /v1/memory/{id}` — detail
- `POST /v1/memory` — create
- `PATCH /v1/memory/{id}` — edit (re-embeds in background)
- `DELETE /v1/memory/{id}` — soft-delete
- `POST /v1/memory/{id}/touch` — internal — runtime increments `last_used_at`
- `GET /v1/memory/search?q=...` — semantic + keyword (reuses Library's hybrid search engine; new index target_kind=memory)
- `GET /v1/memory/proposals` — pending auto-extraction proposals
- `POST /v1/memory/proposals/{id}/accept` / `/reject`
- `GET /v1/memory/stream` — SSE for created / updated / deleted / proposal_appended

### 4.3 Palette

- `GET /v1/palette/search?q=...&context=...` — single endpoint; server fans out to per-destination indexes; ranks; returns up to N hits within a budget (200ms p95).
- The server reads from a denormalized `palette_index` table (one row per searchable entity) refreshed by per-destination LISTEN/NOTIFY triggers. Each row carries `tenant_id + entity_kind + entity_id + title + body + tags + route` and a tsvector. Hybrid search (BM25 + per-tenant embedding) reuses Phase 7.5 infra; embed model `Purpose.PALETTE_RANKING`.

### 4.4 Settings

- `GET /v1/settings/notifications` — user defaults (quiet hours, per-destination toggle)
- `PATCH /v1/settings/notifications` — user patch
- `GET /v1/settings/workspace/notifications` — workspace defaults (admin)
- `PATCH /v1/settings/workspace/notifications` — admin patch
- `GET /v1/settings/security/webhooks` — workspace webhook signing defaults (admin)
- `PATCH /v1/settings/security/webhooks` — toggle HMAC default-on / require_ip_allowlist / max_secret_age_days

---

## §5 Storage

### 5.1 Reused tables

- `users` + `tenant_memberships` — Team destination's source of truth.
- `library_embeddings` (Phase 7.5) — extended with `target_kind = "memory"` rows. **No parallel memory_embeddings table.** DRY.
- `runtime_tool_invocations` + `runtime_run_usage` — Team `recent_activity` projection reads from here.
- `audit_events` — Settings changes + role changes + memory changes write through canonical helper.

### 5.2 New tables

- `memory_items` — `(id, tenant_id, scope, kind, title, body, tags, created_by, last_used_at, project_id, created_at, updated_at, deleted_at)`. Tenant + scope indexed. Soft-delete with 90d retention.
- `memory_proposals` — `(id, tenant_id, user_id, status, proposed_at, proposed_kind, proposed_title, proposed_body, source, decided_at)`. Status-indexed for the Inbox / palette feed.
- `palette_index` — `(tenant_id, entity_kind, entity_id, title, body, tags, route, tsv, embedding, updated_at)`. Vector + GIN indexes. Refresh triggers per-destination on insert/update/soft-delete.
- `presence_state` (in-memory KV — Redis if present, in-proc dict fallback) — `(tenant_id, user_id) → { state, last_seen_at }`. Volatile.
- `tenant_settings` + `user_settings` — JSONB blobs keyed by namespace (`notifications`, `security.webhooks`, `home.activity_window_hours`). Existing pattern from Phase 2 Home Q1.

### 5.3 Retention

- `memory_items.deleted_at`: hard-delete past 90 days (cascade embeddings).
- `memory_proposals` decided_at + 30 days: hard-delete.
- `palette_index`: stale entries (entity_kind/entity_id no longer exists) garbage-collected nightly.
- `presence_state`: in-memory; no retention.

---

## §6 ACL + audit

### 6.1 Team

- Read list: tenant member.
- Read detail: tenant member. Admin sees `recent_activity`; non-admin gets empty array.
- Invite: admin.
- Patch role: admin (cannot demote sole owner; cannot demote self).
- Offboard: admin. Cascades per `OffboardingRequest.reassignments` — each asset reassigned via the existing PATCH endpoints (no force-transfer; explicit per-asset).

### 6.2 Memory

- Read: scope=user → owner only; scope=workspace → tenant member; project-scoped via `is_project_member`.
- Write: owner (or admin for workspace-scoped). 404-not-403.
- Audit: created / updated (fields changed) / scope_changed / deleted / proposal_accepted / proposal_rejected.

### 6.3 Palette

- Read: tenant member; results pre-filtered by ACL of each underlying entity (so palette never leaks an item the user can't open).
- No write surface; no audit.

### 6.4 Settings

- User defaults: owner only.
- Workspace defaults: admin only.
- Audit: every Settings change writes through canonical helper.

---

## §7 Frontend surface

### 7.1 Team

- `/team` — catalog with `<PageHeader>` + `<FilterTabs>` (All / Admins / Members / Guests) + `<CardGrid>` of `PersonCard`s.
- `/team/<id>` — detail tabs (Overview / Agents / Projects / Activity (admin) / Settings (admin)).
- `TeamInviteWizard.tsx` — modal entry from "Invite" CTA.
- `OffboardingWizard.tsx` — step machine; reassignment picker.

### 7.2 Memory

- `/memory` — catalog with `<FilterTabs>` (Skills / Facts / Preferences) + `<DocList>`.
- `/memory/<id>` — detail tabs (Body / Provenance / Used by).
- `MemoryEditor.tsx` — title + body (markdown) + scope toggle + tags.
- `MemoryProposalToast.tsx` — site-wide toast that lifts pending proposals; reuse Inbox's notification component.

### 7.3 Palette

- `packages/chat-surface/src/shell/CommandPalette.tsx` — substrate-shared.
- `PaletteHitRow.tsx` — single row.
- Keyboard contract: ⌘K / Ctrl+K opens; Esc closes; ↑↓ navigate; Enter activates.
- Host wires a `PaletteSearchPort` (new port; mirrors NotificationPort/BadgePort pattern). Web → facade. Desktop → IPC.

### 7.4 Settings

- `/settings/notifications` — user defaults + workspace defaults (admin tab).
- `/settings/security/webhooks` — workspace webhook signing (admin).
- `/settings/profile` — display name + avatar (links IdP if connected).

### 7.5 ⌘K placement

- Topbar `<CommandPaletteTrigger>` button (visible affordance).
- Global keyboard hook (`useCommandPaletteHotkey`) on the shell.
- The "Make this a routine?" CTA on the chat canvas (Routines Q10) is a deep-link into the palette, not a separate dialog.

---

## §8 Cross-destination linking

- Every `ItemRef { kind: "person" }` resolves to `/team/<id>`.
- Memory items in chat → ItemLink to `/memory/<id>`.
- Palette hits whose target is in another destination open via the normal `<ItemLink>` resolver registry (cross-audit §1.1).
- Settings is profile-menu-mounted; not a destination per master §3.5. The page uses `<PageHeader>` for visual consistency.

---

## §9 Memory auto-extraction (Routines §9.7 Q9/Q10 implementation)

### 9.1 Extractor pipeline

After every run completes, an extractor (`services/ai-backend/src/agent_runtime/.../proposal_extractor.py`, mirrors the Todos extractor pattern from Phase 3) runs with `Purpose.MEMORY_EXTRACTION` (new enum value). It:

1. Reads the run's user messages + assistant final output.
2. Calls the LLM with a structured-output prompt asking for any of: `MemoryProposal[]`, `RoutineProposal[]`, `AtlasCronSuggestion[]`.
3. Writes pending proposals.

### 9.2 Display

- Memory proposals → toast (auto-dismiss after 8s) + permanent in `/memory/proposals`.
- Routine proposals → palette suggestions (top-rank when matching context); permanent in `/routines?proposed=true`.
- Cron suggestions → palette only; tied to the chat/routine that surfaced them.

### 9.3 Cost discipline

The extractor budget is capped at $0.001 per run (≈ 1k input tokens with a small model). Costs attribute to `runtime_model_call_usage` with `Purpose.MEMORY_EXTRACTION`.

---

## §10 Open questions

1. **Profile photo upload vs Gravatar.** **Recommend:** IdP-supplied first; Gravatar fallback; manual upload deferred to Wave 13.
2. **Multi-workspace identity.** Same email in multiple tenants — `users` table is tenant-scoped, so this works naturally; only the IdP layer needs cross-tenant pivot. **Recommend:** no special UX in Phase 12; user picks the workspace at login.
3. **Memory versioning.** Every edit a version? **Recommend:** yes — same pattern as Library pages (audit-driven version history; GC after 90d).
4. **Memory expiry.** Stale 6-month memories auto-archive? **Recommend:** no auto-expiry; "last used 6 months ago" UI hint sufficient; user-driven cleanup.
5. **Palette typo tolerance.** Fuzzy match aggressiveness? **Recommend:** edit-distance ≤ 2 OR token-prefix OR semantic-similarity ≥ 0.6 — any axis wins. Server returns the rank.
6. **Routines §9.7 Q12 re-evaluation.** Admin force-reassign owner. **Recommend:** STAYS DEFERRED — the offboarding wizard (U-T5) is the controlled handoff; naive force-transfer remains a security hazard per [cross-audit §9.8 Q1](../cross-audit.md).
7. **Settings page or destination?** Master PRD §3.5 says off-profile-menu, not a destination. **Recommend:** confirmed — `/settings/*` routes exist but no destination card in the nav.

---

## §11 Phasing within Phase 12 (P12-A/B/C sub-phases)

Given the size (three destinations + palette + Settings), Phase 12 dispatches more agents than earlier phases:

| Sub-phase | Scope                                                                                                                 | Estimated LOC | Order         |
| --------- | --------------------------------------------------------------------------------------------------------------------- | ------------- | ------------- |
| P12-A1    | api-types/team.ts + memory.ts + palette.ts                                                                            | ~500          | First         |
| P12-A2    | services/backend team/ — list + detail + invite + role + offboarding + SSE                                            | ~700          | After A1      |
| P12-A3    | services/backend memory/ — CRUD + proposals + search + SSE; reuses library_embeddings                                 | ~700          | After A1      |
| P12-A4    | services/backend palette/ — palette_index + search route + per-destination triggers                                   | ~600          | After A1      |
| P12-A5    | services/ai-backend memory extractor + Purpose.MEMORY_EXTRACTION + Purpose.PALETTE_RANKING + Purpose.MEMORY_RETRIEVAL | ~500          | Independent   |
| P12-A6    | services/backend settings/ — user + workspace settings JSONB + canonical Settings adapter                             | ~400          | Independent   |
| P12-A7    | facade — proxy /v1/team/_ + /v1/memory/_ + /v1/palette/_ + /v1/settings/_                                             | ~400          | After A2-A6   |
| P12-B1    | chat-surface destinations/team/ — TeamDestination + PersonCard + InviteWizard + OffboardingWizard                     | ~800          | After A1      |
| P12-B2    | chat-surface destinations/memory/ — MemoryDestination + MemoryEditor + ProposalToast                                  | ~700          | After A1      |
| P12-B3    | chat-surface shell/CommandPalette.tsx + PaletteHitRow + PaletteSearchPort                                             | ~600          | After A1      |
| P12-B4    | chat-surface settings/ pages (NotificationDefaults + WebhookSecurity)                                                 | ~500          | After A1      |
| P12-C1    | apps/frontend features/team/                                                                                          | ~400          | After A2 + B1 |
| P12-C2    | apps/frontend features/memory/                                                                                        | ~400          | After A3 + B2 |
| P12-C3    | apps/frontend features/palette/ + global hotkey                                                                       | ~300          | After A4 + B3 |
| P12-C4    | apps/frontend features/settings/                                                                                      | ~400          | After A6 + B4 |
| P12-D     | README pass + cross-audit reconciliation + final ship audit                                                           | ~200          | Last          |

Total: ~7700 LOC across ~16 agents. Orchestrator runs in 3 waves: contracts (A1) → backend + chat-surface (A2-A6 / B1-B4) → frontend (C1-C4) → polish (D).

---

## §12 Done definition

- Every endpoint in §4 implemented + tested (happy + ACL + tenant-isolation).
- Every component in §7 rendered + tested + a11y-pass.
- ⌘K opens in <50ms; hits return in <200ms p95.
- Memory auto-extraction proposals appear within 5s of run completion.
- "Make this a routine?" deep-link from chat canvas to palette works end-to-end.
- Settings changes audit through canonical helper.
- Memory embeddings live in `library_embeddings` (target_kind=memory) — no parallel table.
- Settings JSONB blobs use the existing `tenant_settings` / `user_settings` pattern — no parallel table.
- TU-1 invariant preserved: no direct LLM SDK imports; new Purpose values flow through `build_chat_model`.
- `is_project_member` is the only ACL helper.
- SP-1 primitives used everywhere; no inline color / spacing.
- All Wave-0 placeholder destinations (Team / Memory) are replaced.
- README updated with the four added destinations (Tools / Connectors / Team / Memory) and the ⌘K palette.
- Cross-audit §9.8-9.10 entries verified against the shipped code.

---

## §13 References

- [destinations-master-prd.md §5.9 Team / §5.10 Memory](../destinations-master-prd.md)
- [cross-audit.md](../cross-audit.md) §1.1 / §1.3 / §1.5 / §1.6 / §5.2 / §5.5 / §9.7 (Routines deferrals) / §9.8 Q1 (force-transfer stays deferred)
- [implementation-plan.md §2 Phase 12](../implementation-plan.md)
- [Routines PRD §9.7](routines-prd.md) — Q9 cron suggestions / Q10 "Make this a routine?" / Q12 force-reassign / Q14 notification defaults
- [Library PRD §6](library-prd.md) — embedding pipeline reused for memory
- [Phase 8 Agents PRD §4.9](agents-prd.md) — usage projection pattern reused for team activity lens
