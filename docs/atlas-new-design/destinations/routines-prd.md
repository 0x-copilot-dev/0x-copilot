# Routines Destination — Sub-PRD

**Status:** draft (2026-05-17)
**Owner:** parth (orchestrator) — implementation delegated to phase-5 impl agents
**Master:** [destinations-master-prd.md](../destinations-master-prd.md) (extended; Routines is the **12th destination** — not enumerated in the original 11-destination list because the master PRD predates this addition)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer + thread canvas
**Binding cross-PRD decisions:** [cross-audit.md](../cross-audit.md) — `ItemRef` (§1.1), ports (§1.2), project-scoped ACL (§1.3), audit `context` (§1.4), filter axis OR (§1.5), `<PageHeader>` (§1.6), branded IDs incl. `RoutineId` (§2.1), webhook security (§2.4), `<ItemLink>` registry (§3.3), cascade default (§5.3)
**Implementation phasing:** [implementation-plan.md](../implementation-plan.md) §2 Phase 5 (P5-A / P5-B), §4 merge order
**Design references:**

- Claude.ai's Routines UX (screenshot in dispatch brief): Name + Instructions + Model + Repository/Environment + Triggers + Connectors / Behavior / Permissions tabs. We are building **Atlas's enterprise version**, not a port.
- chat1.md project model: agents are persistent invokable workers; this destination introduces **scheduled invocations** of those workers (and ad-hoc routine-defined system prompts).

---

## §1 Premise + user job

### 1.1 What a Routine is

A **Routine** is a **scheduled or event-triggered agent run with a pinned configuration**. Concretely it is a durable record carrying:

