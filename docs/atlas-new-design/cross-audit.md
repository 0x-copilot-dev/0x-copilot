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

Existing types (e.g., `ConversationId` in `chat-surface/destinations/home`) are removed; everyone imports from `@enterprise-search/api-types`.

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
import type { ItemKind, ItemRef } from "@enterprise-search/api-types";

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
