// Typed wrappers for the Phase 7 Library destination.
//
// Surfaces (sub-PRD §4.2):
//   1. `fetchLibrary(identity, opts)`                — GET /v1/library.
//   2. `fetchLibraryRecents(identity)`               — GET /v1/library/recents.
//   3. `fetchLibraryItem(identity, id)`              — GET /v1/library/{id}.
//   4. `initLibraryUpload / putLibraryBlob / finalizeLibraryFile`
//                                                    — three-stage signed-URL
//                                                      upload (sub-PRD §4.2).
//   5. `createLibraryPage / patchLibraryItem / patchLibraryPageBody`
//                                                    — page create + body edit.
//   6. `initLibraryDataset / finalizeLibraryDataset` — dataset ingest.
//   7. `deleteLibraryItem`                           — soft delete.
//   8. `searchLibrary`                               — hybrid search.
//   9. `fetchLibraryPreview / fetchLibraryDownload`  — preview + signed GET URL.
//  10. `pinLibraryItem / unpinLibraryItem / citeLibraryItem`
//                                                    — viewer-relative pin +
//                                                      citation back-index.
//  11. `fetchLibraryPageVersions / fetchLibraryPageVersion`
//                                                    — page version history.
//  12. `streamLibraryEvents({...})`                  — durable SSE channel.
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Bytes flow DIRECT to the object-store signed URL via `putLibraryBlob`
// — they do NOT proxy through the facade. The facade only mediates the
// grant (init) and the finalize handshake. This is critical to keep
// large uploads off the API hot path (sub-PRD §5.5).
//
// Wire types live in `./_library-stub` until P7-A's
// `@enterprise-search/api-types/src/library.ts` lands on main.
//
// TODO(merge): swap every `./_library-stub` import for
// `@enterprise-search/api-types`.

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";
import type {
  LibraryDatasetCreateRequest,
  LibraryDatasetId,
  LibraryDatasetIngestResponse,
  LibraryDownloadResponse,
  LibraryEntityId,
  LibraryFileId,
  LibraryItem,
  LibraryItemPatchRequest,
  LibraryListResponse,
  LibraryPageCreateRequest,
  LibraryPageId,
  LibraryPagePatchRequest,
  LibraryPreviewResponse,
  LibraryRecentsResponse,
  LibrarySearchRequest,
  LibrarySearchResponse,
  LibrarySortKey,
  LibraryStreamEnvelope,
  LibraryUploadInitRequest,
  LibraryUploadInitResponse,
  LibraryVersionListResponse,
  LibraryVersionResponse,
  ListLibraryFilters,
} from "./_library-stub";

const SSE_EVENT_NAME = "library_event";

// ===========================================================================
// LIST + RECENTS
// ===========================================================================

