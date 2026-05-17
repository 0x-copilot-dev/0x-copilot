// Cross-destination reference types — the ONE shape every destination uses
// when it points at something living in another destination.
//
// Source: cross-audit.md §1.1 (binding 2026-05-17). Every destination
// previously invented its own link shape (`thread_id` strings, custom
// `TodoSource`, ad-hoc `SourceRef`); they all converge here. Drift from
// this file is a bug — converge the consumer or extend `ItemKind`.
//
// Companion `<ItemLink>` component + resolver registry live in
// `packages/chat-surface/src/refs/` (see cross-audit §3.3).

import type {
  AgentId,
  ApprovalId,
  ConnectorId,
  ConversationId,
  InboxItemId,
  LibraryDatasetId,
  LibraryFileId,
  LibraryPageId,
  MeetingExternalId,
  MemoryItemId,
  ProjectId,
  RoutineId,
  RunId,
  SkillId,
  SubagentId,
  TodoId,
  ToolId,
  ToolResultId,
  UserId,
} from "./brands";

/**
 * Every cross-destination ref kind. Discriminator for `ItemRef`.
 *
 * If you add a kind, you MUST also add the corresponding `ItemRef` branch
 * below (exhaustiveness check enforced at consumer call sites via
 * `switch (ref.kind)`).
 */
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
  | "person"
  | "memory"
  | "routine"
  | "approval"
  | "meeting_external";

/**
 * Discriminated union; one branch per `ItemKind`. The `id` field on each
 * branch is the correctly branded ID type, so consumers can write
 * `switch (ref.kind) { case "chat": ref.id /* ConversationId *\/ }`
 * with full type narrowing.
 */
export type ItemRef =
  | { readonly kind: "chat"; readonly id: ConversationId }
  | { readonly kind: "run"; readonly id: RunId }
  | { readonly kind: "subagent"; readonly id: SubagentId }
  | { readonly kind: "tool_result"; readonly id: ToolResultId }
  | { readonly kind: "todo"; readonly id: TodoId }
  | { readonly kind: "inbox_item"; readonly id: InboxItemId }
  | { readonly kind: "project"; readonly id: ProjectId }
  | { readonly kind: "library_file"; readonly id: LibraryFileId }
  | { readonly kind: "library_page"; readonly id: LibraryPageId }
  | { readonly kind: "library_dataset"; readonly id: LibraryDatasetId }
  | { readonly kind: "agent"; readonly id: AgentId }
  | { readonly kind: "tool"; readonly id: ToolId }
  | { readonly kind: "skill"; readonly id: SkillId }
  | { readonly kind: "connector"; readonly id: ConnectorId }
  | { readonly kind: "person"; readonly id: UserId }
  | { readonly kind: "memory"; readonly id: MemoryItemId }
  | { readonly kind: "routine"; readonly id: RoutineId }
  | { readonly kind: "approval"; readonly id: ApprovalId }
  | { readonly kind: "meeting_external"; readonly id: MeetingExternalId };

/**
 * Display-side denormalization. Carries pre-fetched label/icon hints so
 * the host can render a list entry without resolving every ref through
 * the registry on first paint. NEVER trusted as source of truth — the
 * canonical resolve happens on item open via the `<ItemLink>` registry
 * (cross-audit §3.3).
 */
export interface ItemRefSnapshot {
  readonly ref: ItemRef;
  readonly display_label?: string;
  readonly display_icon_hint?: string;
}

/**
 * Partial-failure wrapper for endpoints that aggregate upstream calls
 * (Home morning briefing, Routines health rollup, etc). Non-aggregation
 * endpoints (Todos list, Inbox list) do NOT wrap.
 *
 * Source: cross-audit.md §2.3.
 */
export interface SectionResult<T> {
  readonly status: "ok" | "error" | "unavailable";
  readonly data?: T;
  /** Human-readable, frontend-displayable. Never an exception trace. */
  readonly error?: string;
  /** Optional backoff hint when `status === "error"`. */
  readonly retry_after_ms?: number;
}