1. A **name** (required, one line) — e.g. "Daily briefing", "Weekly Salesforce hygiene".
2. **Instructions** (multi-line system prompt) — what Atlas should do when the routine fires.
3. A pinned **model** (and optional reasoning depth) — the model preference used for every firing.
4. One or more **triggers** — schedule (cron-like), webhook (inbound HTTP), workspace-internal event, or manual ("Run now").
5. Per-routine **connector scope** — which connectors are in scope at fire time (a subset of the owner's connectors).
6. **Behavior settings** — autonomy (manual-approval vs auto-apply), retry policy, max duration, max tool calls, output target.
7. **Permissions** — read-only vs read-write, per-tool / per-skill allowlist, max output tokens, data-residency.
8. Optional **repository + environment** (code-routines; see §16 Q1) — deferred behind a feature flag; the shape lands in this PRD so we don't re-design later.

When a Routine fires, it produces an **ai-backend Run** under the existing run pipeline — **no new run-storage**. The Routine's execution history IS the set of ai-backend runs tagged with `source.kind = "routine"` and `source.routine_id = <id>`.

A Routine is the answer to: _"I want this work done on a schedule, with this exact configuration, without thinking about it every time."_

### 1.2 Why a separate destination instead of "scheduled chat" inside Chats

Three reasons, in priority order:

1. **A Routine is not a conversation.** A chat is an interactive thread the user steers turn-by-turn. A Routine is a **definition** that produces runs on its own cadence. Burying definitions inside Chats forces the user to scroll a chat history to find "the thing that fires at 6pm" — wrong information architecture. Chats answer _"what am I working on right now?"_; Routines answer _"what work is set up to happen on its own?"_.
2. **Different governance surface.** Routines need rotating webhook secrets, IP allowlists, cron editors, permission intersection at fire time, owner-pause workflows. These would dwarf the Chats destination if folded in.
3. **Different audit shape.** Chats audit message-edit/approval-accept events. Routines audit definition mutations PLUS every trigger fire PLUS every webhook hit (success and failed-auth). Mixing them in one stream makes both noisier.

Routines is therefore the **12th destination** in the workspace rail. Implementation-plan §2 P5-B introduces `"routines"` to `ShellDestinationSlug`.

### 1.3 User success states (what "done" looks like)

- _"I want a briefing every weekday at 6pm without thinking about it."_ → a Routine with cron `0 18 * * 1-5 GMT+5:30`, instructions describing the briefing, output target = inbox.
- _"When my customer support webhook fires, escalate-classify and file under the project."_ → a Routine with webhook trigger, output target = a library page in `Support escalations` project.
- _"Every time Salesforce gets a new opportunity over $500k, draft an exec-summary email."_ → a Routine with event trigger (`connector.salesforce.opportunity.created` with filter), output target = email-draft surface in the originating chat.
- _"I want a manual 'Run now' for the weekly investor update so I can fire it on demand and not by schedule."_ → a Routine with only a manual trigger.

### 1.4 Relationship to the Agents destination

| Concept     | What it is                                                                                                                                                                                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agent**   | A persistent invokable worker with a system prompt, skills, MCPs, memory, runbook history. (`/agents`.)                                                                                                                         |
| **Routine** | A scheduled invocation. A Routine **may** reference an Agent (use Agent X's configuration as the base + override instructions) or be **standalone** (Routine-defined system prompt without an Agent backing it). (`/routines`.) |
| **Run**     | A single execution. Produced either interactively (Chats), by an Agent invocation, by a manual Routine fire, or by a triggered Routine fire. Lives in ai-backend.                                                               |
| **Chat**    | An interactive thread that orchestrates one-or-many runs. (`/chats`.)                                                                                                                                                           |

**Source-of-truth rule:** when a Routine references an Agent (`agent_id` set), the Agent's `system_prompt`, `skills`, `mcps`, `memory_ref` are **resolved at fire time, not snapshotted at Routine save**. This avoids configuration drift between Agents and Routines. The Routine's `instructions` field then **appends** to (not replaces) the Agent's `system_prompt` — explicit composition. (See §16 Q11 if product prefers snapshot semantics.)

---

## §2 Source-of-truth map

Per master PRD §2.2, each artefact has **exactly one** canonical location.

| Concern                                         | Canonical file                                                                                                      | Status                               |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Wire types                                      | `packages/api-types/src/routines.ts` (NEW)                                                                          | introduce; re-export from `index.ts` |
| Branded `RoutineId`                             | `packages/api-types/src/brands.ts` (cross-audit §2.1)                                                               | already promised by SP-1             |
| Destination (router-mounted)                    | `packages/chat-surface/src/destinations/routines/RoutinesDestination.tsx` (NEW)                                     | introduce                            |
| Context panel                                   | `packages/chat-surface/src/destinations/routines/RoutinesPanel.tsx` (NEW)                                           | introduce                            |
| Editor (`/routines/new`, `/routines/<id>/edit`) | `packages/chat-surface/src/destinations/routines/RoutineEditor.tsx` (NEW)                                           | introduce                            |
| Detail (`/routines/<id>`)                       | `packages/chat-surface/src/destinations/routines/RoutineDetail.tsx` (NEW)                                           | introduce                            |
| Per-tab editor subcomponents                    | `packages/chat-surface/src/destinations/routines/tabs/{Connectors,Behavior,Permissions,Triggers}Tab.tsx` (NEW)      | introduce                            |
| Cron editor primitive                           | `packages/chat-surface/src/destinations/routines/cron/CronEditor.tsx` (NEW)                                         | introduce                            |
| Item-link registry registration                 | `packages/chat-surface/src/destinations/routines/index.ts` (registers `<ItemLink kind="routine">`)                  | introduce                            |
| Backend route module                            | `services/backend/src/backend_app/routines/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`              | introduce                            |
| Backend Postgres schema                         | `services/backend/src/backend_app/routines/schema.py` + Alembic migration                                           | introduce                            |
| Facade proxy                                    | `services/backend-facade/src/backend_facade/routines_routes.py` (NEW)                                               | introduce                            |
| **Scheduler worker**                            | `services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py` (NEW) — see §3.7 for the architectural call      | introduce                            |
| Run-source tagging (`source.kind="routine"`)    | extension of `services/ai-backend/src/agent_runtime/api/run_coordinator.py` + run-record schema                     | extend                               |
| Webhook ingest endpoint                         | `services/backend-facade/src/backend_facade/routines_webhook_routes.py` (NEW; separate to keep auth-shape distinct) | introduce                            |
| Webhook secret storage                          | `services/backend/src/backend_app/routines/webhook_secrets.py` — uses existing `TokenVault`                         | introduce                            |
| Frontend HTTP wrappers + SSE                    | `apps/frontend/src/api/routines.ts` (NEW)                                                                           | introduce                            |
| App switch case (mount destination)             | `apps/frontend/src/app/App.tsx` (extend)                                                                            | extend                               |
| `ShellDestinationSlug` extension                | `packages/chat-surface/src/shell/destinations.ts` — add `"routines"` as the 12th slug                               | extend                               |

A second copy of any of these is a bug.

---

## §3 Architecture

### 3.1 Layout

Standard workspace shell from `ChatShell.tsx` with `<RoutinesPanel>` in the ContextPanel slot. Right rail collapsed for this destination (PRD §10 default; routines does not opt in). List vs detail vs editor pivot lives inside the main pane and is driven by `route.view` + `route.id`:

- `{ view: null, id: null }` → **list** view
- `{ view: null, id: <RoutineId> }` → **detail** view
- `{ view: "edit", id: <RoutineId> }` → **editor** (edit mode)
- `{ view: "new", id: null }` → **editor** (create mode)

Matches master §4.5 routing convention `/<dest>/<view?>/<id?>`.

### 3.2 List view

`RoutinesDestination.tsx` (when route.id null and route.view null) renders, top to bottom:

1. `<PageHeader title="Routines" subtitle="Scheduled and triggered runs" primaryAction={{ label: "New routine", onClick: createNew }} badges={[activeCount, errored ? <Pill tone="alert">{errored} errored</Pill> : null]} />` (cross-audit §1.6 shape).
2. `<FilterTabs value={filter} options={["all","active","paused","errored","draft"]} counts={countsByFilter} />` — multi-value OR semantics per cross-audit §1.5 (`?filter[status]=active&filter[status]=paused`).
3. **List body** — virtualized when total > 100 (reuse `@tanstack/react-virtual` introduced by Inbox/Todos).

Per-row content:

```
[ status pill ]  [ Routine name                                  [ next-fire-at ] ]
                 [ description / trigger summary, e.g. "Runs weekdays at 18:00 GMT+5:30" ]
                 [ triggerChips: ⏱ cron · 🔗 webhook · ⚡ event ]
                 [ ownerChip ] [ modelChip ] [ outputTargetChip ]
                 [ hover actions: ▶ Run now · ⏸ Pause / ▶ Activate · ⋯ Edit · 🗑 Delete ]
```

- Status pill uses cross-audit §1.6 `<StatusPill tone>`: `draft` (neutral), `active` (ok), `paused` (info), `errored` (alert).
- "Next fire at" shows `formatRelativeTime(next_fire_at, now)` (cross-audit §3.4 hoisted util). When the routine has no schedule trigger, shows the next-event-source label ("Webhook · waiting" / "Event · waiting" / "Manual only").
- The optional "Routines next-fire countdown" is the partial-failure-aggregated section that cross-audit §2.3 marked as `SectionResult<T>`-eligible — only relevant on Home aggregation, not on the routines list itself.

### 3.3 Panel view (context panel)

`RoutinesPanel.tsx` composes the generic `<ContextPanel title="Routines" subtitle="Scheduled and triggered work">`. Sections, top to bottom:

1. **Quick filters** — same 5 axes as the main `FilterTabs`, listed vertically with counts. One source of truth for filter state.
2. **Search** — debounced 250ms; `GET /v1/routines?q=...` searches over `name`, `description`, `instructions` (subject to `LLM_OUTPUT_REDACT` — see §7.5 below).
3. **By trigger kind** — collapsible groups: Schedule (count), Webhook (count), Event (count), Manual (count). Multi-trigger routines appear under every applicable group.
4. **By project** — list of projects with ≥1 routine.
5. **By owner** — list of owners with ≥1 routine. (Tenant admins use this to triage; non-admins see only themselves + collaborators in their projects.)
6. **Saved searches** — same primitive as Inbox §3.3 #5; ≤ 20 per user.
7. **Footer** — links to "Routine quotas" admin page (when admin) and "Webhook security guide" doc.

### 3.4 Detail view (`/routines/<id>`)

`RoutineDetail.tsx` renders stacked sections, top to bottom:

1. **Header** — status pill + name + `[Edit]` + `[⋯]` menu; instructions preview (truncated to 2 lines, expandable); ownerChip + modelChip + createdAtRelative; action row: `[Run now] [Pause/Activate] [Disable]`.
2. **Triggers** — one card per trigger. Schedule cards show "Runs weekdays at 18:00 GMT+5:30 · next: in 2h". Webhook cards show URL + `[Copy URL] [Rotate secret] [Reveal secret]`. Event cards show event source + filter summary. `[+ Add trigger]` footer.
3. **Configuration summary** — read-only summary of Connectors / Output target / Behavior / Permissions; "Edit" pivots to the editor on the matching tab.
4. **Run history** — list of `ItemRef { kind: "run" }` rendered via `<ItemLink>` (cross-audit §1.1 / §3.3). Proxied through `GET /v1/routines/<id>/runs` from ai-backend (filter on `source.kind=routine, source.routine_id=<id>` — single source of truth for runs).
5. **Audit log** — last 20 audit rows for `target_kind=routine, target_id=<id>`. See §6.

Behaviour:

- **Run-now** posts `POST /v1/routines/<id>/runs` (manual trigger). Creates a Run in ai-backend with `source = { kind: "routine", routine_id, trigger_kind: "manual" }`; frontend navigates to the new run / output target.
- **Pause / Activate** calls `PATCH /v1/routines/<id>` with `status`. Optimistic UI with rollback.
- **Rotate secret** (per webhook trigger): `POST .../triggers/<trigger_id>/rotate-secret`; old secret enters **7-day grace window** per cross-audit §2.4 — yellow chip + countdown in UI.
- **Reveal secret** is single-show: backend returns cleartext **only once**, modal copy-button via `ClipboardPort` (cross-audit §1.2). After that, only the masked form (`****...abcd`) is visible.

### 3.5 Editor (`/routines/new` and `/routines/<id>/edit`)

`RoutineEditor.tsx` is a tabbed editor. Header: draft-state pill + name input + `[Save]` `[Cancel]`. Tabs: **Basics · Triggers · Connectors · Behavior · Permissions**.

**Basics tab** (the rest live in §3.8–§3.10):

| Field                    | Constraint                                              |
| ------------------------ | ------------------------------------------------------- |
| Name                     | required, ≤80 chars                                     |
| Description              | optional, ≤200 chars                                    |
| Instructions             | multi-line, ≤16 KB                                      |
| Model                    | picker; see `/v1/models`                                |
| Depth                    | enum `ReasoningDepth` (re-uses composer's contract)     |
| Base agent               | optional Agents picker; null = standalone (see §1.4)    |
| Repository + Environment | feature-flagged; §16 Q1                                 |
| Project                  | optional; project-scoped ACL applies (cross-audit §1.3) |

Rules: Save persists as draft if any required field missing, else as active (with explicit "Save as draft" secondary action). Cancel prompts on dirty form. Each tab has a validation badge (red dot) when it has unresolved errors. Tabs are ARIA tabs (master §3.6, native semantics).

### 3.6 Trigger kinds (the four)

A Routine has **at least one** trigger and may have many. Validation rejects zero-trigger routines unless `status = "draft"`.

| Kind         | Wire shape (see §4)                                                                                                                                                                                                                    | UX                                                                                                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **schedule** | `{ kind: "schedule", cron: string, tz: string }`                                                                                                                                                                                       | Cron editor (§3.6.1). Human-readable preview ("Runs weekdays at 18:00 GMT+5:30"). Tz-aware (defaults to user's IdP tz; falls back to UTC).                                   |
| **webhook**  | `{ kind: "webhook", trigger_id: TriggerId, secret_masked: string, secret_rotated_at?: ISO, secret_grace_until?: ISO, ip_allowlist: CIDR[] }`                                                                                           | URL displayed via `<CodeBlock>` with `[Copy]` (ClipboardPort). Rotation button + grace countdown. IP allowlist chip-input (validates CIDR client-side, server re-validates). |
| **event**    | `{ kind: "event", event_source: EventSource, filter?: EventFilter }` where `EventSource = "connector.salesforce.opportunity.created" \| "inbox.item_created" \| "library.page_created" \| "library.file_uploaded" \| ...` (see §3.6.2) | Event-source picker with allowlist (no free-text). Filter editor: `{field, op, value}` triples; UI is form-driven (no DSL string for end users).                             |
| **manual**   | `{ kind: "manual" }`                                                                                                                                                                                                                   | Adds the "Run now" button to the Detail view (which is _always_ present, but a manual-only routine has no other trigger).                                                    |

#### 3.6.1 Cron editor (accessibility-first; per master §3.6)

The cron editor has **two modes**, default human-readable:

- **Human-readable mode** (default) — discrete form: frequency (Hourly / Daily / Weekdays / Weekly / Monthly / Custom), time picker (24h, user-tz-aware), day-of-week multi-select (when Weekly), day-of-month (when Monthly), timezone (defaults to user tz).
- **Advanced mode** (opt-in toggle) — raw cron string editor with live human-readable preview underneath. Validates against `croniter` server-side.

Both modes preview "Next 3 fire times: …" rendered relative to the chosen timezone. Live region announces the preview when it changes (master §3.6).

**Hard constraint:** **1-minute minimum granularity.** No `@reboot`, no per-second triggers, no nested ranges that resolve to sub-minute (e.g. `*/30 * * * * *`). The server rejects with `422` and explains why. (See cross-audit §3.5 deferred-features inventory.)

#### 3.6.2 Event sources (Wave 5 allowlist)

`EventSource` is **server-allowlisted**. The Wave-5 allowlist is workspace-internal **only** (anti-goal: not a feed reader; no third-party scheduler):

- `inbox.item_created` (filterable by `kind` / `priority` / `sender.kind`)
- `library.page_created` (filterable by `project_id` / `tag`)
- `library.file_uploaded` (filterable by `mime_type` / `project_id`)
- `library.dataset_updated` (filterable by `dataset_id`)
- `todos.item_created` (filterable by `priority` / `source.kind`)
- `chats.run_completed` (filterable by `status`)
- `connector.<connector_kind>.<entity>.created|updated|deleted` — gated per connector. Initial Wave 5 set: `salesforce.opportunity.{created,updated}`, `gmail.thread.received` (with filter), `calendar.event.created`. Each connector ships an event manifest that the backend validates against.

**Not included** in Wave 5 (deferred to later phases / forever):

- Atlas-proposed cron suggestions (`autopropose:*`) — see §16 Q10.
- External-source events (e.g., Stripe webhook → Routines) — connector concern.
- Per-second / per-millisecond events — not supported.

### 3.7 Scheduler architecture — Option A (in-process worker mirroring `retention_sweeper.py`)

**Decision:** scheduler lives at `services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py` — an in-process loop on the existing ai-backend `runtime_worker` (Option A in the dispatch brief).

**Why Option A, not Option B (dedicated `services/scheduler/`):**

- **DRY** — reuses `runtime_worker`'s claim/retry/metrics/loop scaffolding (`RetentionSweeperLoop` is the template). Option B re-builds a worker process, Dockerfile, CI pipeline, deploy path — for a single cron loop.
- **Single source of truth** — routine fires produce ai-backend Runs through the existing run coordinator. Same address space means one Python module call; Option B introduces cross-process IPC for no behavioural gain.
- **Simple & elegant** — one new file under existing `jobs/` directory. Master rule "every deployable service owns its own venv/Dockerfile/image" makes a new service for a cron loop a poor cost/value trade.
- **Performant** — one DB poll every `ROUTINE_SCHEDULER_INTERVAL_SECONDS`; horizontally-scalable via `FOR UPDATE SKIP LOCKED`.

Trade-off accepted: if routine fire rate becomes large (≥1000 fires/min/tenant), the ai-backend worker pool grows. Wave-7+ concern; the lock pattern scales linearly with worker count.

**Hard rule deriving from this choice:** the scheduler **never imports product persistence directly**. It calls `backend` over HTTP (`GET /internal/v1/routines/due?as_of=…`, `POST /internal/v1/routines/<id>/fires`). Cross-service boundary holds per root CLAUDE.md.

**Claim semantics — lock-based:** `SELECT … FOR UPDATE SKIP LOCKED` (same pattern `runtime_worker`'s run-claim and `retention_sweeper` use). Optimistic compare-and-swap is rejected: it produces wasted work when N workers race for the same row.

**Loop shape:** `RoutineSchedulerLoop` mirrors `RetentionSweeperLoop`. Default interval `60s`, configurable. Per tick: (1) claim a batch of routines with `next_fire_at <= now()` (skip-locked), (2) for each: validate owner status, intersect permissions, create Run via `run_coordinator`, advance `next_fire_at` to next cron occurrence, (3) on permission-intersection failure → auto-pause + Inbox item (§7.4), (4) on create-run failure → record retry, back off, eventually mark errored.

**Missed-fire policy default: `fire_once`** (see §16 Q7). If a routine was paused 3 days and 3 daily fires were missed, on activation the scheduler does **one** fire (the most-recent missed window) and records 2 `routine.fire_skipped` audit rows for the others. Rationale: replaying 3 daily briefings on Monday morning is worse UX than skipping. Per-routine override (`fire_once` / `fire_all` / `skip_all`) is in the wire.

### 3.8 Connectors tab

For each connector the **owner** has connected, the editor shows a per-row toggle + scope override (`inherit | read_only | custom`). The picker is filtered to the owner's connected connectors — the routine cannot enable a connector the owner does not have.

- **inherit** (default) — uses owner's current connector scope at fire time (re-resolved every fire; no stale snapshot).
- **read_only** — downgrades to read-only regardless of owner scope.
- **custom** — owner narrows scope (e.g. only `salesforce.opportunity:read`). Narrowing allowed; widening is not.

**Hard rule:** `effective_scope = MIN(routine_choice, owner_scope_at_fire_time)`. Ownership of connector secrets stays owner-only and is never shared with project members.

### 3.9 Behavior tab

| Field          | Choices / range                                                                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Autonomy       | `manual_approval` (every diff requires user accept) · `auto_apply` (low-risk auto, high-risk asks) · `full_auto` (no approval; only meaningful for read-only) |
| Retries        | max 0-10, backoff `exponential` / `linear` / `none`, base seconds                                                                                             |
| Max duration   | 60s–7200s (hard cap 2h)                                                                                                                                       |
| Output target  | `inbox` · `library_page` (mode: `new_per_fire` / `update_same`) · `existing_chat` · `project_log`. Each non-inbox target carries an `ItemRef`.                |
| Notify success | any of `owner` / `project_members` / `tenant_admin`. Routes via Inbox; desktop native notification if `NotificationPort.isAvailable()`.                       |
| Notify failure | same axes (default: owner + project_members).                                                                                                                 |

Cross-audit §5.3 cascade default applies to `output_target`: if the target was deleted, the next fire records `routine.output_target_dead_link` audit, falls back to Inbox with an explanatory message; routine is NOT auto-paused for a dead output target alone (only for owner offboarding or critical-connector disconnect; see §7.4).

### 3.10 Permissions tab

| Field                  | Choices / range                                                                                                                           |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Scope                  | `read_only` (forbids side-effect tools) · `read_write`                                                                                    |
| Allowed tools          | multi-select; defaults to "all owner-approved"                                                                                            |
| Allowed skills         | multi-select; defaults to "all owner-approved"                                                                                            |
| Max tool calls/fire    | 1–5000 (default 200)                                                                                                                      |
| Max output tokens/fire | 1–200000 (default 32000)                                                                                                                  |
| Data residency         | `inherit` (tenant default) · `us_only` · `eu_only` · `apac_only` — fails closed at fire time if the runtime cannot satisfy the constraint |

**Hard rule (permission intersection at fire time):**

```
effective = INTERSECT(routine.permissions, owner.current, project.current if filed else MAX)
```

If `effective` does not satisfy `routine.permissions` (owner lost a tool, project lost data-residency scope, etc.):

1. The routine **does not fire** this tick.
2. Auto-pauses with `pause_reason="permission_shrinkage"`.
3. Inbox item (kind=error, priority=high) → owner, with `<ItemLink kind="routine">` + "Re-authorize and resume" CTA.
4. Audit row `routine.auto_paused`, `context = { reason: "permission_shrinkage", missing: [...] }`.

Resume requires explicit owner action — auto-resume on permission restoration is **out of scope** (avoids surprise fires after a vacation re-auth; see §16 Q5).

### 3.11 Manual fire ("Run now")

`POST /v1/routines/<id>/runs` creates an immediate run, regardless of trigger kind. Auth rule (see §16 Q2 for product confirmation):

- **Default:** owner-only.
- **Override:** routine can declare `permissions.manual_fire = "owner" | "project_members" | "tenant"` for explicit cases ("Acme renewal play book — any AE on the project can fire").

Rate limit: per-user 60 manual-fires/hour across all routines (prevents abuse).

### 3.12 Webhook fire flow

External system → `POST /v1/webhook/routines/<trigger_id>` with `X-Atlas-Routine-Secret: <secret>` (optional source-IP must match allowlist CIDRs).

Facade-side: look up trigger, validate secret (constant-time compare against current + grace-window secrets per cross-audit §2.4), validate source IP if allowlist non-empty, validate Content-Type + size (≤256KB), audit-log success-or-failure per §6.1 with `context = { trigger_id, source_ip, auth_method }`. On success → `POST /internal/v1/routines/<routine_id>/fires {trigger_id, payload}`; returns `200 {fire_id, run_id}` (or 401/403/422 on failure).

The payload is stored with the fire record (≤256KB JSON) so users can inspect what triggered the routine. Sensitive payload data must be filtered upstream by the caller — Atlas does not redact unknown shapes.

---

## §4 Wire contracts (per master §3.5 + cross-audit §1.5)

### 4.1 Types (`packages/api-types/src/routines.ts`)

```typescript
import type {
  RoutineId,
  ProjectId,
  UserId,
  AgentId,
  ToolId,
  SkillId,
  ConnectorId,
  RunId,
  TenantId,
} from "./brands";
import type { ItemRef } from "./refs";
import type { ReasoningDepth } from "./chats"; // existing

export type TriggerId = string & { readonly __brand: "TriggerId" };
export type RoutineFireId = string & { readonly __brand: "RoutineFireId" };

export type RoutineStatus = "draft" | "active" | "paused" | "errored";

export type RoutineMissedFirePolicy = "fire_once" | "fire_all" | "skip_all";

export type RoutineAutonomy = "manual_approval" | "auto_apply" | "full_auto";

export type RoutineScope = "read_only" | "read_write";

export type RoutineDataResidency =
  | "inherit"
  | "us_only"
  | "eu_only"
  | "apac_only";

export type RoutineOutputTargetKind =
  | { kind: "inbox" }
  | { kind: "library_page"; ref: ItemRef; mode: "new_per_fire" | "update_same" }
  | { kind: "existing_chat"; ref: ItemRef }
  | { kind: "project_log"; ref: ItemRef };

export type RoutineTrigger =
  | { kind: "schedule"; trigger_id: TriggerId; cron: string; tz: string }
  | {
      kind: "webhook";
      trigger_id: TriggerId;
      secret_masked: string;
      secret_rotated_at: string | null;
      secret_grace_until: string | null;
      ip_allowlist: ReadonlyArray<string>; // CIDR
    }
  | {
      kind: "event";
      trigger_id: TriggerId;
      event_source: string; // server-allowlisted; client validates against /v1/routines/event-sources
      filter: ReadonlyArray<{
        readonly field: string;
        readonly op:
          | "eq"
          | "ne"
          | "gt"
          | "gte"
          | "lt"
          | "lte"
          | "in"
          | "matches";
        readonly value: string | number | boolean | ReadonlyArray<string>;
      }>;
    }
  | { kind: "manual"; trigger_id: TriggerId };

export interface RoutineConnectorConfig {
  readonly connector_id: ConnectorId;
  readonly mode: "inherit" | "read_only" | "custom";
  readonly custom_scope?: ReadonlyArray<string>; // valid only when mode="custom"
}

export interface RoutineBehavior {
  readonly autonomy: RoutineAutonomy;
  readonly max_retries: number; // 0-10
  readonly backoff: "exponential" | "linear" | "none";
  readonly backoff_base_seconds: number;
  readonly max_duration_seconds: number; // 60 - 7200
  readonly output_target: RoutineOutputTargetKind;
  readonly notify_on_success: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
  readonly notify_on_failure: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
}

export interface RoutinePermissions {
  readonly scope: RoutineScope;
  readonly allowed_tools: ReadonlyArray<ToolId>;
  readonly allowed_skills: ReadonlyArray<SkillId>;
  readonly max_tool_calls_per_fire: number;
  readonly max_output_tokens_per_fire: number;
  readonly data_residency: RoutineDataResidency;
  readonly manual_fire: "owner" | "project_members" | "tenant"; // §3.11
}

export interface Routine {
  readonly id: RoutineId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly name: string; // ≤ 80 chars
  readonly description: string; // ≤ 200 chars
  readonly instructions: string; // ≤ 16 KB
  readonly model: string; // model id; see /v1/models
  readonly depth: ReasoningDepth | null;
  readonly base_agent_id: AgentId | null;
  readonly repository?: { url: string; ref: string }; // §16 Q1; feature-flagged
  readonly environment?: Record<string, string>; // §16 Q1; feature-flagged
  readonly status: RoutineStatus;
  readonly pause_reason: string | null;
  readonly triggers: ReadonlyArray<RoutineTrigger>;
  readonly connectors: ReadonlyArray<RoutineConnectorConfig>;
  readonly behavior: RoutineBehavior;
  readonly permissions: RoutinePermissions;
  readonly missed_fire_policy: RoutineMissedFirePolicy;
  readonly next_fire_at: string | null; // ISO; null for webhook/event/manual-only
  readonly last_fire_at: string | null;
  readonly last_fire_status: "succeeded" | "failed" | "skipped" | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface RoutineFire {
  readonly id: RoutineFireId;
  readonly tenant_id: TenantId;
  readonly routine_id: RoutineId;
  readonly trigger_kind: "schedule" | "webhook" | "event" | "manual";
  readonly trigger_id: TriggerId;
  readonly run_ref: ItemRef; // ref.kind = "run"
  readonly status: "queued" | "running" | "succeeded" | "failed" | "skipped";
  readonly skip_reason: string | null;
  readonly payload_snapshot?: unknown; // webhook payload, event payload; ≤ 256KB
  readonly created_at: string;
  readonly completed_at: string | null;
}

export interface RoutineListResponse {
  readonly items: ReadonlyArray<Routine>;
  readonly next_cursor: string | null;
}
```

### 4.2 Endpoints (facade — what apps call)

| Method | Path                                                    | Purpose                                                                                                                                                     |
| ------ | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/routines`                                          | List. Filter axes: `status` (multi-value OR per cross-audit §1.5), `owner_user_id`, `project_id`, `trigger_kind`, `q`, `sort`.                              |
| GET    | `/v1/routines/{id}`                                     | Single routine + triggers (with masked secrets).                                                                                                            |
| POST   | `/v1/routines`                                          | Create. Body: full `Routine` minus server-assigned fields.                                                                                                  |
| PATCH  | `/v1/routines/{id}`                                     | Mutate definition. Owner-only writes (§7). Audited per §6.                                                                                                  |
| DELETE | `/v1/routines/{id}`                                     | Soft delete. Tombstone retained per §5.                                                                                                                     |
| POST   | `/v1/routines/{id}/runs`                                | **Manual fire** ("Run now"). §3.11 ACL. Returns the new run ref.                                                                                            |
| POST   | `/v1/routines/{id}/activate`                            | Set `status=active`. Validates triggers + permissions; recomputes `next_fire_at`.                                                                           |
| POST   | `/v1/routines/{id}/pause`                               | Set `status=paused` with optional `pause_reason`.                                                                                                           |
| POST   | `/v1/routines/{id}/triggers`                            | Add a trigger.                                                                                                                                              |
| PATCH  | `/v1/routines/{id}/triggers/{trigger_id}`               | Update a trigger.                                                                                                                                           |
| DELETE | `/v1/routines/{id}/triggers/{trigger_id}`               | Remove a trigger. (Validation: cannot remove the last trigger of an active routine; pause first.)                                                           |
| POST   | `/v1/routines/{id}/triggers/{trigger_id}/rotate-secret` | Rotate webhook secret. Returns new cleartext **once**. Starts 7d grace window per cross-audit §2.4.                                                         |
| GET    | `/v1/routines/{id}/fires`                               | List fires (paginated). Filter by `status` / time range.                                                                                                    |
| GET    | `/v1/routines/{id}/runs`                                | Proxy to `ai-backend` GET `/v1/agent/runs?source[kind]=routine&source[routine_id]=<id>` — single source of truth for run history.                           |
| GET    | `/v1/routines/event-sources`                            | Server-allowlisted event source manifest (for the Triggers tab event-source picker).                                                                        |
| GET    | `/v1/routines/stream`                                   | SSE; envelopes: `routine_created`/`routine_updated`/`routine_deleted`/`routine_fired`/`routine_paused`. `?after_sequence=N` reconnect per cross-audit §5.2. |

### 4.3 Endpoints (webhook ingest)

Mounted on the facade at a **separate router** to keep auth-shape distinct from `/v1/*` (which expects bearer auth).

| Method | Path                                | Purpose                                                                                                   |
| ------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------- |
| POST   | `/v1/webhook/routines/{trigger_id}` | Webhook fire. Auth: `X-Atlas-Routine-Secret` header (per cross-audit §2.4). Body: arbitrary JSON ≤ 256KB. |

### 4.4 Endpoints (internal — ai-backend ↔ backend)

| Method | Path                                              | Purpose                                                                                                                                                          |
| ------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/internal/v1/routines/due?as_of=<ISO>&limit=<N>` | Scheduler poll. Returns up to `limit` routine ids whose `next_fire_at <= as_of` AND `status='active'`, claimed `FOR UPDATE SKIP LOCKED` for `CLAIM_TTL_SECONDS`. |
| POST   | `/internal/v1/routines/{id}/fires`                | Scheduler / webhook ingest writes a fire record + emits stream envelope. Returns `{fire_id, run_id}`.                                                            |
| PATCH  | `/internal/v1/routines/{id}/fires/{fire_id}`      | Update fire status / completion. Called by run-coordinator when the run completes/fails.                                                                         |
| POST   | `/internal/v1/routines/{id}/auto-pause`           | Scheduler reports permission-shrinkage; backend persists + emits Inbox item via existing Inbox producer (§3.10 / §7.4).                                          |

### 4.5 Filter / sort allowlist (per cross-audit §1.5)

- `filter[status]`: `draft` | `active` | `paused` | `errored` (multi-value = OR).
- `filter[trigger_kind]`: `schedule` | `webhook` | `event` | `manual` (multi-value = OR).
- `filter[project_id]`: ProjectId (multi-value = OR).
- `filter[owner_user_id]`: UserId (multi-value = OR; project-scoped read enforced per §7).
- `sort`: `name:asc` | `name:desc` | `next_fire_at:asc` (default for active) | `created_at:desc` (default for all) | `last_fire_at:desc`.
- `q`: full-text on `name + description` (GIN index per §5.2). Instructions are NOT searchable (treated as sensitive — see §7.5).

---

## §5 Storage + retention

Per master §3.3 + cross-audit §1.3 (project-scoped access where applicable).

### 5.1 Tables (Postgres, owned by `services/backend`)

**`routines`** — one row per routine.

| Column                                               | Type                            | Notes                                                                        |
| ---------------------------------------------------- | ------------------------------- | ---------------------------------------------------------------------------- |
| `id` / `tenant_id` / `owner_user_id`                 | uuid PK / NN / NN               | Owner immutable post-create.                                                 |
| `project_id`                                         | uuid NULL                       | Project filing per cross-audit §1.3.                                         |
| `name` / `description` / `instructions`              | text (≤80 / ≤200 / ≤16384)      | All NOT NULL (description defaults to '').                                   |
| `model` / `depth` / `base_agent_id`                  | text NN / text NULL / uuid NULL | `depth` = enum `ReasoningDepth`. `base_agent_id` loose FK; resolved at fire. |
| `repository` / `environment`                         | jsonb NULL                      | Null unless feature-flag-enabled (§16 Q1).                                   |
| `status` / `pause_reason`                            | text NN / text NULL             | status ∈ {draft, active, paused, errored}.                                   |
| `behavior` / `permissions`                           | jsonb NOT NULL                  | Match `RoutineBehavior` / `RoutinePermissions`.                              |
| `missed_fire_policy`                                 | text NN DEFAULT 'fire_once'     |                                                                              |
| `next_fire_at` / `last_fire_at` / `last_fire_status` | timestamptz NULL                | `next_fire_at` null for webhook/event/manual-only.                           |
| `claim_token` / `claim_expires_at`                   | uuid NULL / timestamptz NULL    | Scheduler `FOR UPDATE SKIP LOCKED` claim; expires on worker crash.           |
| `created_at` / `updated_at` / `deleted_at`           | timestamptz                     | `deleted_at` = soft-delete marker.                                           |

**`routine_triggers`** — one row per trigger (≥1 per routine).

| Column                                                    | Type / Notes                                                        |
| --------------------------------------------------------- | ------------------------------------------------------------------- |
| `id` (TriggerId) / `tenant_id` / `routine_id`             | uuid; `routine_id` FK ON DELETE CASCADE                             |
| `kind`                                                    | `schedule` / `webhook` / `event` / `manual`                         |
| `config`                                                  | jsonb: kind-specific (cron+tz / event_source+filter / ip_allowlist) |
| `secret_ref` / `secret_rotated_at` / `secret_grace_until` | TokenVault ref (kind=webhook only); 7d grace per cross-audit §2.4   |
| `created_at` / `updated_at`                               | timestamptz                                                         |

**`routine_connector_configs`** — one row per (routine, connector).

| Column                                                              | Type / Notes                                   |
| ------------------------------------------------------------------- | ---------------------------------------------- |
| `routine_id` + `connector_id` (composite PK) + `tenant_id`          | uuid                                           |
| `mode` (`inherit` / `read_only` / `custom`) + `custom_scope text[]` | `custom_scope` valid only when `mode='custom'` |

**`routine_fires`** — append-only fire history (lightweight; the heavy run record lives in ai-backend).

| Column                                            | Type / Notes                                                              |
| ------------------------------------------------- | ------------------------------------------------------------------------- |
| `id` (RoutineFireId) / `tenant_id` / `routine_id` | `routine_id` FK ON DELETE CASCADE                                         |
| `trigger_id` / `trigger_kind`                     | `trigger_id` LOOSE FK (no cascade — old fires survive a retrigger delete) |
| `run_id`                                          | LOOSE FK to ai-backend run record (cross-service; not DB-enforced)        |
| `status` / `skip_reason`                          | status ∈ {queued, running, succeeded, failed, skipped}                    |
| `payload_snapshot` / `source_ip`                  | jsonb ≤256KB / inet (webhook only)                                        |
| `created_at` / `completed_at`                     | timestamptz                                                               |

### 5.2 Indexes

- `routines_scheduler_idx` — B-tree on `(tenant_id, status, next_fire_at ASC) WHERE deleted_at IS NULL AND status='active'` — **the scheduler poll index** (per dispatch brief §5 spec).
- `routines_owner_idx` — B-tree on `(tenant_id, owner_user_id, status, created_at DESC)` — list view default sort.
- `routines_project_idx` — B-tree on `(tenant_id, project_id) WHERE project_id IS NOT NULL` — project-scoped reads.
- `routines_search_idx` — GIN on `to_tsvector('simple', name || ' ' || description) WHERE deleted_at IS NULL` — search.
- `routine_triggers_routine_idx` — B-tree on `(routine_id)`.
- `routine_triggers_webhook_idx` — UNIQUE on `(id) WHERE kind='webhook'` — webhook URLs use `trigger_id`.
- `routine_fires_routine_time_idx` — B-tree on `(routine_id, created_at DESC)`.
- `routine_fires_tenant_status_time_idx` — B-tree on `(tenant_id, status, created_at DESC)` — admin "what fired recently" queries.

### 5.3 Retention (per master §3.3)

- **Routine definitions**: indefinite while `status != deleted_at`. Soft-deleted retained 90 days then hard-deleted by the same backend retention cron used by Inbox (extend `inbox_retention.py` to also sweep `routines`, OR add `routines_retention.py` — implementation chooses; the contract is daily cleanup).
- **Triggers**: cascade with parent.
- **Fires**: retained 365 days from `created_at`, then hard-deleted. Failed-fire payload snapshots are useful for forensics; 365d is the same window the rest of the audit envelope uses.
- **Webhook secrets in TokenVault**: rotated secrets are retained `secret_grace_until + 30 days` (the +30 lets admins forensic-investigate a leaked secret), then purged.
- **Audit rows**: 365d per master rule; cascade-on-delete never applies (audit append-only).

### 5.4 Cleanup job

Daily backend cron extends the existing retention pattern. Tasks:

1. Hard-delete soft-deleted routines past 90 days.
2. Hard-delete fires past 365 days.
3. Purge expired rotated secrets from TokenVault.
4. Emit `routine.retention_cleanup_run` audit summary per-tenant.

---

## §6 Audit (per master §3.2 + cross-audit §1.4 `context` field)

Every state-changing operation writes an audit row through `packages/audit-chain`. The audit row's `context` field carries trigger / cron / source_ip / auth_method.

### 6.1 Action taxonomy

| Action                                  | Trigger                                              | `context` (cross-audit §1.4)                                                                                                            |
| --------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `routine.created`                       | `POST /v1/routines` succeeds                         | `{ trigger_kinds }`                                                                                                                     |
| `routine.updated`                       | `PATCH /v1/routines/{id}`                            | `{ changed_fields }`; `before_state` + `after_state` populated                                                                          |
| `routine.activated` / `paused`          | activate / pause endpoints                           | `{ from_status, to_status, reason? }`                                                                                                   |
| `routine.auto_paused`                   | scheduler emits via internal route                   | `{ reason: "permission_shrinkage" \| "owner_offboarded" \| "critical_connector_disconnected" \| "base_agent_deleted", missing: [...] }` |
| `routine.deleted`                       | `DELETE`                                             | `{ soft: true }`                                                                                                                        |
| `routine.trigger_added/updated/deleted` | trigger CRUD                                         | `{ trigger_id, kind, changed_fields? }`                                                                                                 |
| `routine.secret_rotated`                | rotate-secret endpoint                               | `{ trigger_id, grace_until }`                                                                                                           |
| `routine.fire_scheduled`                | scheduler claims + invokes                           | `{ trigger_id, cron, next_after }`                                                                                                      |
| `routine.fire_manual`                   | manual fire                                          | `{ trigger_id, run_id }`                                                                                                                |
| `routine.fire_webhook`                  | webhook hit succeeds                                 | `{ trigger_id, source_ip, auth_method: "secret" }`                                                                                      |
| `routine.fire_webhook_unauthorized`     | bad/missing secret OR IP not in allowlist            | `{ trigger_id, source_ip, auth_method, reason: "bad_secret" \| "ip_not_allowed" \| "missing_secret" }`                                  |
| `routine.fire_event`                    | event subscriber matches + fires                     | `{ trigger_id, event_source, event_ref: ItemRef }`                                                                                      |
| `routine.fire_skipped`                  | scheduler skips (missed_fire_policy / quota / pause) | `{ trigger_id, reason }`                                                                                                                |
| `routine.output_target_dead_link`       | fire's `output_target` ref no longer resolves        | `{ trigger_id, output_target_kind, fallback: "inbox" }`                                                                                 |
| `routine.run_completed`                 | ai-backend completion callback                       | `{ trigger_id, run_id, status, duration_ms }`                                                                                           |
| `routine.retention_cleanup_run`         | cron daily                                           | `{ deleted_count, fires_deleted_count, secrets_purged }`                                                                                |

**Critical:** webhook auth failures are audited just like successes (`routine.fire_webhook_unauthorized`). Cross-audit §2.4 is binding: every webhook hit, success or failed-auth, leaves a forensics trail.

### 6.2 What is NOT audited

- List queries (`GET /v1/routines`) — auditing every list scrape would dwarf signal with noise.
- SSE connections themselves (audit the published envelope, not each fan-out).
- Reads of an individual routine (`GET /v1/routines/{id}`) — definitions are non-sensitive metadata. Webhook secrets, even masked, are NOT included in this query's response (separate `reveal-secret` endpoint exists for owner-only single-use reveal).

Audit rows are append-only (audit-chain enforces). Audit is exportable via the existing SIEM export path.

---

## §7 Authorization

### 7.1 Visibility rules (read)

Per cross-audit §1.3 (project-scoped access):

- A `Routine` is visible when:
  - `tenant_id` matches the verified bearer's tenant claim, **and**
  - `owner_user_id` matches the verified bearer's user_id, **OR**
  - `project_id IS NOT NULL` AND the bearer is a member of the project, **OR**
  - the bearer has the `compliance_reader` role (tenant admin with audit-read scope; the read itself is audited).
- Non-readers see `404` (existence-not-leaked default per cross-audit §1.3).

### 7.2 Mutation rules (write)

| Action                        | Required role                                                 |
| ----------------------------- | ------------------------------------------------------------- |
| Create routine                | Any tenant member.                                            |
| PATCH / DELETE routine        | `owner_user_id` only. Project members CANNOT mutate.          |
| Add / update / delete trigger | `owner_user_id` only.                                         |
| Rotate webhook secret         | `owner_user_id` only. Single-use reveal returned.             |
| Activate / pause              | `owner_user_id` only.                                         |
| Manual fire ("Run now")       | Per `routine.permissions.manual_fire` (default owner; §3.11). |

Admins cannot edit another user's routine (read-only compliance scope). A future "admin force-pause for departed user" workflow is a separate audited operation (§16 Q11).

### 7.3 Webhook authorization (per cross-audit §2.4)

- **Rotating secret** in `X-Atlas-Routine-Secret` header. Secrets stored encrypted in `TokenVault` (referenced from `routine_triggers.secret_ref`). Rotation is owner-initiated. **7-day grace window**: during the grace, both the new and the old secret validate. After grace, only the new.
- **Optional IP allowlist** per trigger (`routine_triggers.config.ip_allowlist`). Empty = no IP restriction. CIDR list; server validates both client-supplied (on save) and incoming-request IPs.
- **mTLS deferred** to Wave 5+ (cross-audit §2.4 explicit).
- **Constant-time secret compare** mandatory (no early-return on prefix mismatch).
- **No bearer / no session auth on the webhook endpoint** — the secret IS the auth. This is why the webhook router lives under a separate facade module (§2 source-of-truth map).

### 7.4 Permission intersection at fire time (the hard rule)

Repeated from §3.10 because it is load-bearing:

```
At fire time, before invoking the run:
  effective = INTERSECT(routine.permissions, owner.current, project.current if filed)
  if effective does not satisfy routine.permissions:
    1. Do NOT fire.
    2. routine.status := "paused", pause_reason := "permission_shrinkage"
    3. Inbox item (kind=error, priority=high) → owner with re-authorize CTA
    4. Audit row: routine.auto_paused with context={reason, missing:[...]}
```

Same flow applies when:

- **Owner offboarded** (owner's user record marked `disabled_at`): `pause_reason="owner_offboarded"`. Tenant admin can reassign owner (separate Wave-6 endpoint; out of scope here).
- **Critical connector disconnected** (a connector the routine `requires` is now `status="disconnected"` for the owner): `pause_reason="critical_connector_disconnected"`. The Inbox item links to the connector destination's repair flow (mirroring Inbox §13.1 reply-to-error pattern).

Routine permissions auto-edit-down is **explicitly not** the policy (see §16 Q5 for product to confirm). Reasons: opaque change to the routine's behavior; surprise to the owner; harder to audit. The trade-off the user pays is a one-step pause + re-auth.

### 7.5 Sensitive-field handling

- **Webhook secrets**: stored encrypted in TokenVault. Returned cleartext exactly once (rotate response). Subsequent reads return masked form (last 4 chars).
- **Instructions field**: treated as **sensitive** in telemetry / audit (never logged; `before_state.instructions` and `after_state.instructions` in audit are stored as content-hash + length, not raw content — admins with compliance scope can reveal-via-audit-export per the existing SIEM pipeline). Search (`?q=...`) intentionally does NOT cover `instructions` to keep the field out of any indexable surface beyond direct GET.
- **Environment vars (`environment` field)**: same treatment as secrets — masked on read; cleartext only on the editor save round-trip with HTTPS + service-token bracket.

---

## §8 Pagination + search (per master §3.5 + cross-audit §1.5)

- **Cursor pagination.** `?after=<opaque-cursor>&limit=<n>`. Default `limit=50`, max `limit=200`. Cursor encodes `(sort_field, id)` for stable scrolling under inserts.
- **Multi-value filter axis = OR within axis; AND across axes** per cross-audit §1.5.
- **Search.** `?q=<query>` runs PostgreSQL `plainto_tsquery('simple', q)` against `name || ' ' || description` via the GIN index. Instructions are NOT searched (§7.5).
- **Sort allowlist** per §4.5.

Combined example:

```
GET /v1/routines?filter[status]=active&filter[status]=paused&filter[trigger_kind]=schedule&q=briefing&sort=next_fire_at:asc&limit=50
```

---

## §9 Accessibility (per master §3.6)

- **Cron editor** — human-readable mode is the default (form fields, native time picker, multi-select for days-of-week). Advanced mode is opt-in toggle with live human preview. Every change updates a `aria-live=polite` preview region ("Will run weekdays at 18:00 GMT+5:30; next 3 fires: Mon 18:00, Tue 18:00, Wed 18:00").
- **Editor tabs** — ARIA tabs pattern (`role="tablist"` / `role="tab"` / `role="tabpanel"`). Arrow keys cycle (left/right wrap), Home/End jump, Enter activates. Tab-validation badges are described in `aria-describedby`.
- **List rows** — each row is one tab stop. Enter opens detail. The hover/focus actions (Run now / Pause / Edit) reveal on focus, not only hover.
- **Live region** — when on Routines destination, polite `aria-live` announces:
  - "Routine activated" / "Routine paused" / "Routine errored" on status change.
  - "Routine fired: <name>" on a `routine_fired` SSE envelope (throttled to one announcement per 3s).
- **Color is never the sole carrier** — status pill combines color + icon + text. Errored routines have a red border AND an alert icon AND the word "errored".
- **Reduced motion** — fire-arrival pulse animation respects `prefers-reduced-motion`.
- **Webhook secret reveal modal** — labels, copy-button accessible name "Copy webhook secret", warning text reads "Secret will not be shown again". Focus trap; Esc closes.

---

## §10 Performance (per master §3.7)

- **LCP < 2.5s** — list endpoint returns 50-row first page with denormalized owner name + trigger-kind chips; no waterfall.
- **INP < 200ms** — filter tab clicks operate on already-fetched first page; mark-pause / mark-activate optimistic with rollback.
- **Virtualized list** when count > 100 (reuse `@tanstack/react-virtual` introduced by Inbox/Todos).
- **Scheduler poll cadence** — default `ROUTINE_SCHEDULER_INTERVAL_SECONDS=60` (1 minute, the platform granularity floor). Configurable down to 15s for low-latency tenants. Each tick scans only `WHERE status='active' AND next_fire_at <= now()` (index covers).
- **Claim semantics** — `FOR UPDATE SKIP LOCKED` with `CLAIM_TTL_SECONDS=120` (twice the poll cadence). Worker crash → claim expires → next worker re-claims; never double-fire.
- **Webhook ingest budget** — facade-side validate-and-forward in ≤ 50ms p50; backend-side trigger-lookup-and-insert ≤ 50ms p50. End-to-end target p99 ≤ 500ms (network excluded). Concurrent webhook firing for the same trigger is rate-limited at the facade (Wave 5 default: 60 hits/min/trigger; configurable per tenant).
- **SSE keepalive** — `:keepalive` comment every 25s. Client tolerates 60s silence before reconnect.
- **Shell render isolation** — navigating to Routines does not re-mount `ChatShell`; only the destination remounts.

---

## §11 Telemetry (per master §3.8)

OpenTelemetry spans (no PII; only ids + enum values):

```
destination=routines
  action=list_open
  action=detail_open               routine_id=<id>
  action=editor_open               mode=<new|edit>
  action=editor_save               result=<ok|validation_error>
  action=filter_change             value=<slug>
  action=search                    q_len=<n>
  action=activate                  routine_id=<id>
  action=pause                     routine_id=<id>
  action=manual_fire               routine_id=<id>
  action=secret_rotate             trigger_id=<id>
  action=trigger_add               kind=<schedule|webhook|event|manual>
  action=trigger_delete            trigger_id=<id>
  action=sse_reconnect             after_sequence=<n>
  action=sse_failover_to_poll
```

Backend emits structured logs with `request_id` correlation (per cross-audit §5.1 — OTel trace_id, facade injects). Error logs include `tenant_id`, route, error code (never user data; never the `instructions` field).

Scheduler emits per-tick metrics: `routine_scheduler_ticks_total`, `routine_fires_initiated_total{trigger_kind=...}`, `routine_fires_failed_total{reason=...}`, `routine_scheduler_tick_duration_seconds`, `routine_permission_intersection_failures_total`.

---

## §12 States (per master §3.10)

| State                        | Renders                                                                                                                                                                                                               |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **List loading**             | Skeleton: PageHeader visible, FilterTabs visible (no counts), 6 skeleton rows.                                                                                                                                        |
| **List empty (any filter)**  | `<EmptyState icon="clock" title="No routines yet" sub="Routines run on a schedule, webhook, event, or manual fire — without you starting a chat each time." action={{ label: "New routine", onClick: createNew }} />` |
| **List empty (filter)**      | filter-specific copy: paused → "No paused routines"; errored → "No errored routines — all clean"; draft → "No drafts saved".                                                                                          |
| **Filter-empty + search**    | "No routines match \"{q}\" in {filter}." with "Clear filters" button.                                                                                                                                                 |
| **Detail loading**           | Skeleton matching detail shape (header / triggers / config / history / audit sections).                                                                                                                               |
| **Editor saving**            | Save button shows spinner; form disabled; on 200 → toast "Routine saved" + nav back to detail; on error → toast + error highlighted on offending tab with badge.                                                      |
| **Editor activation failed** | Banner at top of editor: "Activation failed: {server-reason}. Routine saved as draft." Tab badges highlight the failing field.                                                                                        |
| **Paused (any reason)**      | Detail header shows pause-pill + reason ("Auto-paused: connector disconnected") with the "Re-authorize and resume" CTA when applicable.                                                                               |
| **Errored**                  | Detail header shows error-pill + last error message + last-attempt timestamp. CTA: "Investigate" → opens last failed run.                                                                                             |
| **Webhook secret modal**     | Modal with one-time secret, copy button, "I have stored this secret" confirmation required to close.                                                                                                                  |
| **Offline**                  | Banner: "You're offline — showing cached routines. New fires will resume when you reconnect." Reads from `KeyValueStore` cache.                                                                                       |
| **Stale**                    | If last-fetch > 5 min AND SSE disconnected: top hint "Routine list may be out of date. Refresh." with refresh button.                                                                                                 |

---

## §13 Cross-destination references (per master §3.11 + cross-audit §1.1, §3.3, §5.3)

Routines cross-references (typed, via `<ItemLink>` registry):

| Field                           | Target                                               | UI affordance                                                  |
| ------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------- |
| `behavior.output_target.ref`    | inbox / library_page / existing_chat / project_log   | rendered via `<ItemLink>`; opens target on click.              |
| `connectors[*].connector_id`    | connectors destination                               | per-row icon + name; click → connector detail.                 |
| `permissions.allowed_tools[*]`  | tools destination                                    | chip with `<ItemLink kind="tool">`.                            |
| `permissions.allowed_skills[*]` | tools destination (skills tab)                       | chip with `<ItemLink kind="skill">`.                           |
| `base_agent_id`                 | agents destination                                   | chip in detail header; opens agent.                            |
| `project_id`                    | projects destination                                 | chip in detail header.                                         |
| run history rows (`run_ref`)    | ai-backend run records (via existing chats run pill) | clickable; opens originating run / chat.                       |
| Auto-pause Inbox item           | inbox destination                                    | the produced item carries `<ItemRef kind="routine" id={...}>`. |

### 13.1 Cascade rules (per cross-audit §5.3 default — cross-destination = dead link, audit = never cascade)

| Origin deletion                            | Routines effect                                                                                                                       |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Owner offboarded                           | Auto-pause (`pause_reason="owner_offboarded"`); Inbox item to tenant admin "Routine X needs new owner". Admin reassignment is Wave 6. |
| Critical connector disconnected (owner)    | Auto-pause (`pause_reason="critical_connector_disconnected"`); Inbox item to owner with re-authorize CTA.                             |
| Tool revoked / scope shrunk                | Auto-pause (`pause_reason="permission_shrinkage"`); Inbox item to owner.                                                              |
| Project deleted (routine has `project_id`) | `project_id` becomes a dead reference; "deleted project" pill; routine NOT auto-paused; filing falls back to unfiled.                 |
| Base Agent deleted                         | Auto-pause (`pause_reason="base_agent_deleted"`); owner picks new Agent or converts to standalone.                                    |
| Output target deleted                      | Per §3.9 — `routine.output_target_dead_link` audit; falls back to Inbox; NOT auto-paused.                                             |
| Tenant deleted                             | Hard cascade (master rule).                                                                                                           |

Library dataset / file referenced in `instructions` is NOT a typed reference at fire time (instructions are free text); no auto-detection — owner sees a runtime error.

---

## §14 Desktop substrate caveats (per master §3.12 + cross-audit §1.2)

- **Routines edit on desktop is identical to web.** No desktop-specific editor.
- **Webhook URL copy** — uses `ClipboardPort` (cross-audit §1.2). Web default: `navigator.clipboard.writeText`. Desktop: native `clipboard.writeText` via main process.
- **One-time secret reveal** — modal uses `ClipboardPort` for the copy-button; the secret is never written to anything outside the React modal state (no clipboard caching, no localStorage). Modal text reminds user "secret will not be shown again".
- **Native notification on routine completion / failure** — fires through `NotificationPort` (cross-audit §1.2). Title `Routine succeeded: <name>` / `Routine errored: <name>`; body excluded for privacy. Web default: no-op when permission ungranted; desktop: native OS notification with click → `router.navigate(<RoutineId>)`.
- **Deep-link routing** — desktop registers `atlas://routines/<id>` and `atlas://routines/<id>/edit` as URL handlers. Frontend `HashRouter` and desktop main process resolve to the same `route.id`/`route.view` shape.
- **No direct browser API access from any routines component** — substrate-agnostic. Clipboard, notifications, deep-link routing all go through ports.

---

## §15 Implementation phasing (per implementation-plan.md §2 Phase 5 + §4 merge order)

Per master §7, this destination uses a 2-agent pattern (impl-plan does not budget a desktop-only agent for Phase 5 — desktop wiring is folded into ports the host already supplies).

### 15.1 Agent boundaries (no overlap with shared files)

**P5-A backend — `worktree-agent-phase5-routines-backend`**

Prereqs: SP-1 (`brands.ts` for `RoutineId`/`TriggerId`/`RoutineFireId`, `refs.ts` for `ItemRef`), P1-A (`approvals.ts` referenced via output-target=chat), P4-A (`api-types/index.ts` + `backend/app.py` + `facade/app.py` shared lines — must rebase).

Exclusive files:

- `packages/api-types/src/routines.ts` (NEW); append one re-export line to `packages/api-types/src/index.ts` (rebase after P4-A)
- `services/backend/src/backend_app/routines/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `webhook_secrets.py`, `events.py` (SSE bus), `permission_intersector.py`
- Alembic migration for `routines`, `routine_triggers`, `routine_connector_configs`, `routine_fires` + indexes
- `services/backend/src/backend_app/jobs/routines_retention.py` (NEW)
- `services/backend/src/backend_app/app.py` — append `include_router(routines_router)` + internal-router lines (merge after P4-A)
- `services/backend-facade/src/backend_facade/routines_routes.py` (NEW), `routines_webhook_routes.py` (NEW); append to `facade/app.py`
- `services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py` (NEW) — mirrors `retention_sweeper.py` (§3.7)
- `services/ai-backend/src/runtime_worker/__main__.py` — single-line registration alongside `RetentionSweeperLoop`
- `services/ai-backend/src/agent_runtime/api/run_coordinator.py` — extend run-source tagging without breaking interactive runs
- All tests per §17.1

Deliverables: routine CRUD, scheduler worker, webhook ingest + rotation + 7d grace + IP allowlist, permission intersection enforcement, audit hooks, retention cron.

**P5-B chat-surface + frontend — `worktree-agent-phase5-routines-surface`**

Prereqs: SP-1 (`<PageHeader>`, `<StatusPill>`, `<FilterTabs>`, `<EmptyState>`, `<ItemLink>`, `BadgePort`, `NotificationPort`, `ClipboardPort`, `formatRelativeTime`), P5-A (wire contracts).

Exclusive files:

- `packages/chat-surface/src/shell/destinations.ts` — extend `ShellDestinationSlug` to add `"routines"` as the 12th slug + extend `SHELL_DESTINATIONS` array (only Phase-5 touch to this file per impl-plan §6)
- `packages/chat-surface/src/destinations/routines/` (NEW): `RoutinesDestination.tsx`, `RoutinesPanel.tsx`, `RoutineDetail.tsx`, `RoutineEditor.tsx`, `tabs/{TriggersTab,ConnectorsTab,BehaviorTab,PermissionsTab}.tsx`, `cron/{CronEditor.tsx,cronHumanize.ts}`, `secret-reveal-modal.tsx`, `index.ts` (registers `<ItemLink kind="routine">`)
- `packages/chat-surface/src/index.ts` — append Routines re-export
- `apps/frontend/src/api/routines.ts` (NEW) — HTTP wrappers + SSE
- `apps/frontend/src/app/App.tsx` — extend destination dispatch switch + ContextPanel slot (merge after P4-B)
- All tests per §17.2

Deliverables: Routines UI; cron editor (human-readable + advanced); editor tabs; trigger management; webhook UI; `BadgePort` errored count.

### 15.2 Merge order (strict, per implementation-plan.md §4)

1. P1-A (approvals contracts) → main _(prerequisite already merged)_
2. P4-A (inbox backend) → main _(prerequisite already merged)_
3. P4-B (inbox surface) → main _(prerequisite already merged)_
4. **P5-A** → main. Rebases `api-types/index.ts`, `backend/app.py`, `facade/app.py` on top of P4-A.
5. **P5-B** → main. Rebases `App.tsx` on top of P4-B and `destinations.ts` on top of any in-flight wave-2 destination work (currently the only file-owner of the 12th-slug extension).

### 15.3 Acceptance criteria (gate to closing Phase 5)

- ✅ Every endpoint in §4.2, §4.3, §4.4 implemented and tested.
- ✅ Audit rows emitted for every action in §6.1; verified in audit-chain export.
- ✅ Tenant + owner isolation tests pass (cross-tenant: 404; cross-user-non-member: 404; project-member-read: 200 read / 403 write).
- ✅ Scheduler claim test: 10 concurrent fake workers + 100 due routines → no double-fire; every routine fires exactly once.
- ✅ Permission intersection test: routine requires tool X; owner loses tool X; next scheduler tick → routine.status=paused, Inbox item created, audit row written, no run created.
- ✅ Webhook secret rotation + 7d grace test: old secret valid for 7d, then 401.
- ✅ Webhook auth-failure audit test: bad-secret hit produces `routine.fire_webhook_unauthorized` audit row.
- ✅ Retention cron promotes routines past 90d soft-delete to hard-delete; audit summary written.
- ✅ axe-core green on `RoutinesDestination + RoutinesPanel + RoutineDetail + RoutineEditor` in default + high-contrast themes.
- ✅ SSE reconnect resumes from `?after_sequence=N` without dropping envelopes.
- ✅ Frontend typecheck + chat-surface tests + backend tests + ai-backend tests green; no `any` introduced in `routines.ts`.

---

## §16 Open questions for product (parth)

These need a call before P5-A / P5-B code the affected branch.

1. **Code routines (repository + environment).** The screenshot's optional repo + env implies a code-routine track (executor + sandboxing infra). **Recommend deferring to Wave 6** — the wire shape lands in this PRD so we don't redesign later, but P5-A behind feature flag `ROUTINE_CODE_EXEC_ENABLED=false` treats the fields as opaque storage. Confirm.
2. **Manual run-now ACL.** §3.11 default: owner-only; override `permissions.manual_fire = "owner" | "project_members" | "tenant"`. Confirm.
3. **Output target = library page mode.** Recommend **`new_per_fire` with date-stamp** default ("Daily briefing — 2026-05-17"); `update_same` opt-in. Confirm.
4. **Permission shrinkage policy.** Default: auto-pause + Inbox + manual resume (vs auto-edit-down). Recommend auto-pause for security + transparency. Confirm.
5. **Auto-resume on permission restoration.** Default: **no auto-resume** (avoids surprise fires post vacation re-auth). Confirm.
6. **Webhook security beyond rotating secret + IP allowlist.** Recommend deferring mTLS + HMAC-of-payload to Wave 5+ per cross-audit §2.4. HMAC-signature is the next-best add. Confirm.
7. **Missed-fire policy default.** §3.7 default: `fire_once`. Per-routine override is in the wire either way. Confirm default.
8. **Routine quotas per tenant.** Recommend initial soft caps: 100 active routines/tenant, 500 manual-fires/day/user, 100k webhook-fires/day/tenant. Confirm.
9. **Atlas-proposed cron suggestions** when the user types instructions ("This looks like daily work — run at 6pm?"). Recommend **out of scope** for Wave 5; Wave 6. Confirm.
10. **Auto-extracted/proposed routines.** "Make this a routine?" CTA after a repeated interactive run. Wave 6+. Confirm.
11. **Snapshot vs live agent reference.** §1.4 default: live re-resolve at fire time. Recommend live + explicit `agent_version_pin` field for users who want pinned. Confirm.
12. **Admin force-reassign owner / force-pause.** Out of scope Phase 5 (Wave 6). For Phase 5: offboarded owner ⇒ admin Inbox item, no admin-edit. Confirm.
13. **Routine forking / templates.** Cross-audit §3.5 lists as Wave 5+. Confirm.
14. **Notification preferences (tenant + per-user defaults).** Per-routine controls are in the wire (§3.9). Settings UI for tenant/user defaults punted to Wave 6. Confirm.

---

## §17 Test plan

### 17.1 Backend / facade unit + integration (P5-A)

- Tenant isolation: cross-tenant GET/PATCH/DELETE → 404.
- Owner-only writes: project member reads OK; PATCH → 403; non-member → 404.
- Project-scoped read: project member can GET routine when `project_id` matches one of user's projects.
- Compliance read: tenant admin with `compliance_reader` can GET; audit row written.
- **Scheduler claim correctness**: 10 concurrent fake workers + 100 due routines → each routine fires exactly once (no doubles, no skips); pattern verified at `FOR UPDATE SKIP LOCKED` + `CLAIM_TTL_SECONDS=120`.
- Claim expiry: simulated worker crash mid-fire → after 120s, next worker re-claims; audit row notes recovery.
- **Permission intersection at fire time**: routine requires tool X; revoke X; scheduler tick → routine paused, Inbox item created (via existing Inbox producer P4-A), audit `routine.auto_paused` with `context.missing=["tool:X"]`, no run created. Happy-path: tool still present → fires normally.
- Webhook secret validation: valid → 200; bad → 401; missing → 400; compare is timing-independent.
- Webhook secret rotation + 7d grace: both new + old valid during grace; day-8 old secret → 401 + `routine.fire_webhook_unauthorized` audit.
- Webhook IP allowlist: CIDR match → 200; non-match → 403 + audit.
- Webhook payload size: 256KB → 200; +1 byte → 413.
- Cron validation: `@reboot` / sub-minute / nested invalid → 422; valid Unix cron → 200 with `croniter`-derived next-fire-times.
- Missed-fire policy: pause 3 days, activate; `fire_once` → 1 fire + 2 skip audits; `fire_all` → 3 fires; `skip_all` → 0 fires + 3 skips.
- Connector disconnect auto-pause: disconnect a required connector → auto-pause + Inbox.
- Manual fire ACL: owner OK; project member OK iff `manual_fire="project_members"`; tenant member OK iff `manual_fire="tenant"`; else 403.
- Manual-fire rate limit: 61 calls/hour from one user → last call 429.
- Audit immutability: UPDATE on audit row → audit-chain raises.
- Retention cleanup: insert past-window routines/fires → cron hard-deletes; summary audit row written.
- SSE delivery + reconnect (`?after_sequence=N`) replays envelopes; tenant + owner ACL holds on the stream.
- Run-source tagging: routine fire → run record carries `source.kind="routine"`, `source.routine_id`, `source.trigger_kind`. Existing interactive runs unchanged.

### 17.2 Frontend unit + integration (P5-B)

- Editor validation: empty name → save disabled; zero triggers + status=active → activation blocked with tab badge.
- Cron editor: human-readable ↔ advanced round-trip preserves expression; tz preview updates on tz change; user's tz is the default.
- Editor tab arrow-key navigation (axe + RTL): Left/Right cycle, Home/End jump, Enter activates; validation badges in `aria-describedby`.
- Webhook URL copy invokes `ClipboardPort.copyText` mock; toast confirms.
- Secret-reveal modal: blocks close until checkbox; only one reveal per rotation.
- Manual fire: owner Run-now → POST to `/runs` → nav.
- Pause/Activate optimistic: pill flips instantly; rollback on 5xx.
- SSE reconnect: 3 server events while disconnected → all applied after reconnect.
- Filter combinations: every pairwise of `filter[status]` (multi-value OR) × `filter[trigger_kind]` × `q`.
- axe-core green on RoutinesDestination + RoutinesPanel + RoutineDetail + RoutineEditor in default + high-contrast themes.
- `<ItemLink kind="routine">` resolves to detail route; deleted routine → `{route: null}` per cross-audit §5.3.

### 17.3 End-to-end smoke (added to `docs/dev-testing.md`)

```bash
export TOKEN=$(make dev-bearer)
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://127.0.0.1:8200/v1/routines -d '{...}'              # create
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/routines   # list
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/routines/<id>/runs  # manual fire
curl -X POST -H 'X-Atlas-Routine-Secret: <s>' http://127.0.0.1:8200/v1/webhook/routines/<trigger_id> -d '{}'
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/routines/stream
```

---

## §18 Anti-goals

Restated as testable invariants:

- ❌ **NOT a workflow builder.** No multi-step DAGs. A Routine is a single agent invocation per fire; orchestration emerges from instructions. Cross-audit §3.5: Wave 5+ or never.
- ❌ **NOT a cron clone.** No `@reboot`, no per-second triggers, no nested ranges resolving to sub-minute. 1-minute granularity floor.
- ❌ **NOT a feed reader.** Event triggers are workspace-internal only (§3.6.2 allowlist).
- ❌ **NO third-party scheduler / no Zapier / Pipedream / n8n integration.** Cross-audit §3.5: connector concern, not core.
- ❌ **NO cross-tenant sharing** of routines. Security boundary.
- ❌ **NO routine forking / templates** in Phase 5. Wave 5+.
- ❌ **NO snapshot of agent at save time** by default — live re-resolve at fire (§1.4). Pin field deferred (§16 Q11).
- ❌ **NO frontend-only ACL.** Every §7 check is server-validated.
- ❌ **NO PII in telemetry or logs.** Instructions, payloads, environment vars never logged (§11 + §7.5).
- ❌ **NO direct browser API access** — clipboard, notifications, deep-links go through ports (§14).
- ❌ **NO bearer auth on the webhook endpoint.** Rotating secret IS the auth (§7.3); separate facade router enforces.
- ❌ **NO double-fire on scheduler crash.** `FOR UPDATE SKIP LOCKED` + `CLAIM_TTL_SECONDS=120` guarantees at-most-once delivery per fire window.

---

## §19 References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas (the foundation).
- [destinations-master-prd.md](../destinations-master-prd.md) — §3 (enterprise checklist), §4 (shared primitives), §7 (dispatch pattern). Routines is the 12th destination, added by this sub-PRD.
- [cross-audit.md](../cross-audit.md) — binding decisions consumed: §1.1 ItemRef, §1.2 ports, §1.3 project ACL, §1.4 audit context, §1.5 filter OR, §1.6 PageHeader, §2.1 branded IDs incl. `RoutineId`, §2.3 SectionResult, §2.4 webhook security, §3.3 ItemLink registry, §3.4 formatRelativeTime, §3.5 deferred-features, §4 shared-primitives prereq, §5.1 request_id, §5.2 SSE, §5.3 cascade default, §5.4 port injection.
- [implementation-plan.md](../implementation-plan.md) — §2 Phase 5 row (P5-A / P5-B file boundaries), §4 strict merge order, §6 anti-conflict file rules.
- [destinations/inbox-prd.md](inbox-prd.md) — template; matches §1–§19 structure. Inbox is the producer for "routine auto-paused" / "routine errored" items; Phase 5 reuses the Inbox producer pipeline (no new writer).
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — `source.kind` carrier on run records extended in P5-A; chats run-pill surfaces routine runs.
- [destinations/todos-prd.md](todos-prd.md) — `TodoSource.kind="agent"` analog to a routine fire's source attribution.
- `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py` — template for `routine_scheduler.py` (§3.7); same per-tenant claim-loop pattern.
- `services/ai-backend/src/agent_runtime/api/run_coordinator.py` — extended in P5-A to accept `source.kind="routine"`.
- `services/backend/src/backend_app/token_vault.py` — webhook secret storage (existing).
- `packages/audit-chain` — audit row writer (existing; cross-audit §1.4 `context` field consumed here).
- Root [`CLAUDE.md`](../../../CLAUDE.md) — compliance section (audit immutability, retention scope, tenant isolation, untrusted-input rules).
- [`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) · [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md) · [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md) · [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md).
