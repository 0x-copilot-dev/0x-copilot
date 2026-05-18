// Library destination (Phase 7) — CRUD + canonical wire contract.
//
// Source: docs/atlas-new-design/destinations/library-prd.md §4 (wire
// contracts), §5 (storage), §7 (ACL), and docs/atlas-new-design/cross-audit.md
// §1.1 (ItemRef-linked source refs), §1.3 (project-scoped ACL — owner
// writes, project-member reads, tenant admin compliance reads,
// 404-not-403), §1.5 (multi-value OR filter axes), §2.1 (branded IDs).
//
// Scope of P7-A1 (this declaration site):
//   - The full kind-agnostic wire union: LibraryFile / LibraryPage /
//     LibraryDataset, joined as the LibraryItem discriminated union.
//   - List + Get + Page-create + PATCH metadata + DELETE wire shapes.
//   - File upload finalize, dataset ingest finalize, signed-URL preview /
//     download, embeddings, search, citations, version history, pin —
//     all owned by P7-A2 (blob handling) and P7-A3 (search) and re-export
//     additively from this file when those phases land.
//
// Wire-only file: no business logic, no HTTP client, no view models.
// Servers own routes; this package mirrors public payloads exactly as
// the facade serves them. Internal `/internal/v1/*` producer payloads
// are NOT mirrored here — those contracts live behind the service
// boundary.

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
} from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Primitive enums
// ---------------------------------------------------------------------------

/**
 * Three storage kinds that share the unified Library destination. The
 * All view subsumes the kind — recognition-first, kind-specific cards
 * render uniformly. Drift between this union and the server CHECK
 * constraint is a bug.
 */
export type LibraryItemKind = "file" | "page" | "dataset";

/**
 * Where the item came from. The discriminator is `kind`; payload shape
 * varies per kind so the UI can attribute the row precisely (cross-audit
 * §1.1 — denormalized display fields are stamped at producer write time
 * and refreshed on rewrite, but the canonical resolver still goes through
 * the source itself, e.g. `chat_id` resolves via ItemRef).
 */
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

/** Tag the source kind in filter[source.kind]=… without unwrapping the payload. */
export type LibrarySourceKind = LibrarySource["kind"];

/**
 * Indexer lifecycle. Files / pages / datasets are queued for embedding
 * + tsvector update by `library_indexer.py`. P7-A3 owns the indexer; the
 * status is read by P7-A1 list / get routes but not driven here.
 */
export type LibraryIndexStatus =
  | "pending"
  | "indexing"
  | "indexed"
  | "failed"
  | "skipped";

/**
 * Coarse render bucket for the file row (icon + thumbnail strategy). The
 * server derives this from `mime` at write time so the FE never has to
 * parse mime strings to choose an icon.
 */
export type LibraryFileKind =
  | "doc"
  | "image"
  | "pdf"
  | "sheet"
  | "slide"
  | "other";

/** Dataset column-type primitive set; matches the indexer's schema inference. */
export type LibraryDatasetColumnType =
  | "string"
  | "integer"
  | "float"
  | "boolean"
  | "date"
  | "datetime"
  | "json"
  | "binary";

/** Dataset blob format on disk; canonical = parquet. */
export type LibraryDatasetFormat = "parquet" | "csv" | "jsonl";

// ---------------------------------------------------------------------------
// Item shapes
// ---------------------------------------------------------------------------

/** One column in a dataset's schema. Up to 5 sample values per column for
 * preview-without-download (library-prd §4.1). */
export interface LibraryDatasetColumnSpec {
  readonly name: string;
  readonly type: LibraryDatasetColumnType;
  readonly nullable: boolean;
  readonly sample_values?: ReadonlyArray<string>;
}

/**
 * One file row. Bytes live in object store; this is metadata + opaque
 * `blob_ref`. Clients NEVER see object-store URLs directly — the
 * server returns signed GET URLs via `/preview` / `/download` (P7-A2).
 *
 * `tags` and `index_status` are observable but mutated by separate
 * routes (PATCH metadata / `library_indexer`).
 */
