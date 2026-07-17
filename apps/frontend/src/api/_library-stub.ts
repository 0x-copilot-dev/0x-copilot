// Local stub for the Phase 7 Library wire contract.
//
// The canonical types live in `@0x-copilot/api-types`
// (`packages/api-types/src/library.ts`), authored by the parallel
// Phase 7 P7-A backend-types agents. This frontend wave (P7-C) runs in
// parallel against the same sub-PRD spec and cannot import a type that
// is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/library-prd.md` §4.1.
//
// `LibraryFileId`, `LibraryPageId`, `LibraryDatasetId`, `LibraryItemId`
// (and the cross-destination `ItemRef` / `ItemKind` union, `ProjectId`,
// `TenantId`, `UserId`, `ConnectorId`, `ConversationId`, `RunId`) already
// live in `@0x-copilot/api-types` — re-export from there so the
// `<ItemLink>` registry stays a single source of truth even before the
// rest of the Library contract merges.
//
// TODO(merge): delete this file. Replace every `_library-stub` import
// with `@0x-copilot/api-types` once P7-A's
// `packages/api-types/src/library.ts` lands on main.

import type {
  ConnectorId,
  ConversationId,
  ItemKind,
  ItemRef,
  LibraryDatasetId,
  LibraryEntityId,
  LibraryFileId,
  LibraryItemId,
  LibraryPageId,
  ProjectId,
  RunId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

export type {
  ConnectorId,
  ConversationId,
  ItemKind,
  ItemRef,
  LibraryDatasetId,
  LibraryEntityId,
  LibraryFileId,
  LibraryItemId,
  LibraryPageId,
  ProjectId,
  RunId,
  TenantId,
  UserId,
};

// ===========================================================================
// Enums + leaf types (§4.1)
// ===========================================================================

export type LibraryKind = "file" | "page" | "dataset";

export type LibraryIndexStatus =
  | "pending"
  | "indexing"
  | "indexed"
  | "failed"
  | "skipped";

export type LibraryFileMime =
  | "application/pdf"
  | "image/png"
  | "image/jpeg"
  | "image/gif"
  | "image/webp"
  | "image/svg+xml"
  | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  | "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  | "application/msword"
  | "application/vnd.ms-excel"
  | "application/vnd.ms-powerpoint"
  | "text/plain"
  | "text/markdown"
  | "text/csv"
  | "application/json"
  | "application/octet-stream";

export type LibraryFileKind =
  | "doc"
  | "image"
  | "pdf"
  | "sheet"
  | "slide"
  | "other";

export type LibraryDatasetFormat = "parquet" | "csv" | "jsonl";

// ===========================================================================
// LibrarySource discriminated union (§4.1)
// ===========================================================================

export type LibrarySource =
  | { readonly kind: "user_upload"; readonly uploaded_by: UserId }
  | {
      readonly kind: "agent_save";
      readonly chat_id: ConversationId;
      readonly run_id: RunId;
      readonly message_id: string;
      readonly tool_call_id?: string;
      readonly range?: { readonly start: number; readonly end: number };
    }
  | {
      readonly kind: "connector_sync";
      readonly connector_id: ConnectorId;
      readonly external_id: string;
      readonly external_url?: string;
    };

export type LibrarySourceKind = LibrarySource["kind"];

// ===========================================================================
// Items (§4.1)
// ===========================================================================

export interface LibraryFile {
  readonly id: LibraryFileId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "file";
  readonly file_kind: LibraryFileKind;
  readonly name: string;
  readonly mime: LibraryFileMime;
  readonly size_bytes: number;
  readonly blob_ref: string;
  readonly thumbnail_blob_ref: string | null;
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly checksum_sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

export interface LibraryPage {
  readonly id: LibraryPageId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "page";
  readonly title: string;
  readonly markdown: string;
  readonly version: number;
  readonly version_etag: string;
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

export interface LibraryDatasetColumnSpec {
  readonly name: string;
  readonly type:
    | "string"
    | "integer"
    | "float"
    | "boolean"
    | "date"
    | "datetime"
    | "json"
    | "binary";
  readonly nullable: boolean;
  readonly sample_values?: ReadonlyArray<string>;
}

export interface LibraryDataset {
  readonly id: LibraryDatasetId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "dataset";
  readonly name: string;
  readonly description: string | null;
  readonly schema: ReadonlyArray<LibraryDatasetColumnSpec>;
  readonly row_count: number;
  readonly size_bytes: number;
  readonly blob_ref: string;
  readonly format: LibraryDatasetFormat;
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly checksum_sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

/** Discriminated union for kind-agnostic list endpoints. */
export type LibraryItem = LibraryFile | LibraryPage | LibraryDataset;

// ===========================================================================
// List + search (§4.1, §4.4)
// ===========================================================================

export interface LibraryListResponse {
  readonly items: ReadonlyArray<LibraryItem>;
  readonly next_cursor: string | null;
  readonly counts_by_kind: {
    readonly file: number;
    readonly page: number;
    readonly dataset: number;
  };
}

export interface LibrarySearchHit {
  readonly ref: ItemRef;
  readonly snippet: string;
  readonly score: number;
  readonly score_breakdown: {
    readonly bm25: number | null;
    readonly vector_cosine: number | null;
    readonly reranker: number | null;
  };
  readonly source: LibrarySource;
  readonly project_id: ProjectId | null;
  readonly updated_at: string;
}

export interface LibrarySearchResponse {
  readonly query: string;
  readonly hits: ReadonlyArray<LibrarySearchHit>;
  readonly took_ms: number;
  readonly retrieval_strategy: "bm25_only" | "vector_only" | "hybrid";
  readonly reranker_used: boolean;
  readonly partial: {
    readonly status: "ok" | "partial" | "failed";
    readonly missing: ReadonlyArray<string>;
    readonly details: {
      readonly bm25_complete: boolean;
      readonly vector_complete: boolean;
      readonly rerank_complete: boolean;
    };
  };
}

export interface LibraryRecentsResponse {
  readonly recently_saved: ReadonlyArray<LibraryItem>;
  readonly recently_accessed: ReadonlyArray<LibraryItem>;
}

export type LibrarySortKey =
  | "updated_at:desc"
  | "created_at:desc"
  | "name:asc"
  | "name:desc"
  | "last_accessed_at:desc"
  | "size_bytes:desc";

export interface ListLibraryFilters {
  readonly kind?: LibraryKind;
  readonly project_id?: ProjectId;
  readonly source_kind?: LibrarySourceKind;
  readonly tag?: string;
  readonly index_status?: LibraryIndexStatus;
  readonly owner_user_id?: UserId;
  readonly file_kind?: LibraryFileKind;
}

// ===========================================================================
// Upload / create / mutate (§4.1, §4.2)
// ===========================================================================

export interface LibraryUploadInitRequest {
  readonly name: string;
  readonly mime: LibraryFileMime;
  readonly size_bytes: number;
  readonly checksum_sha256?: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
}

export interface LibraryUploadInitResponse {
  readonly file_id: LibraryFileId;
  readonly upload_url: string;
  readonly upload_headers: Record<string, string>;
  readonly expires_at: string;
}

export interface LibraryPageCreateRequest {
  readonly title: string;
  readonly markdown: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
  readonly source?: LibrarySource;
}

export interface LibraryPagePatchRequest {
  readonly title?: string;
  readonly markdown?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

export interface LibraryItemPatchRequest {
  readonly name?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

export interface LibraryDatasetCreateRequest {
  readonly name: string;
  readonly description?: string;
  readonly format: LibraryDatasetFormat;
  readonly size_bytes: number;
  readonly checksum_sha256?: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
  readonly source?: LibrarySource;
}

export interface LibraryDatasetIngestResponse {
  readonly dataset_id: LibraryDatasetId;
  readonly upload_url: string;
  readonly upload_headers: Record<string, string>;
  readonly expires_at: string;
}

export interface LibraryPreviewResponse {
  readonly kind: LibraryKind;
  readonly file_preview?: {
    readonly thumbnail_signed_url: string;
    readonly page_count: number;
    readonly first_page_signed_url: string;
  };
  readonly page_preview?: {
    readonly title: string;
    readonly markdown_excerpt: string;
  };
  readonly dataset_preview?: {
    readonly schema: ReadonlyArray<LibraryDatasetColumnSpec>;
    readonly rows: ReadonlyArray<
      ReadonlyArray<string | number | boolean | null>
    >;
    readonly total_rows: number;
  };
}

export interface LibraryDownloadResponse {
  readonly signed_url: string;
  readonly expires_at: string;
}

export interface LibraryVersion {
  readonly version: number;
  readonly etag: string;
  readonly created_at: string;
  readonly created_by: UserId;
  readonly diff_summary?: string;
}

export interface LibraryVersionListResponse {
  readonly items: ReadonlyArray<LibraryVersion>;
}

export interface LibraryVersionResponse {
  readonly version: number;
  readonly etag: string;
  readonly markdown: string;
  readonly created_at: string;
  readonly created_by: UserId;
}

export interface LibrarySearchRequest {
  readonly query: string;
  readonly filters?: ListLibraryFilters;
  readonly max_hits?: number;
  readonly rerank?: boolean;
}

// ===========================================================================
// SSE envelope (§4.2 — durable library channel)
// ===========================================================================

export type LibraryStreamEventType =
  | "library.item_created"
  | "library.item_indexed"
  | "library.item_index_failed"
  | "library.item_updated"
  | "library.item_deleted";

/** Payload union — concrete LibraryItem rows for create/update/index events,
 *  a small descriptor for delete events. */
export type LibraryStreamPayload =
  | LibraryItem
  | {
      readonly item_id: LibraryEntityId;
      readonly kind?: LibraryKind;
      readonly error?: string;
    };

export interface LibraryStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: LibraryStreamEventType;
  readonly item_id: LibraryEntityId;
  readonly payload: LibraryStreamPayload;
  readonly emitted_at: string;
}
