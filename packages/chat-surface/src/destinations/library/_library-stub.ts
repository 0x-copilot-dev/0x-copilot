// chat-surface Library adapter shape (transitional; orchestrator rewires
// at merge to `@0x-copilot/api-types/library`).
//
// Phase 7 has parallel wave-agents working off the same shape conventions
// as the canonical library-prd.md §3 + §4.1:
//   - P7-A* (api-types + backend wire) will own canonical
//     `packages/api-types/src/library.ts`.
//   - P7-B1 (this shell), P7-B2 (detail + per-kind preview + page editor),
//     P7-C (host wiring) ship later.
//
// Until P7-A1 lands, this stub is the local view-model contract every UI
// sub-agent consumes. Naming + discriminators match the canonical site
// so merge-time rewire is a pure import swap.
//
// Every import of this stub should be marked
// `TODO(merge): rewire to "@0x-copilot/api-types"` so the
// orchestrator's rewrite script can find them.

import type {
  ConnectorId,
  ConversationId,
  LibraryDatasetId,
  LibraryFileId,
  LibraryPageId,
  ProjectId,
  RunId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

// ---- §4.1 Discriminators ---------------------------------------------------

/** Kind of Library item. Source: library-prd §4.1. */
export type LibraryItemKind = "file" | "page" | "dataset";

/** Per-kind file shape. Source: library-prd §4.1. */
export type LibraryFileKind =
  | "doc"
  | "image"
  | "pdf"
  | "sheet"
  | "slide"
  | "other";

/** Indexing lifecycle. Source: library-prd §4.1. */
export type LibraryIndexStatus =
  | "pending"
  | "indexing"
  | "indexed"
  | "failed"
  | "skipped";

/** Where the item came from. Source: library-prd §4.1. */
export type LibrarySourceKind = "user_upload" | "agent_save" | "connector_sync";

/**
 * Tagged provenance — same shape as library-prd §4.1's `LibrarySource`,
 * shrunk to the fields the shell renders (ids only; the host expands
 * them into <ItemLink> chips). Connector / chat / run ids are surfaced
 * so the panel's source filter and the detail's "originated from" chip
 * both pull from the same place.
 */
export type LibrarySource =
  | { readonly kind: "user_upload"; readonly uploaded_by: UserId }
  | {
      readonly kind: "agent_save";
      readonly chat_id: ConversationId;
      readonly run_id: RunId;
      readonly message_id: string;
      readonly tool_call_id?: string;
    }
  | {
      readonly kind: "connector_sync";
      readonly connector_id: ConnectorId;
      readonly external_id: string;
      readonly external_url?: string;
    };

// ---- §4.1 Library summary --------------------------------------------------

/**
 * Lightweight projection used by list endpoints. Mirrors library-prd
 * §4.1 — only the fields the shell renders. The shell trusts the
 * server-projected `display_label` / `subtitle` / `thumbnail_url` so it
 * never reaches into kind-specific payloads to format.
 *
 * The discriminator is `kind` (file / page / dataset); branded id types
 * surface per-branch so consumers can switch on `kind` and forward the
 * id to `<ItemLink>` without casting.
 */
export type LibraryItemSummary =
  | LibraryFileSummary
  | LibraryPageSummary
  | LibraryDatasetSummary;

interface LibraryItemBase {
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly owner_display_name?: string;
  readonly project_id: ProjectId | null;
  readonly project_name?: string;
  /** Display title — server-projected so the shell never has to compute it. */
  readonly name: string;
  /** Sub-line beneath the name (size for files, row-count for datasets,
   *  first-line preview for pages). Server-projected; ≤ 120 chars. */
  readonly subtitle?: string;
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
  /** Optional thumbnail URL (server-signed). Files only; null for pages /
   *  datasets which render kind-glyph instead. */
  readonly thumbnail_url?: string | null;
}

export interface LibraryFileSummary extends LibraryItemBase {
  readonly kind: "file";
  readonly id: LibraryFileId;
  readonly file_kind: LibraryFileKind;
  readonly mime: string;
  readonly size_bytes: number;
}

export interface LibraryPageSummary extends LibraryItemBase {
  readonly kind: "page";
  readonly id: LibraryPageId;
  readonly version: number;
}

export interface LibraryDatasetSummary extends LibraryItemBase {
  readonly kind: "dataset";
  readonly id: LibraryDatasetId;
  readonly row_count: number;
  readonly column_count: number;
  readonly format: "parquet" | "csv" | "jsonl";
}

// ---- §3.2 / §3.3 Filter axes ----------------------------------------------

/**
 * Kind filter slug used by the destination's FilterTabs and the panel's
 * mirror chips. Matches library-prd §3.1 routing convention
 * (`{ view: "files" | "pages" | "datasets" | "all" }`).
 *
 * "all" is the destination's default — recognition-first browsing across
 * every kind.
 */
export type LibraryKindFilterSlug = "all" | "files" | "pages" | "datasets";

/** Per-kind counts; driven by the host so chips never disagree with rows. */
export type LibraryKindFilterCounts = Readonly<
  Record<LibraryKindFilterSlug, number>
>;

/**
 * View-toggle slug — CardGrid vs DocList. CardGrid is default
 * (recognition-first; library-prd §3.2.1); DocList is opt-in for
 * scanning > 200 items.
 */
export type LibraryViewMode = "cards" | "list";

/** Sort allowlist surfaced by the panel. Source: library-prd §4.4. */
export type LibrarySortSlug =
  | "updated_at:desc"
  | "created_at:desc"
  | "name:asc"
  | "name:desc"
  | "last_accessed_at:desc"
  | "size_bytes:desc";

// ---- §3.6 SaveToLibrary widget --------------------------------------------

/**
 * Default kind suggestion supplied by the originating surface. Source:
 * library-prd §3.6.1.
 *
 *  - Tool result with structured (JSON / table) output → "dataset"
 *  - Tool result with binary output → "file"
 *  - Tool result with text-shaped output / agent message → "page"
 *  - Chat thread pin summary → "page"
 *  - Run output artifact (depends on shape; caller picks) → any
 */
export type SaveToLibraryDefaultKind = LibraryItemKind;

/** Where the popover was launched from. Drives telemetry `from=` axis
 *  (library-prd §11) and the form's "Source preview" subtitle. */
export type SaveToLibrarySource =
  | "chat_tool_result"
  | "chat_agent_msg"
  | "chat_thread_pin"
  | "run_completion"
  | "routine_output";