export interface LibraryFile {
  readonly id: LibraryFileId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "file";
  readonly file_kind: LibraryFileKind;
  readonly name: string;
  readonly mime: string;
  readonly size_bytes: number;
  readonly blob_ref: string;
  readonly thumbnail_blob_ref: string | null;
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly checksum_sha256: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

/**
 * One markdown page (knowledge card). Body is canonical content stored
 * in the same row; up to 1 MB enforced at write time (library-prd §5.5).
 *
 * `version` + `version_etag` support optimistic concurrency on body
 * edits — clients pass `If-Match: <version_etag>` on PATCH. Server
 * bumps both on every successful save and appends a row to
 * `library_page_versions` (history surface owned by P7-A2).
 */
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

/**
 * One dataset row (tabular data with a schema). Bytes (Parquet/CSV/JSONL)
 * live in object store; this row is the metadata + inferred schema.
 *
 * `row_count` / `size_bytes` populated post-finalize by the indexer
 * (P7-A2 — until then both can be 0 in `pending` state).
 */
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
  readonly checksum_sha256: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

/**
 * Kind-agnostic discriminated union — list endpoints render All by
 * default, kind-specific filters narrow. The discriminator is `kind`;
 * the FE selects the card by `kind` (recognition-first UI).
 */
export type LibraryItem = LibraryFile | LibraryPage | LibraryDataset;

// ---------------------------------------------------------------------------
// Response envelopes
// ---------------------------------------------------------------------------

/**
 * List page. `counts_by_kind` is computed once over the filtered set so
 * the destination header can show "Files 12 · Pages 4 · Datasets 1"
 * without a second round-trip (library-prd §4.1).
 */
export interface LibraryListResponse {
  readonly items: ReadonlyArray<LibraryItem>;
  readonly next_cursor: string | null;
  readonly counts_by_kind: {
    readonly file: number;
    readonly page: number;
    readonly dataset: number;
  };
}

// ---------------------------------------------------------------------------
// Request shapes
// ---------------------------------------------------------------------------

/**
 * Create-page request — markdown body up to 1 MB. P7-A1 ships this as
 * the only create route; file + dataset creation flow through the
 * signed-URL initiate-then-finalize handshake owned by P7-A2.
 */
export interface LibraryPageCreateRequest {
  readonly title: string;
  readonly markdown: string;
  readonly project_id?: ProjectId | null;
  readonly tags?: ReadonlyArray<string>;
  /**
   * Optional — defaults to `{ kind: "user_upload", uploaded_by: <caller> }`
   * server-side when omitted. Service-token producers (Routines / agent
   * saves) supply the richer `agent_save` payload via the internal
   * route, not this public one.
   */
  readonly source?: LibrarySource;
}

// ---------------------------------------------------------------------------
// Hybrid search — P7.5-A4 (BM25 + pgvector + RRF + optional re-rank).
// Source: docs/atlas-new-design/destinations/library-prd.md §6
// (retrieval pipeline). Cross-audit §5.2 (SSE convention: Last-Event-ID
// resume + 30s heartbeat) for the streaming variant.
// ---------------------------------------------------------------------------

/**
 * Which leg of the pipeline produced hits.
 *
 * - `bm25_only` — vector leg returned nothing (no embeddings configured
 *   for this tenant, or no embedded rows match);
 * - `vector_only` — BM25 returned nothing (rare; mostly a degenerate
 *   query case);
 * - `hybrid` — both legs contributed at least one hit, RRF fused them.
 */
export type LibrarySearchStrategy = "bm25_only" | "vector_only" | "hybrid";

/** Where the BM25 leg's match landed — drives the small "Matched in: …"
 *  chip under the snippet (library-prd §6.2). */
export type LibrarySearchMatchedIn = "title" | "content" | "tag";

/**
 * One hit. `ref` is the canonical cross-destination ItemRef so any
 * `<ItemLink>` resolver can navigate to the item; the denormalized
 * `title` / `project_id` / `owner_user_id` / `updated_at` fields are
 * a server-stamped snapshot for first-paint rendering (cross-audit
 * §1.1: display fields are never trusted, source of truth is the ref).
 */
export interface LibrarySearchHit {
  readonly ref: ItemRef;
  readonly score: number;
  readonly excerpt: string;
  readonly matched_in: LibrarySearchMatchedIn;
  readonly kind: LibraryItemKind;
  readonly title: string;
  readonly project_id: ProjectId | null;
  readonly owner_user_id: UserId;
  readonly updated_at: string;
}

/** One-shot search response — `GET /v1/library/search`. */
export interface LibrarySearchResponse {
  readonly hits: ReadonlyArray<LibrarySearchHit>;
  /** Total readable hits, post-ACL — useful for "showing X of Y". */
  readonly total: number;
  /** Wall-clock pipeline time at the route layer. */
  readonly took_ms: number;
  readonly strategy: LibrarySearchStrategy;
}

/**
 * One envelope on the SSE stream for `GET /v1/library/search/stream`.
 * Mirrors the four ordered events emitted by the server (cross-audit
 * §5.2 — `event:` lines are the discriminator). Heartbeats are
 * `: keepalive\n\n` SSE comment frames (no event/data); EventSource
 * silently ignores them.
 */
export type LibrarySearchStreamEnvelope =
  | LibrarySearchLegEnvelope
  | LibrarySearchRerankedEnvelope
  | LibrarySearchCompleteEnvelope
  | LibrarySearchErrorEnvelope;

interface LibrarySearchLegEnvelopeBase {
  readonly correlation_id: string;
  readonly hit_count: number;
  readonly hits: ReadonlyArray<{
    readonly ref: ItemRef;
    readonly score: number;
  }>;
  readonly elapsed_ms: number;
}

/** `event: library.search_bm25_result` / `event: library.search_vector_result`. */
export interface LibrarySearchLegEnvelope extends LibrarySearchLegEnvelopeBase {
  readonly leg: "bm25" | "vector";
}

/** `event: library.search_reranked`. */
export interface LibrarySearchRerankedEnvelope extends LibrarySearchLegEnvelopeBase {
  readonly leg: "reranked";
}

/** `event: library.search_complete`. Carries the final hit list. */
export interface LibrarySearchCompleteEnvelope {
  readonly correlation_id: string;
  readonly hits: ReadonlyArray<LibrarySearchHit>;
  readonly total: number;
  readonly took_ms: number;
  readonly strategy: LibrarySearchStrategy;
  readonly emitted_at: string;
}

/** `event: library.search_error` — server-side pipeline failure. */
export interface LibrarySearchErrorEnvelope {
  readonly correlation_id: string;
  readonly code: "library_search_failed";
}

/**
 * Metadata PATCH — applies to all three kinds. Body fields are kind-
 * specific (only `markdown` is page-only; only `name` is file/dataset-
 * only; `title` is page-only). The server enforces "owner_user_id only"
 * for every metadata mutation per library-prd §7.2.
 */
export interface LibraryItemPatchRequest {
  /** Files + datasets: rename. Ignored on pages (use `title`). */
  readonly name?: string;
  /** Pages: rename. Ignored on files/datasets. */
  readonly title?: string;
  /** Pages: body edit; requires `If-Match: <version_etag>` header. */
  readonly markdown?: string;
  /** Tags replace (not merge); empty array clears. */
  readonly tags?: ReadonlyArray<string>;
  /** Re-file to a different project, or detach with explicit `null`. */
  readonly project_id?: ProjectId | null;
  /** Datasets only: description edit. */
  readonly description?: string | null;
}