export interface FetchLibraryOptions {
  readonly filters?: ListLibraryFilters;
  readonly q?: string;
  readonly sort?: LibrarySortKey;
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/library with allowlisted filters + cursor pagination
 * (sub-PRD §4.2, §4.4, §8). Filter encoding mirrors the projects /
 * routines APIs — `filter[<axis>]=<value>` keys.
 */
export function fetchLibrary(
  identity: RequestIdentity,
  options: FetchLibraryOptions = {},
): Promise<LibraryListResponse> {
  return httpGet<LibraryListResponse>(
    "/v1/library",
    identity,
    encodeListParams(options),
  );
}

/** GET /v1/library/recents — destination header strip. */
export function fetchLibraryRecents(
  identity: RequestIdentity,
): Promise<LibraryRecentsResponse> {
  return httpGet<LibraryRecentsResponse>("/v1/library/recents", identity);
}

// ===========================================================================
// DETAIL
// ===========================================================================

/** GET /v1/library/{id}. 404 (not 403) on cross-tenant per sub-PRD §1.3. */
export function fetchLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<LibraryItem> {
  return httpGet<LibraryItem>(
    `/v1/library/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// UPLOAD FLOW — files (3 stages: init → PUT signed URL → finalize)
// ===========================================================================

/**
 * POST /v1/library/files — stage 1 of 3.
 *
 * Returns a signed PUT URL + a provisional `file_id`. The client then
 * `putLibraryBlob`s bytes directly to the signed URL (stage 2), and
 * `finalizeLibraryFile`s the row to enqueue indexing (stage 3).
 *
 * Bytes NEVER flow through the facade — only the metadata handshake
 * does. Sub-PRD §5.5: this keeps large uploads off the API hot path.
 */
export function initLibraryUpload(
  identity: RequestIdentity,
  body: LibraryUploadInitRequest,
): Promise<LibraryUploadInitResponse> {
  return httpPostQuery<LibraryUploadInitResponse>(
    "/v1/library/files",
    body,
    identity,
  );
}

/**
 * Stage 2 of 3: PUT raw bytes to the signed URL returned by
 * `initLibraryUpload`. This call goes to the object store (S3 /
 * S3-compatible), NOT to the facade — the URL is opaque and pre-signed.
 *
 * The transport singleton is intentionally not used here: signed URLs
 * carry their own auth (query-string signature), and any additional
 * bearer header would conflict with S3's signature validation. We
 * therefore use the global `fetch` directly.
 *
 * The `upload_headers` from the init response must be forwarded
 * verbatim — S3 / S3-compatible stores enforce `Content-Type` and
 * `x-amz-*` headers as part of the signed envelope; tampering breaks
 * the signature.
 */
export async function putLibraryBlob(
  uploadUrl: string,
  headers: Record<string, string>,
  body: Blob | ArrayBuffer | Uint8Array,
): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers,
    body: body as BodyInit,
  });
  if (!response.ok) {
    // The object store usually returns XML for errors; surface the
    // status so the caller can branch on 403 / 412 / 5xx.
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Upload to signed URL failed (${String(response.status)})${
        detail ? `: ${detail.slice(0, 200)}` : ""
      }`,
    );
  }
}

/**
 * POST /v1/library/files/{id}/finalize — stage 3 of 3.
 *
 * Server validates checksum + size against the init request and
 * enqueues indexing. Returns the now-realised `LibraryFile` row
 * (index_status = `pending` immediately after; the SSE channel
 * surfaces the `library.item_indexed` event later).
 */
export function finalizeLibraryFile(
  identity: RequestIdentity,
  id: LibraryFileId,
): Promise<LibraryItem> {
  return httpPostQuery<LibraryItem>(
    `/v1/library/files/${encodeURIComponent(id)}/finalize`,
    {},
    identity,
  );
}

// ===========================================================================
// UPLOAD FLOW — datasets (same 3-stage shape, distinct endpoints)
// ===========================================================================

/** POST /v1/library/datasets — stage 1 of 3 (datasets). */
export function initLibraryDataset(
  identity: RequestIdentity,
  body: LibraryDatasetCreateRequest,
): Promise<LibraryDatasetIngestResponse> {
  return httpPostQuery<LibraryDatasetIngestResponse>(
    "/v1/library/datasets",
    body,
    identity,
  );
}

/** POST /v1/library/datasets/{id}/finalize — stage 3 of 3 (datasets). */
export function finalizeLibraryDataset(
  identity: RequestIdentity,
  id: LibraryDatasetId,
): Promise<LibraryItem> {
  return httpPostQuery<LibraryItem>(
    `/v1/library/datasets/${encodeURIComponent(id)}/finalize`,
    {},
    identity,
  );
}

// ===========================================================================
// PAGES (markdown-bodied items — no signed-URL handshake)
// ===========================================================================

/** POST /v1/library/pages — create a markdown page. */
export function createLibraryPage(
  identity: RequestIdentity,
  body: LibraryPageCreateRequest,
): Promise<LibraryItem> {
  return httpPostQuery<LibraryItem>("/v1/library/pages", body, identity);
}

