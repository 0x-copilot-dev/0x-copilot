// Branded ID types — every entity ID in the public API surface.
//
// Source: cross-audit.md §2.1 (binding 2026-05-17). This file is the
// SINGLE declaration site for every brand. Re-exported from
// `packages/api-types/src/index.ts`; every consumer (frontend, chat-surface,
// destinations) imports from `@0x-copilot/api-types`.
//
// Branding pattern: a `string & { readonly __brand: "<Name>" }` intersection
// produces a structurally-unique type at compile-time without runtime cost.
// Plain `string` values are not assignable; consumers must `as` at trust
// boundaries (typically right after parsing a server response). Cross-brand
// assignment (e.g. `RunId` → `ConversationId`) is rejected by the compiler.

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

/**
 * Free-form identifier for a `meeting_external` ItemRef branch. Calendar
 * events come from third-party connectors and don't have a tenant-local
 * entity row — the identifier is the upstream provider's event id (plus
 * connector id for disambiguation). Branded for cross-kind hygiene.
 */
export type MeetingExternalId = string & {
  readonly __brand: "MeetingExternalId";
};

/**
 * `tool_result` ItemRef branch — points to a single step inside a run.
 * The id is the runtime's `(run_id, step_id)` composite, serialized as
 * `"<run_id>:<step_id>"` so the brand stays a plain string at the wire.
 * Consumers split on `":"` when they need the components.
 */
export type ToolResultId = string & { readonly __brand: "ToolResultId" };

/**
 * `library_file` / `library_page` / `library_dataset` already have dedicated
 * brands above; this alias collects them when a generic library reference is
 * needed (e.g. an `ItemRef` union branch).
 */
export type LibraryEntityId = LibraryFileId | LibraryPageId | LibraryDatasetId;

/**
 * Pre-cross-audit Library destination concept: a generic library "item" id
 * (the union of adapter / result / knowledge card rows the existing
 * `LibraryDestination` renders). Distinct from `LibraryFileId` /
 * `LibraryPageId` / `LibraryDatasetId` which are the canonical Wave 6
 * Library entity ids. Hoisted here so the chat-surface tree has zero
 * `__brand:` declaration sites (Phase 0.5 DRY rule).
 *
 * TODO(Wave 6 Library): reconcile with the canonical `LibraryFileId`/
 * `LibraryPageId`/`LibraryDatasetId` brands when the Library destination
 * is rewritten — likely one of those three replaces this alias.
 */
export type LibraryItemId = string & { readonly __brand: "LibraryItemId" };

// P5 Routines — webhook trigger id (per-trigger; one routine can have many).
export type TriggerId = string & { readonly __brand: "TriggerId" };

// P3 Todos — recurring series id (links all materialized instances).
export type TodoSeriesId = string & { readonly __brand: "TodoSeriesId" };

// P6.5 Projects — template id (saved project shape for cloning).
export type ProjectTemplateId = string & {
  readonly __brand: "ProjectTemplateId";
};

// ConnectorSlug already lives in ./projects.ts (P6.5) — single source of
// truth, kept there for now. Could be hoisted in a future cleanup.
