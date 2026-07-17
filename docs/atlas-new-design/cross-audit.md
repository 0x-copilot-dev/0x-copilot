# Atlas Destinations — Cross-PRD Audit & Binding Decisions

**Status:** binding (2026-05-17)
**Owner:** parth (orchestrator)
**Companion docs:** [PRD.md](PRD.md) · [destinations-master-prd.md](destinations-master-prd.md) · [destinations/inbox-prd.md](destinations/inbox-prd.md) · [destinations/home-prd.md](destinations/home-prd.md) · [destinations/todos-prd.md](destinations/todos-prd.md) · [destinations/chats-canvas-prd.md](destinations/chats-canvas-prd.md)

---

## 0. Purpose

The five Phase-1-to-4 sub-PRDs were written in parallel by independent agents. A cross-PRD read (separate audit agent) surfaced drift on wire contracts, endpoint shapes, ports, audit, authz, shared primitives, and terminology. This document records the **binding orchestrator decisions** that resolve each drift. Every implementation agent reads this as input before writing code.

**Rule:** when a sub-PRD and this audit conflict, this audit wins. The sub-PRDs are not re-edited (they're the design record at write time); the binding decisions are layered on top via this doc and the master PRDs.

---

## 1. Critical drifts (blocking — resolved here)

### 1.1 Cross-destination reference type — one shape

**Drift:** Home.activity uses discriminated-union `ItemRef`; Todos.source uses custom `TodoSource`; Inbox uses raw `thread_id` / `run_id` / `project_id` strings plus a denormalized `InboxSender` carrying display data.

**Binding decision:**

There is ONE cross-destination reference type, defined in `packages/api-types/src/refs.ts`:

```typescript
export type ItemKind =
  | "chat"
  | "run"
  | "subagent"
  | "tool_result"
  | "todo"
  | "inbox_item"
  | "project"
  | "library_file"
  | "library_page"
  | "library_dataset"
  | "agent"
  | "tool"
  | "skill"
  | "connector"
  | "person" // workspace user
  | "memory"
  | "routine"
  | "approval"
  | "meeting_external"; // calendar event from a connector

export type ItemRef =
  | { readonly kind: "chat"; readonly id: ConversationId }
  | { readonly kind: "run"; readonly id: RunId }
  // … etc, one branch per kind
  | { readonly kind: "approval"; readonly id: ApprovalId };

/** Display-side denormalization for use in lists. NEVER trusted as source
 *  of truth — re-fetched on item open via ItemLink registry resolver. */
export interface ItemRefSnapshot {
  readonly ref: ItemRef;
  readonly display_label?: string;
  readonly display_icon_hint?: string;
}
```

**How each destination consumes it:**

- **Home.activity** — already uses `ItemRef` (Home-PRD §4.6). Unchanged.
- **Todos.source** — `TodoSource` keeps its `kind: "user" | "chat" | "agent"` discriminator (intra-destination provenance metadata is a legitimate axis), but the _link payload_ changes from raw strings to `ItemRef`. New shape:
  ```typescript
  TodoSource =
    | { kind: "user" }
    | { kind: "chat"; ref: ItemRef /* with ref.kind="chat" */; excerpt?: string }
    | { kind: "agent"; ref: ItemRef /* with ref.kind="agent" */; run_ref?: ItemRef };
  ```
- **Inbox** — `InboxItem.thread_id` / `run_id` / `approval_id` / `project_id` collapse into a single `links: ReadonlyArray<ItemRef>` field. `InboxSender` keeps display-denormalized `agent_name` / `origin`, but the link to the agent is `sender.ref: ItemRef`.
- **Chats** — citations + cross-thread mentions use `ItemRef` (replaces existing inline string refs in `citationHrefs.ts`).

**Single source of truth:** `packages/api-types/src/refs.ts`. Every `ItemKind` value, every `ItemRef` branch, every consumer reads from here. Drift is a bug.

**`<ItemLink>` registry contract** (master PRD §4.3 enforced):

- `<ItemLink ref={ref} />` is the **only** way a destination renders a cross-destination link. Direct `router.navigate(…)` from a destination card is forbidden.
- Each destination's `index.ts` registers a resolver at package-load: `registerItemRefResolver("chat", (id) => ({ label, icon, route, breadcrumb }))`.
- The registry is in `packages/chat-surface/src/refs/registry.ts` (NEW — see §3.3).

Sub-PRDs that diverge: Todos §13.1 and Inbox §13.1 are updated by this audit (no PRD-file rewrite; binding decision suffices).

### 1.2 `NotificationPort` and `BadgePort` — defined here, owned by shared-primitives agent

**Drift:** `BadgePort` defined in Todos-PRD §14.1 (one shape); `NotificationPort` referenced by Inbox-PRD §14 and Home-PRD §5.2 without a shape; `FilePickerPort` mentioned in master §2.1 without a shape.

**Binding decision:** all destination-relevant substrate ports live in `packages/chat-surface/src/ports/` (existing path). The shared-primitives prerequisite agent (see §4) ships these definitions BEFORE any destination implementation agent runs.

```typescript
// packages/chat-surface/src/ports/BadgePort.ts
export interface BadgePort {
  /** Set the numeric badge for a destination slug. count=0 clears.
   *  Web: no-op (favicon overlay deferred). Desktop: dock/tray badge. */
  setBadge(slug: ShellDestinationSlug, count: number): void;
}

// packages/chat-surface/src/ports/NotificationPort.ts
export interface NotificationPort {
  /** Show a native notification. Web: no-op when permission not granted.
   *  Desktop: OS notification with click → router.navigate(ref). */
  notify(payload: {
    readonly title: string;
    readonly body: string;
    readonly destination: ShellDestinationSlug;
    readonly ref?: ItemRef; // optional click-target
    readonly priority?: "low" | "med" | "high";
  }): void;

  /** True if the host can show native notifications (permission granted
   *  AND substrate supports it). Destinations gate UX hints on this. */
  isAvailable(): boolean;

  /** Web only: prompt user for permission. Desktop: no-op (granted at
   *  install time). Returns the new state. */
  requestPermission?(): Promise<"granted" | "denied" | "default">;
}

// packages/chat-surface/src/ports/FilePickerPort.ts
export interface FilePickerPort {
  /** Open the substrate's file picker. Web: <input type=file>; desktop:
   *  native OS dialog. Returns selected files (browser-only stream). */
  pick(options: {
    readonly multiple?: boolean;
    readonly accept?: ReadonlyArray<string>; // MIME types
  }): Promise<
    ReadonlyArray<{
      name: string;
      size: number;
      type: string;
      stream(): ReadableStream<Uint8Array>;
    }>
  >;
}

// packages/chat-surface/src/ports/ClipboardPort.ts (also needed — Routines webhook URL copy, share-link copy)
export interface ClipboardPort {
  copyText(text: string): Promise<void>;
}
```

Each port is **always present** on the host injection (web supplies a default no-op for `setBadge` / `notify` when unsupported). Destinations call without checking `if (window…)` — that's the substrate-agnostic invariant.

**Owner:** shared-primitives agent (§4 below).

### 1.3 Project-scoped access — one rule across all `project_id`-carrying resources

**Drift:** Todos §7.2 has detailed project-member-read + owner-write ACL; Inbox §7 silent on the same axis.

**Binding decision:** every resource carrying `project_id` follows the same rule:

- **Read**: owner OR project member (when `project_id IS NOT NULL`) OR tenant admin (compliance, audited).
- **Write**: owner only. Project members cannot mutate someone else's project-filed item.
- **Existence-not-leaked**: non-readers get `404`, not `403`. (Aligns Inbox to Todos's choice; cross-destination consistency wins.)

Applies to: Todos, Inbox, Library docs/pages/datasets filed under a project (Phase 6), Memory items scoped to a project (Phase 11), Routines filed under a project (Routines phase). Master PRD §3.4 amended by this decision.

### 1.4 Audit row shape — `context` is master-level optional

**Drift:** Chats-PRD §6 extends audit row with `context: { run_id, conversation_id, sequence_no? }`.

**Binding decision:** master PRD §3.2 audit row gains an optional `context: object | null` field. Allowed shapes are per-destination and free-form (JSON object). SIEM consumers select on `target_kind` and inspect `context` selectively.

```python
# packages/audit-chain conceptual shape (already exists; this is the contract)
AuditRow = {
  tenant_id, actor_user_id,
  action: str,           # dotted: "todo.create" / "approval.accept" / …
  target_kind: str,
  target_id: str,
  before_state: dict | None,
  after_state: dict | None,
  context: dict | None,   # NEW master-level field
  ts: datetime,
  request_id: str,        # see §5.3 below
}
```

### 1.5 Filter axis repeatability — multi-value OR by default

**Drift:** Todos §8 allows `?filter[kind]=a&filter[kind]=b` (OR semantics within axis); Inbox §4.4 forbids.

**Binding decision:** every list endpoint allows multi-value within an axis (OR), across axes AND'd. Inbox amended. Master PRD §3.5 amended.

```
?filter[status]=unread&filter[status]=snoozed   # OR within status
?filter[status]=unread&filter[kind]=mention     # AND across axes
```

Empty axis (no `filter[<axis>]=` query) means "all values allowed" (no filter on that axis).

### 1.6 `PageHeader` shape — defined here, owned by shared-primitives agent

**Drift:** master §4.1 says `<PageHeader>` is planned. Todos and Inbox each invented their own.

**Binding decision:** `packages/chat-surface/src/shell/PageHeader.tsx` (NEW — shared-primitives agent):

```typescript
export interface PageHeaderProps {
  readonly title: string;
  readonly subtitle?: string;
  readonly badges?: ReactNode; // pre-rendered (StatusPill, count chips)
  readonly actions?: ReactNode; // right-aligned action buttons
  readonly primaryAction?: {
    // emphasized primary CTA (e.g., "New routine")
    readonly label: string;
    readonly onClick: () => void;
    readonly disabled?: boolean;
  };
}
```

Every destination's main view starts with `<PageHeader>`. No destination renders its own header chrome.

---

## 2. Medium-severity decisions (resolved here)

### 2.1 Branded IDs — every entity, no exceptions

**Drift:** Todos brands `TodoId`/`TodoExtractionId`; Home uses plain `string` for IDs (`conversation_id`); Inbox brands `InboxItemId`/`InboxBodyRef`; Chats uses both forms.

**Binding decision:** every entity ID in `packages/api-types/` is a branded string. Single declaration in `packages/api-types/src/brands.ts`:

```typescript
export type TenantId = string & { readonly __brand: "TenantId" };
export type UserId = string & { readonly __brand: "UserId" };
export type ConversationId = string & { readonly __brand: "ConversationId" };
export type RunId = string & { readonly __brand: "RunId" };
export type SubagentId = string & { readonly __brand: "SubagentId" };
export type TodoId = string & { readonly __brand: "TodoId" };
export type TodoExtractionId = string & {
  readonly __brand: "TodoExtractionId";
};
export type InboxItemId = string & { readonly __brand: "InboxItemId" };
export type ProjectId = string & { readonly __brand: "ProjectId" };
export type LibraryFileId = string & { readonly __brand: "LibraryFileId" };
export type LibraryPageId = string & { readonly __brand: "LibraryPageId" };
export type LibraryDatasetId = string & {
  readonly __brand: "LibraryDatasetId";
};
export type AgentId = string & { readonly __brand: "AgentId" };
export type ToolId = string & { readonly __brand: "ToolId" };
export type SkillId = string & { readonly __brand: "SkillId" };
export type ConnectorId = string & { readonly __brand: "ConnectorId" };
export type MemoryItemId = string & { readonly __brand: "MemoryItemId" };
export type RoutineId = string & { readonly __brand: "RoutineId" };
export type ApprovalId = string & { readonly __brand: "ApprovalId" };
```

Existing types (e.g., `ConversationId` in `chat-surface/destinations/home`) are removed; everyone imports from `@0x-copilot/api-types`.

### 2.2 Terminology — "approval" is the noun; "approval_request" is the event

**Drift:** "approval", "approval_request", "approval_assigned" appear inconsistently.

**Binding decision:**

- **Noun:** `Approval` — the in-flight or resolved diff. Has `id: ApprovalId` and `state: "pending" | "accepted" | "rejected" | "edited"`.
- **Inbox kind:** `kind: "approval_request"` — the row in Inbox that points to a pending Approval. (Read as: "the inbox is showing an approval request to you".)
- **Audit actions:** `approval.accept`, `approval.reject`, `approval.suggest_edit`.
- **Runtime events:** `approval_requested` (system emits → fans out), `approval_resolved` (system emits when state moves).

Every PRD reads from this glossary. Add to master PRD as a glossary appendix.

### 2.3 Partial-failure section pattern — adopt as master-level

**Drift:** Home introduces `SectionResult<T> = { status: "ok" | "error" | "unavailable", data?: T, error?: string }`. Other destinations don't use it.

**Binding decision:** add `SectionResult<T>` to `packages/api-types/src/refs.ts` as a master-level wrapper. Any endpoint that aggregates upstream calls returns sections wrapped in `SectionResult` so partial-failure renders cleanly. Single-fetch endpoints don't wrap.

```typescript
export interface SectionResult<T> {
  readonly status: "ok" | "error" | "unavailable";
  readonly data?: T;
  readonly error?: string; // human-readable, frontend-displayable
  readonly retry_after_ms?: number; // when status="error", optional backoff hint
}
```

Home uses it for every section. Future aggregation endpoints (e.g., Routines list with per-routine "next-fire" health) use it. Non-aggregation endpoints (Inbox list, Todos list) don't.

### 2.4 Webhook security for Routines — rotating secret + optional IP allowlist

**Drift (preempted, not from existing PRDs):** Routines spec will need this; pin now so Routines impl agent doesn't reinvent.

**Binding decision:**

- Per-trigger rotating secret in `X-Atlas-Routine-Secret` header. Secrets stored encrypted in `TokenVault`. Rotation is owner-initiated; 7-day grace window for the old secret.
- Optional IP allowlist per trigger (CIDR list). Empty = no restriction.
- mTLS deferred (Wave 5+).

Each webhook hit is audited (success or failure-auth) with `context = { trigger_id, source_ip, auth_method }`.

---

## 3. Minor decisions

### 3.1 `SourceRef` and similar one-off types — fold into `ItemRef`

Every prior occurrence of `SourceRef`, `ItemReference`, `TargetLink`, ad-hoc `{ kind, id }` shapes — folds into `ItemRef`. Sub-PRDs amended.

### 3.2 `RecentRunStatus` and `RoutineStatus` — separate enums, justified

`RecentRunStatus = "running" | "succeeded" | "failed" | "cancelled" | "queued"` (existing in Home/api-types). Stays.

`RoutineStatus = "draft" | "active" | "paused" | "errored"` (Routines-PRD). Different domain; separate enum.

No drift fix needed. Naming convention: every `<Resource>Status` enum is local to its resource. They never share values across resources unless the semantics are identical.

### 3.3 `<ItemLink>` registry — single instance, lifecycle-clean

The registry lives at `packages/chat-surface/src/refs/registry.ts`. Public API:

```typescript
import type { ItemKind, ItemRef } from "@0x-copilot/api-types";

export interface ItemRefResolved {
  readonly label: string;
  readonly icon: ReactNode; // small SVG icon for inline links
  readonly route: AppRoute | null; // null = deleted/inaccessible
  readonly breadcrumb?: string; // optional context (e.g., "Acme renewal · 11:43 message")
}

export type ItemRefResolver<K extends ItemKind> = (
  id: ItemRef extends { kind: K; id: infer I } ? I : never,
) => Promise<ItemRefResolved | null>;

export function registerItemRefResolver<K extends ItemKind>(
  kind: K,
  resolver: ItemRefResolver<K>,
): void;

export function resolveItemRef(ref: ItemRef): Promise<ItemRefResolved | null>;
```

Each destination's `index.ts` registers its kind on package import. The registry is a module-singleton; no React context (resolution happens outside the component tree, e.g., from a hook hidden inside `<ItemLink>`).

### 3.4 `formatRelativeTime` hoist — shared-primitives agent ships it

Moves from `HomeDestination.tsx:70-85` → `packages/chat-surface/src/util/time.ts` in the shared-primitives agent's PR. Every consumer imports from there. Locale-aware (Intl.RelativeTimeFormat); tested against a frozen clock.

### 3.5 Deferred-features inventory — add to master PRD as appendix

Append to `destinations-master-prd.md` a §15 Deferred Features Inventory tracking what each phase explicitly punts:

- Per-user personalization of section order (Home / Todos sections) → Wave 4
- Recurring todos → Wave 4
- Todo subtasks → Wave 5
- Multiplayer threads → Wave 5
- Snooze-on-extraction → Wave 4
- Routine forking + templates → Wave 5
- Workflow DAGs (multi-step routines) → Wave 5+ (or never)
- Per-second cron granularity → never (1m minimum)
- Third-party scheduler integration → connector concern; not core
- Cross-tenant sharing → never (security boundary)
- Project-default-context for new todos → Wave 3 (Projects)
- Bulk reply / bulk snooze in Inbox → Wave 4

When a phase wants to ship one of these, it lands its own sub-PRD that bumps the wave.

---

## 4. Shared-primitives prerequisite agent (must run BEFORE any destination impl)

Multiple destinations need primitives that don't exist yet. Rather than each phase racing to build its own, **one foundation agent** ships them first. All destination impl agents wait for this merge.

**Agent:** "Shared Primitives — Wave 2 prerequisites"
**Branch:** `worktree-agent-shared-primitives`
**Files owned (no overlap with destination agents):**

- `packages/api-types/src/refs.ts` (NEW): `ItemKind`, `ItemRef`, `ItemRefSnapshot`, `SectionResult<T>`.
- `packages/api-types/src/brands.ts` (NEW): every branded ID type.
- `packages/api-types/src/index.ts`: re-export from above + remove any duplicated definitions in existing code paths.
- `packages/chat-surface/src/refs/registry.ts` (NEW): registry implementation.
- `packages/chat-surface/src/refs/ItemLink.tsx` (NEW): the `<ItemLink ref>` component.
- `packages/chat-surface/src/shell/PageHeader.tsx` (NEW).
- `packages/chat-surface/src/shell/FilterTabs.tsx` (NEW — promote from any prior inline impl).
- `packages/chat-surface/src/shell/StatusPill.tsx` (NEW).
- `packages/chat-surface/src/shell/EmptyState.tsx` (NEW).
- `packages/chat-surface/src/shell/CardGrid.tsx` (NEW).
- `packages/chat-surface/src/shell/DocList.tsx` (NEW).
- `packages/chat-surface/src/shell/ActivityList.tsx` (NEW).
- `packages/chat-surface/src/util/time.ts` (NEW): hoist `formatRelativeTime`.
- `packages/chat-surface/src/ports/BadgePort.ts`, `NotificationPort.ts`, `FilePickerPort.ts`, `ClipboardPort.ts` (NEW).
- `packages/chat-surface/src/ports/index.ts`: aggregate port exports.
- Updates to `packages/chat-surface/src/index.ts` re-exports.
- Tests for every primitive + the registry.
- Updates the existing `formatRelativeTime` call site in `HomeDestination.tsx` to import from the new location (tiny diff; non-conflict with destination agents because destination agents will get the updated file at merge time).

**Acceptance:** all chat-surface tests pass. No destination impl runs until this is merged to main.

---

## 5. System-level decisions affecting every phase

### 5.1 `request_id` propagation

Every audit row, every log line, every span carries a `request_id`. Source: OpenTelemetry trace_id (already in `apps/frontend/src/observability/otel.ts`). The facade extracts/injects on inbound; backend + ai-backend propagate via the existing service-token + headers pipeline.

No new infrastructure. Document the contract in `services/backend-facade/CLAUDE.md` so impl agents don't reinvent.

### 5.2 SSE vs polling — convention

Live streams (Inbox new items, Home activity, Routines next-fire countdown) use SSE. Cheap polling fallbacks (`/v1/inbox/unread_count` every 60s) exist for environments where SSE breaks (corporate proxies stripping `text/event-stream`).

Single SSE convention: `GET /v1/<resource>/stream`, server emits typed events with `event:` and `data:` fields. The facade does NOT buffer; it streams pass-through. Reconnect via `Last-Event-ID` header. This matches the existing run-event streaming pattern.

### 5.3 Cascade-on-delete rule (single rule across destinations)

Master PRD §3.11 amended: when an item is deleted, references TO it follow this default unless the sub-PRD explicitly overrides:

- **Same-destination references** cascade-delete (e.g., delete a thread → delete its messages; not a cross-destination concern).
- **Cross-destination references**: keep the reference; the `ItemLink` resolver returns `{ route: null }` (dead link); UI renders `<deleted ${kind}>` chip with the breadcrumb if available.
- **Audit references**: never cascade; audit is append-only. The audit row's `target_id` remains; the row is anonymized only on tenant-level GDPR delete.

Each sub-PRD's §13 cascade table is reviewed against this default; deviations require explicit justification (audit doc adds them as exceptions).

### 5.4 Port injection convention

Every port is provided by the host (frontend or desktop) via a React provider at the top of the app, mirroring the existing `TransportProvider`/`RouterProvider` shape. Default web no-ops live in `apps/frontend/src/ports/*Web.ts`. Default desktop implementations live in `apps/desktop/src/main/ports/*Native.ts` (when desktop ships).

Destinations call `usePort("badge")` / `usePort("notification")` etc. Never `if (window…)`.

### 5.5 Token-usage tracking — every LLM call is accounted and traceable

**Requirement (system-level):** every LLM call made by Atlas — from a chat run, a todo-extraction, a routine fire, a memory-retrieval, anything — must be tagged, recorded, and queryable. Tenant-isolated. PII-free. Single integration point so attribution is uniform across phases.

**Status (2026-05-17):** the canonical implementation **already exists** in `services/ai-backend/`. Phase 0.6 ships the missing CI guard that locks the single-integration-point invariant; Phase 3 and Phase 5 extend the existing `Purpose` enum rather than introducing a parallel `(source_kind, source_id)` shape. **Do not build a parallel `llm_token_usage` table in `services/backend/`** — `runtime_run_usage` + `runtime_model_call_usage` in `services/ai-backend/` are the single source of truth.

**The existing TU-1 implementation** (audited 2026-05-17 by Phase 0.6 agent; all paths verified):

| Concern                                        | Existing implementation                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Single integration point                       | `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` → `build_chat_model` → `langchain.chat_models.init_chat_model`. Every provider invocation routes here.                                                                                                                                                                                                                                                            |
| Single tracker boundary                        | `services/ai-backend/src/agent_runtime/observability/usage_recorder.py` — `UsageRecorder` protocol with `PostgresUsageRecorder`, `InMemoryUsageRecorder`, `NullUsageRecorder`, `SummarizationUsageRecorder` implementations.                                                                                                                                                                                                              |
| Storage                                        | Two tables in `services/ai-backend/` Postgres: `runtime_run_usage` (per-run rollup, migration `0004`) and `runtime_model_call_usage` (per-call detail, migration `0005`). Both carry all 7 token kinds (`input`, `output`, `cached_input`, `cache_creation_input`, etc.), `cost_micro_usd`, `pricing_id`, `pricing_version`, plus `org_id` + `user_id` + `conversation_id` + `run_id` + `trace_id` dimensions and `org_id`-first indices. |
| Attribution dimensions                         | `RuntimeModelCallUsageRecord` carries `run_id`, `conversation_id`, `task_id`, `subagent_id`, `connector_slug`, **`purpose`** (the attribution discriminator), `originating_tool_call_id`, `originating_tool_name`.                                                                                                                                                                                                                        |
| `Purpose` enum (the attribution discriminator) | `services/ai-backend/src/agent_runtime/observability/attribution.py` — `Purpose: StrEnum` with `MAIN`, `TOOL_PLANNING`, `TOOL_INTERPRETATION`, `SUBAGENT_WORK`, `CONTEXT_COMPRESSION`. Deterministically derived per call via `Purpose.derive()`. Cross-component aggregation already works: `GROUP BY purpose`.                                                                                                                          |
| Pricing                                        | `services/ai-backend/src/agent_runtime/pricing/` — `CostCalculator` with banker's rounding, `ModelPricingCatalog` with time-keyed lookup + LRU cache, LiteLLM-sourced seeds, versioned `ModelPricingRecord`. Auditable via `pricing/seeds/` + `pricing/litellm_source.py`.                                                                                                                                                                |
| Per-provider extraction                        | `services/ai-backend/src/agent_runtime/observability/token_usage.py` — `NormalizedTokenUsage` + per-provider extractors (`OpenAIProviderTokenUsageExtractor`, `AnthropicProviderTokenUsageExtractor`, `GeminiProviderTokenUsageExtractor`) + LCD fallback + dispatch registry.                                                                                                                                                            |
| Query API                                      | `services/ai-backend/src/runtime_api/http/routes.py` mounts at `/v1/usage`: `/me`, `/me/conversations`, `/runs/{run_id}`, `/conversations/{conversation_id}`, `/org`, `/org/subagents`, `/org/purpose`. RBAC enforced; tenant-first filtering. Proxied through `services/backend-facade/src/backend_facade/app.py`.                                                                                                                       |
| TypeScript contract                            | `packages/api-types/src/index.ts` — `UsageMeResponse`, `UsageOrgResponse`, `UsageOrgSubagentsResponse`, `UsageOrgPurposeResponse`, `RunUsageBreakdown`, `ConversationUsageResponse`, `UsagePeriod`, all row types.                                                                                                                                                                                                                        |
| Tests                                          | `services/ai-backend/tests/unit/runtime_api/test_usage_routes.py` + `tests/unit/agent_runtime/observability/`.                                                                                                                                                                                                                                                                                                                            |

**What Phase 0.6 shipped (commit `4939186`):**

- `tools/check_llm_provider_imports.py` — AST-based pre-commit guard that flags any direct import of `anthropic`, `openai`, `google.generativeai`, `google.genai`, `langchain_anthropic`, `langchain_openai`, `langchain_google_genai`, `langchain_google_vertexai` outside `deep_agent_builder.py`. Inline `# allow-direct-llm-import: <reason>` marker exempts justified one-offs.
- `tools/test_check_llm_provider_imports.py` — 12 self-tests including planted-violation end-to-end + baseline that real tree passes (403 files).
- `.pre-commit-config.yaml` — wires the guard scoped to `services/(ai-backend|backend|backend-facade)/src/.*\.py$`.

This locks the single-integration-point invariant going forward. Any future LLM call MUST route through `build_chat_model`; the existing `UsageRecorder` captures the row.

**Phase 3 / Phase 5 attribution rule (binding):**

Out-of-run LLM calls (todo-extraction, routine fires that wrap a run, memory retrieval) attribute via the **existing `Purpose` enum**, not a new `(source_kind, source_id)` shape. Phase 3 P3-A extends `Purpose` with `TODO_EXTRACTION`; Phase 5 P5-A wraps every routine fire as a regular ai-backend run with `run.source = { kind: "routine", routine_id }` so `runtime_model_call_usage` rows already attribute correctly (run_id → routine via the run's source). Aggregating "what did Todos extraction cost this month" becomes `WHERE purpose = 'todo_extraction' AND org_id = $1` against `runtime_model_call_usage`.

**Why this is the right call (staff-engineer-grade):**

- **DRY:** the existing infrastructure is complete; building a parallel `services/backend/usage/` would duplicate a working system.
- **Single source of truth:** all usage queries hit `runtime_run_usage` / `runtime_model_call_usage`. No two-table reconciliation, no eventual-consistency between services for billing data.
- **Service boundary respected:** usage lives next to runs/messages/events in `ai-backend`. Pushing it into `backend` would force ai-backend to write to backend's table over HTTP per LLM call — adding hop latency and a failure mode the current direct write does not have.
- **No-op delta for Phases 1, 2, 4:** they call `build_chat_model` like every other LLM site; `UsageRecorder` already captures.

**Mandatory consumers:** Phase 3 Todos extraction (via new `Purpose.TODO_EXTRACTION`), Phase 5 Routines runs (via `run.source.kind = "routine"`), Phase 1 Chats runs (existing path; no work needed), Phase 6 Library indexing (when it lands; new Purpose value), Phase 11 Memory retrieval (when it lands; new Purpose value), and any future LLM-calling code path.

**Tenant isolation, PII, retention:** all enforced by the existing implementation (queries are `org_id`-first; rows carry token counts + dimensions only, no message content; retention is configured at the Postgres level matching the audit-window policy).

**Follow-up TODOs (deferred, not blocking):**

- Anthropic prompt-caching: `NormalizedTokenUsage` separates `cached_input_tokens` (cache-read) from `cache_creation_input_tokens` (cache-write), but `CostCalculator.compute` currently bills cached at one rate and folds `cache_creation_input_tokens` into gross `input_tokens` (full rate). If Anthropic exposes a distinct cache-write rate column, add a third pricing column.
- Dynamic-import bypass of the CI guard (`importlib`) is not caught by AST scan. Acceptable; reviewers ask "why?".

---

## 6. Decision summary table (for impl agents — quick reference)

| Concern                                                       | Decision                                                             | Authority |
| ------------------------------------------------------------- | -------------------------------------------------------------------- | --------- | ---- |
| Cross-destination link type                                   | `ItemRef` in `api-types/src/refs.ts`; rendered via `<ItemLink>`      | §1.1      |
| BadgePort + NotificationPort + FilePickerPort + ClipboardPort | Defined in §1.2; shipped by shared-primitives agent                  | §1.2 / §4 |
| Project-scoped access (any resource carrying `project_id`)    | Owner write; member + admin read; 404 to non-readers                 | §1.3      |
| Audit row                                                     | Master shape + optional `context: object                             | null`     | §1.4 |
| Filter axis repeatability                                     | Multi-value within axis = OR; cross-axis = AND                       | §1.5      |
| `<PageHeader>` shape                                          | Defined in §1.6; owned by shared-primitives agent                    | §1.6      |
| Branded IDs                                                   | All entity IDs branded in `api-types/src/brands.ts`                  | §2.1      |
| Approval terminology                                          | "Approval" noun, "approval_request" Inbox kind, `approval.*` actions | §2.2      |
| Partial-failure pattern                                       | `SectionResult<T>` for aggregation endpoints only                    | §2.3      |
| Routine webhook security                                      | Rotating secret + 7d grace + optional IP allowlist                   | §2.4      |
| `<ItemLink>` registry                                         | Module-singleton at `chat-surface/src/refs/registry.ts`              | §3.3      |
| `formatRelativeTime`                                          | Hoisted to `chat-surface/src/util/time.ts`                           | §3.4      |
| Deferred-features list                                        | Appendix in master PRD                                               | §3.5      |
| `request_id` propagation                                      | OTel trace_id; facade injects                                        | §5.1      |
| SSE convention                                                | `GET /v1/<resource>/stream`; `Last-Event-ID` reconnect               | §5.2      |
| Cascade-on-delete default                                     | Cross-destination → dead link; audit → never cascade                 | §5.3      |
| Port injection                                                | React providers at app root; no-op defaults on web                   | §5.4      |
| Shared primitives agent                                       | Runs BEFORE any destination impl; see §4 file list                   | §4        |

---

## 7. Updates to existing master PRDs (companion edits)

The following sections in `PRD.md` and `destinations-master-prd.md` are extended by this audit. Rather than rewriting them, this audit is the canonical reference; future PRD edits should incorporate.

- **`destinations-master-prd.md` §3.2 (audit):** row schema gains optional `context`. (§1.4)
- **§3.4 (authz):** project-scoped access rule lifted from Todos to master. (§1.3)
- **§3.5 (pagination + search):** multi-value filter axes = OR. (§1.5)
- **§3.10 (states):** `SectionResult<T>` is the master pattern for partial-failure aggregation. (§2.3)
- **§3.11 (cross-destination references):** cascade-on-delete default is dead-link, not cascade. (§5.3)
- **§4 (shared primitives):** added rows for `PageHeader`, `StatusPill`, `ItemLink + registry`, `formatRelativeTime`, plus port inventory. (§1.2, §1.6, §3.3, §3.4)

---

## 8. References

- Cross-PRD audit report (audit-agent output, not a checked-in file — captured in this conversation's transcript)
- [PRD.md](PRD.md) §13 open decisions (5 product calls still pending — separate from this audit)
- [destinations-master-prd.md](destinations-master-prd.md) §3 enterprise checklist + §4 shared primitives + §7 sub-PRD dispatch
- [destinations/](destinations/) — the 4 (and counting) sub-PRDs this audit reconciles

---

## 9. Late-arriving product decisions (revisions to merged sub-PRDs)

These override decisions in earlier-merged sub-PRDs. Impl agents read **this section** as the binding source, not the original sub-PRD text.

### 9.1 Inbox Q6 — routing rule revised (drops priority filter)

**Original sub-PRD decision (now superseded):** Inbox fallback row created when `priority=high` AND user inactive >5min in originating thread.

**Revised binding decision:**

Inline-in-surface by default. A durable Inbox row is created when the user has not viewed the originating thread within `INBOX_FALLBACK_INACTIVITY_MS` — **regardless of priority**. The window is **tenant-configurable** (Settings → Workspace → Inbox routing window; default `5min`).

Implications for P4-A impl:

- Drop the `priority == high` predicate from the producer's "should we create an Inbox row" check.
- The tenant config lives in the existing `tenants` table as a new column `inbox_fallback_inactivity_ms: int default 300_000`.
- Settings UI for this is a Wave-2-or-later admin surface — out of scope for P4-A but the column lands in P4-A's migration. UI gets a follow-up issue.

### 9.2 Inbox Q8 — narrow viewport breakpoint confirmed

**Binding decision:** Single-pane swap below 960px (list ↔ detail). Wider: two-pane with detail rendered alongside list. (Matches Gmail / Linear / Notion.)

### 9.3 Inbox Q7 — reply-to-error routing refined

**Binding decision:** Combined (a) + (c):

- The agent that owned the failed run is notified (audited).
- The connectors-destination repair flow opens on click, with the "Re-authorize" button as the primary action.
- Reply is comment-only (routes to the connector owner; does not retry the failed run automatically).

### 9.4 Phase 1 Chats canvas — all 10 sub-PRD recommendations accepted as binding

See [implementation-plan.md](implementation-plan.md) §3 (Phase 1) for the full table. No deviations.

### 9.6 Phase 3 Todos — all 9 questions resolved (4 deviations from sub-PRD recs)

See [implementation-plan.md](implementation-plan.md) §3 (Phase 3) for the full table. Four deviations:

- **Q2 Recurring todos** — **IN Phase 3** (sub-PRD recommended Wave 4 deferral; orchestrator pulled forward). Spec: cron-spec on Todo + materialization worker in ai-backend. Series UNIQUE on `(series_id, due_date)` for idempotent re-fires. See implementation-plan §11.1.
- **Q3 Subtasks** — **IN Phase 3** (sub-PRD recommended Wave 5 deferral; orchestrator pulled forward). One level of nesting; no infinite tree. Parent.done computed from children. Cascade-delete to children. See implementation-plan §11.2.
- **Q6 Project default** — **context-aware**, more specific than sub-PRD's "current project if applicable, else Unfiled". Three rules: project-detail view → that project's id; /todos direct → null; inline-add → inherits TodosPanel's active project filter.
- **Q9 LLM prompt + budget** — **Impl-C proposes; orchestrator approves before merge**, AND every LLM call must flow through the new system-level token-usage tracker (§5.5 above). Sub-PRD's specific prompt/budget recommendation is a starting point but not pre-approved.

**Scope impact on P3-A and P3-B:** the addition of recurring + subtasks expands Phase 3 by an estimated 30-40% (extra schema, materialization worker, parent/child UI, cascade rules, tests). Impl-A and Impl-B briefs are extended accordingly; no extra agent needed unless the implementation surfaces complexity that warrants an Impl-C carve-out for the materializer.

### 9.5 Phase 2 Home — all 8 questions resolved (3 deviations from sub-PRD recs)

See [implementation-plan.md](implementation-plan.md) §3 (Phase 2) for the full table. Three deviations from the sub-PRD's recommendations:

- **Q1 Activity window length** — user-configurable **in Phase 2** (per-user KV `home.activity_window_hours`, allowed values 6/12/24/48/168), not deferred to Wave 4+. Affects P2-A backend: the `/v1/home` route reads the per-user window from KV instead of hard-coding 24h. Tests must cover non-default windows.
- **Q5 Greeting personalization** — fallback chain stops at `"Good morning."` (no name interpolation if both IdP fields are missing). Email local-part is NOT used. Affects P2-A backend's greeting composer.
- **Q8 SSE drop-off** — exponential backoff range explicitly **1s → 30s**. No "paused" indicator surfaced to the user. Affects P2-B chat-surface SSE reconnect logic.

Q7 quick-action customization: "do whatever is easy" — interpreted as **server-driven defaults only in Phase 2**; admin endpoint deferred to Wave 5+. Sub-PRD recommendation aligns; no deviation.

### 9.7 Phase 5 Routines — all 14 questions resolved (2026-05-17)

Decisions are binding for P5-A backend + P5-B chat-surface. Sub-PRD `destinations/routines-prd.md` §16 enumerates the questions; this section records the resolution.

| #   | Question                                        | Decision                                                                                                                                                                               |
| --- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Code-routines (repo + env / executor + sandbox) | **Wire shape lands now (forwards-compatible); executor + sandbox deferred to Wave 6**. Yes we will implement it; the Wave/Phase terminology is loose — Phase 6 = Wave 6.               |
| 2   | Manual run-now ACL                              | **Default owner-only; override field `permissions.manual_fire = "owner" \| "project_members" \| "tenant"`**                                                                            |
| 3   | Library page output mode                        | **`new_per_fire` with date stamp ("Daily briefing — 2026-05-17") default; `update_same` opt-in**                                                                                       |
| 4   | Permission shrinkage at fire time               | **Auto-pause + Inbox CTA + manual resume**; do NOT auto-edit-down                                                                                                                      |
| 5   | Auto-resume on permission restoration           | **NO** — avoid post-vacation re-auth surprise fires                                                                                                                                    |
| 6   | Webhook security beyond rotating secret + IP    | **HMAC-of-payload signature as next add (next-best after secret + allowlist)**; mTLS deferred to Wave 5+. Wire shape lands now: `X-Atlas-Routine-Signature: hmac-sha256=<hex>` header. |
| 7   | Missed-fire policy default                      | **`fire_once` (catch-up exactly once on resume; skip backlog)**. Per-routine override `missed_fire_policy: "fire_once" \| "fire_all" \| "skip"` is in the wire either way.             |
| 8   | Routine quotas per tenant                       | **100 active routines per USER** (not per tenant). Manual-fires + webhook-fires quotas TBD with Phase 5 P5-A (default 500/day/user, 100k webhook-fires/day/tenant).                    |
| 9   | Atlas-proposed cron suggestions                 | **Phase 5/6 — wire signal capture now, UX in Phase 6**                                                                                                                                 |
| 10  | Auto-extracted "Make this a routine?" CTA       | **Phase 6**                                                                                                                                                                            |
| 11  | Snapshot vs live agent reference at fire time   | **Live re-resolve at fire time; explicit `agent_version_pin` field forwards-compatible for users who want pinned**                                                                     |
| 12  | Admin force-reassign owner / force-pause        | **Out of scope** (deferred indefinitely; revisit if compliance auditor flags)                                                                                                          |
| 13  | Routine forking / templates                     | **Wave 5+** (no change from sub-PRD)                                                                                                                                                   |
| 14  | Tenant + per-user default notification prefs    | **Per-routine controls land in wire (§3.9); Settings UI for tenant/user defaults punted to Wave 6**                                                                                    |

**Token-usage attribution for Routines (cross-ref §5.5):** every routine fire is wrapped as a regular ai-backend run with `run.source = { kind: "routine", routine_id }`. Existing `runtime_model_call_usage` rows attribute correctly via the run dimension; no parallel token-usage path. Aggregating "what did this routine cost this month" becomes `WHERE run.source.routine_id = $1 AND org_id = $2`.

**Code-routines wire-shape preservation (deviation note, Q1):** the user explicitly confirmed implementation in a future wave, NOT cancellation. P5-A's `packages/api-types/src/routines.ts` MUST include the forwards-compatible `code?: { repo_ref: ItemRef, env_ref: ItemRef, entry: string }` field on the routine wire shape, even though the executor + sandbox land in Wave 6. Schema gates this field behind a feature flag at the backend; the wire shape is stable.

### 9.8 Phase 6 Projects — 9 questions resolved (2026-05-17)

Decisions binding for P6-A (backend) + P6-B (chat-surface) + P6.5 (extensions). Sub-PRD `destinations/projects-prd.md` and `destinations/projects-extensions-prd.md` enumerate the questions; this section records the **user's explicit overrides** to the sub-PRD recommendations.

| #   | Question                                                                     | Decision                                                                                                                                                                                                                                                                                                       |
| --- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Admin-level project ownership transfer / forced reassignment on deactivation | **Deferred indefinitely.** User reasoning verbatim: "Wtf is admin force-transfer endpoint? That seems so naive and security hazard to me." Tenant admin actions affecting per-user product state require explicit audit + approval flow; not in scope until W6+ compliance review surfaces a real requirement. |
| 2   | Inheritance for "in-progress" chats not filed under a project                | **No inference.** Existing chats are NOT retroactively assigned to projects. A chat acquires a project only when the user explicitly files it under one. Sub-PRD's "infer from connector-overlap" recommendation rejected.                                                                                     |
| 3   | Inheritance scope for `default_connector_allowlist`                          | **Create-time inheritance only.** New routines / chats filed under a project inherit at create-time; subsequent project allowlist edits do NOT cascade (per-artifact override wins). Single source of truth: the project's allowlist + the artifact's override snapshot.                                       |
| 4   | Archive a project that has running chats / routines                          | **Cannot archive while anything is running.** Implementation rule: **one orchestrator (single source of truth)** owns the "is anything live?" check across chats + runs + routines; the archive endpoint queries that one source. Sub-PRD's "soft archive + warn" rejected.                                    |
| 5   | Editor role                                                                  | **In scope — `viewer` / `editor` / `owner` triad lands now.** Per-user role on `project_members`; ACL helper `is_project_member(user, project, min_role)` is the single canonical check.                                                                                                                       |
| 6   | Templates / forking                                                          | **In scope now (Phase 6.5).** A project can be saved as a template and forked. `project_templates` table + `POST /v1/project-templates/{id}/fork`. Brand: `ProjectTemplateId`.                                                                                                                                 |
| 7   | Storage layout question (sub-PRD §6.x)                                       | **Out of scope.** Whatever P6-A1 (already landed) chose stands.                                                                                                                                                                                                                                                |
| 8   | (sub-PRD-specific implementation detail)                                     | **Out of scope.**                                                                                                                                                                                                                                                                                              |
| 9   | (sub-PRD-specific implementation detail)                                     | **No strong preference — ship what's simplest.**                                                                                                                                                                                                                                                               |

**Single-orchestrator note (Q4 implementation):** the "is anything live?" check is a service call to the `ai-backend` `runtime_api` (it owns `runs` + `conversations` lifecycle) — `backend` does NOT keep a parallel projection. P6 archive endpoint is `services/backend-facade/.../projects.py::archive` which:

1. Asks `backend` for project metadata + permission check
2. Asks `ai-backend` `/internal/v1/runs?project_id=...&status=running` for live runs
3. Asks `backend` for active routines under the project
4. Refuses with 409 + JSON body listing the blockers if any are live.

This preserves the boundary (no cross-service DB reads) while keeping the source of truth single per fact.

### 9.9 Phase 7 Library — sub-PRD recommendations accepted as binding (2026-05-17)

The user delegated Phase 7 question resolution to the orchestrator ("Make the best choices and finish"). All 12 open sub-PRD questions in `destinations/library-prd.md` §15 resolved by accepting the sub-PRD's stated recommendations. Notable:

- **Save-to-Library popover** is the universal entry point (every destination's overflow menu); no per-destination duplication of the picker UI.
- **Three-stage upload** (grant → PUT signed URL → finalize) is the only path that puts bytes on disk; bytes never proxy through the API. Mirrors the audited pattern in cross-audit §3.6.
- **Page editor** is markdown-first with WYSIWYG-on-rendering; no rich-text editor in P7. Reuses the chat composer's markdown rendering.
- **Retrieval (BM25 + pgvector + RRF + embeddings)** deferred to **Phase 7.5** (P7-A3 stalled mid-implementation). The wire shape is forward-compatible: `GET /v1/library/search?q=...` returns `LibrarySearchHit[]` with score + scope_metadata; Phase 7.5 just plugs in the real scorer.
- **`Purpose.LIBRARY_RETRIEVAL` and `Purpose.LIBRARY_INDEXING`** extend the runtime tracker (§5.5) — Phase 7.5 wires the embedding model through the canonical `build_chat_model` path. TU-1 invariant preserved.

### 9.10 Phase 8 Agents — sub-PRD recommendations accepted as binding (2026-05-17)

Same orchestrator-delegated resolution as §9.9. All 9 open sub-PRD questions in `destinations/agents-prd.md` resolved by accepting the sub-PRD's stated recommendations. Notable:

- **Agent versioning** is **immutable-snapshot per published version** + `current_version_id` pointer. Edits create a new version; running invocations pin to their resolved version_id at run start. Matches the cross-audit §5.3 "snapshot-pin" rule already used by Routines (Q11).
- **Fork** copies the full agent shape (instructions, tool grants, MCP install refs, skills) but generates a new `agent_id` + clears `current_version_id` so the fork starts as draft.
- **Agent usage chart** is a **READ-ONLY aggregation** over `runtime_run_usage` + `runtime_model_call_usage` (§5.5) — no parallel usage tracker.
- **Gallery filter axes** are App-Store-shaped (My / Installed / Available / Custom / By skill); cross-audit §1.5 FilterTabs primitive handles the rendering.
- **Editor permission model**: only the owner (creator) edits a custom agent today; workspace-level admin edits + multi-author editing deferred to Wave 6.

### 9.11 Phase 10 Tools — sub-PRD recommendations accepted (2026-05-18)

The user delegated Phase 10 resolution to the orchestrator. All 9 open sub-PRD questions in `destinations/tools-prd.md` §10 resolved by accepting the orchestrator's stated recommendations. Notable:

- **Code-routines** (Routines §9.7 Q1 deferral) land in the Tools catalog as `kind = "code"`. The wire shape from Phase 5 (`code?: { repo_ref, env_ref, entry }`) plugs straight into `Tool.code_ref`. Sandbox executor (P10-A3) is in-process Python 3.13 with AST-validation against an allow-list (62 banned imports + 13 banned globals + 10 banned attribute prefixes including the classic `__subclasses__` / `__mro__` / `__globals__` escapes). **In-process v1 is NOT a security boundary for untrusted multi-tenant code**; production needs a container-adapter variant behind the same `CodeSandboxPort` Protocol (deferred to a follow-up wave).
- **TU-1 invariant preserved**: code-routines that don't call an LLM never touch the `Purpose` enum. Code-routines that do call an LLM continue to flow through the existing tool-call envelope. No new tracker, no new `Purpose` value added by P10-A3.
- **Usage projection** (`ToolUsageProjection`) is a `GROUP BY tool_id` read over `runtime_tool_invocations` + `runtime_model_call_usage`. No parallel `tool_usage_daily` table.
- **Onboarding wizard** (P10-B3) uses textareas with monospace + line numbers for code editing; Monaco / CodeMirror integration is a Wave-12 polish decision (keeps chat-surface dependency-free).
- **Composer-popover types renamed** (P10-A2): the legacy `ToolKind` / `ToolDescriptor` / `ToolListResponse` in `api-types/index.ts` were renamed to `ComposerTool*` so the canonical destination names belong to Phase 10. Three chat-surface files were updated minimally (imports only).
- **P10-B2 + P10-B3 shipped production code without their full test suites** (Wave-2 agents hit the org token limit mid-stream). The components compile; tests are filed as audit-gate follow-up rather than a hard re-dispatch.

### 9.12 Phase 11 Connectors — sub-PRD recommendations accepted (2026-05-18)

Same orchestrator-delegated resolution. All 7 open sub-PRD questions in `destinations/connectors-prd.md` §10 resolved by accepting the orchestrator's stated recommendations. Notable:

- **HMAC-of-payload signature** (Routines §9.7 Q6 deferral) lands as the consolidated webhook UX. HMAC algorithm + header names + skew window live as constants in `services/backend/src/backend_app/webhooks/signer.py` (`HMAC_ALGO = "hmac-sha256"`, `SIGNATURE_HEADER = "X-Atlas-Routine-Signature"`, `TIMESTAMP_HEADER = "X-Atlas-Signature-Timestamp"`, `TIMESTAMP_MAX_SKEW_S = 300`). Single source of truth on the server side; the chat-surface wizard renders the PRD §9.4 verification snippet byte-for-byte for the receiver to copy.
- **DRY win**: the `connectors` table is a denormalized read model over the existing `mcp_servers` + `token_vault` paths — zero new OAuth code. Writes flow through the existing `McpRegistryService` / `TokenVault`; the `ConnectorsService` is the substitution point that emits destination-level audit rows + projects the consumer view.
- **Webhook secrets** ride the existing `TokenVault` (no parallel secret store). 14-day grace window per PRD §9.2 via `previous_vault_ref` + `previous_expires_at`. Plaintext is surfaced ONLY in `WebhookCreateResponse.secret_plaintext` and `WebhookRotateResponse.{secret_plaintext, grace_secret_plaintext}` (copy-once-reveal); subsequent GETs return the redacted `Webhook`.
- **Secret-passing in apps/frontend** (P11-C-finish): plaintext lives only in `WebhooksRoute` component state, cleared on dismiss / wizard-close / unmount. **Test-pinned**: the plaintext string never appears in `localStorage`, `sessionStorage`, or `document.body.innerHTML` after dismissal.
- **In-destination routing** (`ConnectorsGateway`) uses local state because `HashRouter` only models top-level `/<slug>` paths today. Single swap-point when sub-slug routing lands.
- **Wave-2 token-limit recovery** (audit cadence note): the original P11-B + P11-C agents hit the org token limit mid-stream. Partial work was committed (chat-surface shell + RevealOnce; frontend API wrappers + adapters); follow-up agents (P11-B-finish + P11-C-finish) shipped the missing 8 chat-surface components + 3 frontend routes. Both follow-up agents flagged the SAME api-types re-export gap, which a clean-up commit (`70bec1b`) closed by adding the webhook lifecycle types to the package index. **Lesson for future waves**: always re-export every wire type used by destination-level UI from the package index, not just the top-level entity types.

### 9.13 Phase 12 Team + Memory + ⌘K palette + polish — sub-PRD recommendations accepted (2026-05-18)

The user delegated Phase 12 resolution to the orchestrator. All 7 open sub-PRD questions in `destinations/team-memory-cmdk-prd.md` §10 resolved by accepting the orchestrator's stated recommendations. Notable:

- **DRY win — memory embeddings**: memory items index into the existing `library_embeddings` table with `target_kind="memory"`. The `IndexJobTargetKindLiteral` union was widened from `Literal["file","page","dataset"]` to `Literal["file","page","dataset","memory"]` so the existing Library indexer worker drains memory queue entries unchanged. **No `memory_embeddings` table** was created.
- **DRY win — settings storage**: NotificationDefaults + WebhookSecurityDefaults piggyback on the existing `user_preferences` JSONB table (Phase 2 home pattern) under namespaced keys, plus a new `tenant_settings` table for workspace defaults. The PATCH endpoints deep-merge so existing `home.activity_window_hours` (Phase 2) + `home.last_visit_iso` (P9-A2) preferences survive unrelated PATCHes — **test-pinned** in both `test_settings_store.py` and `test_settings_routes.py`.
- **DRY win — team module**: zero new identity code. `TeamService` wraps the existing `InvitationsService.create(...)` + `IdentityStore.{revoke_role, assign_role, append_identity_audit, transaction}`. Role-change audits flow through the existing identity audit chain (no parallel audit table).
- **Routines §9.7 Q12 (admin force-reassign) re-evaluated and stays deferred.** The offboarding wizard implements the controlled-handoff workflow (U-T5): the admin picks a new owner per asset. Only `kind: "project"` cascades successfully today via `ProjectsService.force_transfer_ownership`; agent / tool / connector reassignments surface inline as "Not supported in v1 — re-assign manually after offboard" in BOTH the Reassign step AND the Review step of the wizard, so the admin is fully informed before confirming. **The naive admin force-transfer endpoint is NOT shipped.** Re-evaluation matches the cross-audit §9.8 Q1 user verdict.
- **TU-1 single-tracker invariant preserved**: 4 new Purpose enum values (`PALETTE_RANKING`, `MEMORY_RETRIEVAL`, `MEMORY_INDEXING`, `MEMORY_EXTRACTION`); count pin moved from 8 → 12. Memory extractor (`runtime_worker/jobs/proposal_extractor.py`) checks the $0.001-per-run cost cap **before** the LLM call using a flat-rate estimator — budget-exceeded returns `skipped_reason="cost_cap_exceeded"` with no LLM call, no `runtime_model_call_usage` row, one info log. Pre-call gating means no wasted spend on budget-exceeded runs.
- **Palette substrate-seam preserved**: `<CommandPalette>` is search-on-open + debounced (150ms); it never fetches directly. Every search goes through a `PaletteSearchPort.search(req)` Protocol the host wires. Web host (`apps/frontend/src/features/palette/PaletteHost.tsx`) provides `createWebPaletteSearchPort(identity)` that calls `paletteApi.search`; desktop hosts wire their own IPC adapter. Single seam, multiple substrates — DRY win.
- **⌘K hotkey is global**: `<PaletteHost>` mounts exactly once at the App root inside `<ChatShell>`. Verified by `grep -c '<PaletteHost' apps/frontend/src/app/App.tsx == 1`. Single instance per page; no per-route mount.
- **In-destination routing** (TeamGateway / MemoryGateway / SettingsGateway): mirrors the `ConnectorsGateway` pattern from Phase 11 (HashRouter only models top-level `/<slug>` paths today). Single swap-point when sub-slug routing lands.
- **Wave-2 audit-fix commit** (`27c47f6`): P12-C wrote `PaletteHost.tsx` against the pre-revamp `<CommandPalette>` shape (`extraEntries` + local `CommandPaletteEntry` adapter) which it documented as a "TODO drop when P12-B3 lands". P12-B3 shipped the revamped palette with `open` / `onRequestClose` / `searchPort` / `starterActions` props, breaking the typecheck at merge. The audit commit rewires `PaletteHost.tsx` to the canonical interface and deletes the now-obsolete `features/palette/adapters.ts` + `CommandPaletteEntry` shim. **Lesson reinforced from §9.12**: the package index is the contract; agents that consume a sibling agent's surface should never mirror-types or shim subpath imports.
- **Audit gate metrics**: 0 `__brand:` decls in `packages/chat-surface/src/` (canonical site only — `packages/api-types/src/brands.ts`); 1 `formatRelativeTime` in `packages/chat-surface/src/util/time.ts`; TU-1 CI guard clean on 527 files; 1453 backend / 184 facade / 2112+ ai-backend / 1211 frontend / 1201+ chat-surface tests pass.