/**
 * PATCH /v1/library/pages/{id} with body + If-Match `version_etag`.
 *
 * Pages use optimistic-concurrency control: every save bumps `version`
 * + `version_etag`; subsequent writes must echo the latest etag in the
 * `If-Match` header. Server returns 412 (Precondition Failed) on
 * conflict — the caller refetches and reapplies.
 */
export function patchLibraryPageBody(
  identity: RequestIdentity,
  id: LibraryPageId,
  body: LibraryPagePatchRequest,
  versionEtag: string,
): Promise<LibraryItem> {
  // Pages still go through the standard facade PATCH plumbing; the
  // `If-Match` header rides via query-string sidecar — the transport
  // already merges identity params + extras and the backend reads
  // `If-Match` from the request header set the transport forwards.
  // We surface the etag here so the caller does not have to dig into
  // transport internals.
  return getAppTransport().request<LibraryItem>({
    method: "PATCH",
    path: `/v1/library/${encodeURIComponent(id)}`,
    query: {
      org_id: identity.orgId,
      user_id: identity.userId,
    },
    body,
    headers: { "if-match": versionEtag },
  });
}

// ===========================================================================
// METADATA MUTATIONS (rename / retag / move project)
// ===========================================================================

/** PATCH /v1/library/{id} — metadata mutation (name / tags / project_id). */
export function patchLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
  body: LibraryItemPatchRequest,
): Promise<LibraryItem> {
  return httpPatchQuery<LibraryItem>(
    `/v1/library/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

/** DELETE /v1/library/{id} — soft delete (sub-PRD §5.3 tombstone). */
export function deleteLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<void> {
  return httpDelete(`/v1/library/${encodeURIComponent(id)}`, identity);
}

// ===========================================================================
// SEARCH
// ===========================================================================

/**
 * POST /v1/library/search — hybrid (bm25 + vector + rerank) search.
 *
 * The streaming `/v1/library/search/stream` variant is reserved for
 * slow queries; this synchronous variant covers the typical case.
 */
export function searchLibrary(
  identity: RequestIdentity,
  body: LibrarySearchRequest,
): Promise<LibrarySearchResponse> {
  return httpPostQuery<LibrarySearchResponse>(
    "/v1/library/search",
    body,
    identity,
  );
}

// ===========================================================================
// PREVIEW + DOWNLOAD (signed-URL exit for raw bytes)
// ===========================================================================

/** GET /v1/library/{id}/preview — thumbnail / markdown excerpt / cell grid. */
export function fetchLibraryPreview(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<LibraryPreviewResponse> {
  return httpGet<LibraryPreviewResponse>(
    `/v1/library/${encodeURIComponent(id)}/preview`,
    identity,
  );
}

/**
 * GET /v1/library/{id}/download — returns a signed URL for the raw
 * bytes. Audited per sub-PRD §7.1; bytes go DIRECT from the signed URL
 * — the facade never proxies the bytes.
 */
export function fetchLibraryDownload(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<LibraryDownloadResponse> {
  return httpGet<LibraryDownloadResponse>(
    `/v1/library/${encodeURIComponent(id)}/download`,
    identity,
  );
}

// ===========================================================================
// PIN + CITE (viewer-relative + back-index)
// ===========================================================================

/** POST /v1/library/{id}/pin — idempotent pin toggle (server: add row). */
export function pinLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<LibraryItem> {
  return httpPostQuery<LibraryItem>(
    `/v1/library/${encodeURIComponent(id)}/pin`,
    {},
    identity,
  );
}

/** POST /v1/library/{id}/pin with `unpin: true` — idempotent unpin. */
export function unpinLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
): Promise<LibraryItem> {
  return httpPostQuery<LibraryItem>(
    `/v1/library/${encodeURIComponent(id)}/pin`,
    { unpin: true },
    identity,
  );
}

/**
 * POST /v1/library/{id}/cite — record an access for "cited in chat X".
 * Backend increments `last_accessed_at` and writes the citation
 * back-index row (sub-PRD §6.6).
 */
export function citeLibraryItem(
  identity: RequestIdentity,
  id: LibraryEntityId,
  body: {
    readonly conversation_id?: string;
    readonly run_id?: string;
    readonly message_id?: string;
  } = {},
): Promise<void> {
  return httpPostQuery<void>(
    `/v1/library/${encodeURIComponent(id)}/cite`,
    body,
    identity,
  );
}

// ===========================================================================
// PAGE VERSIONS
// ===========================================================================

/** GET /v1/library/pages/{id}/versions — list saves for a page. */
export function fetchLibraryPageVersions(
  identity: RequestIdentity,
  id: LibraryPageId,
): Promise<LibraryVersionListResponse> {
  return httpGet<LibraryVersionListResponse>(
    `/v1/library/pages/${encodeURIComponent(id)}/versions`,
    identity,
  );
}

/** GET /v1/library/pages/{id}/versions/{version} — fetch a historical save. */
export function fetchLibraryPageVersion(
  identity: RequestIdentity,
  id: LibraryPageId,
  version: number,
): Promise<LibraryVersionResponse> {
  return httpGet<LibraryVersionResponse>(
    `/v1/library/pages/${encodeURIComponent(id)}/versions/${encodeURIComponent(
      String(version),
    )}`,
    identity,
  );
}

// ===========================================================================
// SSE (durable library channel — sub-PRD §4.2)
// ===========================================================================

/** Closeable handle for a running library-events SSE subscription. */
export interface LibraryEventsStream {
  close(): void;
}

/**
 * Open the durable library-events SSE stream (sub-PRD §4.2). Each frame
 * carries one `LibraryStreamEnvelope`; the client tracks the highest
 * `sequence_no` and reconnects with `?after_sequence=N` to resume
 * without dropping events (cross-audit §5.2).
 *
 * Mirrors `streamProjectEvents` / `streamRoutineEvents` — one
 * connection attempt + stable error hook; the caller owns reconnect
 * timing so tests can drive it deterministically.
 */
export function streamLibraryEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: LibraryStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): LibraryEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/library/stream",
    query: librarySseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors inboxApi / routinesApi
        // behavior: a single bad frame must not tear down the connection.
        return;
      }
      if (isLibraryStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchLibraryOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.kind !== undefined) {
    params["filter[kind]"] = filters.kind;
  }
  if (filters?.project_id !== undefined) {
    params["filter[project_id]"] = filters.project_id;
  }
  if (filters?.source_kind !== undefined) {
    params["filter[source.kind]"] = filters.source_kind;
  }
  if (filters?.tag !== undefined) {
    params["filter[tag]"] = filters.tag;
  }
  if (filters?.index_status !== undefined) {
    params["filter[index_status]"] = filters.index_status;
  }
  if (filters?.owner_user_id !== undefined) {
    params["filter[owner_user_id]"] = filters.owner_user_id;
  }
  if (filters?.file_kind !== undefined) {
    params["filter[file_kind]"] = filters.file_kind;
  }
  if (q !== undefined && q.length > 0) {
    params.q = q;
  }
  if (sort !== undefined) {
    params.sort = sort;
  }
  if (after !== undefined) {
    params.after = after;
  }
  if (limit !== undefined) {
    params.limit = String(limit);
  }
  return params;
}

function librarySseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural check on the SSE envelope. Matches the discriminator
 * fields per sub-PRD §4.2 — `sequence_no` (number), `event_type`
 * (string), `item_id` (string), `payload` (object), `emitted_at`
 * (string). Same pattern as `isProjectStreamEnvelope` /
 * `isRoutineStreamEnvelope`.
 */
function isLibraryStreamEnvelope(
  value: unknown,
): value is LibraryStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.item_id === "string" &&
    typeof v.emitted_at === "string" &&
    typeof v.payload === "object" &&
    v.payload !== null
  );
}

function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
