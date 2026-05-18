import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthBearerProvider } from "./http";
import {
  citeLibraryItem,
  createLibraryPage,
  deleteLibraryItem,
  fetchLibrary,
  fetchLibraryDownload,
  fetchLibraryItem,
  fetchLibraryPageVersion,
  fetchLibraryPageVersions,
  fetchLibraryPreview,
  fetchLibraryRecents,
  finalizeLibraryDataset,
  finalizeLibraryFile,
  initLibraryDataset,
  initLibraryUpload,
  patchLibraryItem,
  patchLibraryPageBody,
  pinLibraryItem,
  putLibraryBlob,
  searchLibrary,
  unpinLibraryItem,
} from "./libraryApi";
import type {
  LibraryDataset,
  LibraryDatasetId,
  LibraryDatasetIngestResponse,
  LibraryFile,
  LibraryFileId,
  LibraryItem,
  LibraryEntityId,
  LibraryListResponse,
  LibraryPage,
  LibraryPageId,
  LibraryRecentsResponse,
  LibrarySearchResponse,
  LibraryUploadInitResponse,
  ProjectId,
  TenantId,
  UserId,
} from "./_library-stub";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

// ===========================================================================
// Fixtures
// ===========================================================================

function fileFixture(overrides: Partial<LibraryFile> = {}): LibraryFile {
  return {
    id: "lib_file_1" as LibraryFileId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    project_id: null,
    kind: "file",
    file_kind: "pdf",
    name: "Q3 strategy.pdf",
    mime: "application/pdf",
    size_bytes: 1234,
    blob_ref: "blob://abc",
    thumbnail_blob_ref: null,
    source: { kind: "user_upload", uploaded_by: "user_test" as UserId },
    tags: [],
    index_status: "pending",
    index_error: null,
    checksum_sha256: "deadbeef",
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    last_accessed_at: null,
    ...overrides,
  };
}

function pageFixture(overrides: Partial<LibraryPage> = {}): LibraryPage {
  return {
    id: "lib_page_1" as LibraryPageId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    project_id: null,
    kind: "page",
    title: "Launch notes",
    markdown: "# Launch notes\n\nDraft.",
    version: 1,
    version_etag: 'W/"v1"',
    source: { kind: "user_upload", uploaded_by: "user_test" as UserId },
    tags: [],
    index_status: "indexed",
    index_error: null,
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    last_accessed_at: null,
    ...overrides,
  };
}

function datasetFixture(
  overrides: Partial<LibraryDataset> = {},
): LibraryDataset {
  return {
    id: "lib_ds_1" as LibraryDatasetId,
    tenant_id: "tenant_1" as TenantId,
    owner_user_id: "user_test" as UserId,
    project_id: null,
    kind: "dataset",
    name: "Q3 forecast",
    description: null,
    schema: [],
    row_count: 0,
    size_bytes: 0,
    blob_ref: "blob://ds-abc",
    format: "parquet",
    source: { kind: "user_upload", uploaded_by: "user_test" as UserId },
    tags: [],
    index_status: "pending",
    index_error: null,
    checksum_sha256: "deadbeef",
    created_at: "2026-05-18T09:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    last_accessed_at: null,
    ...overrides,
  };
}

function listFixture(items: ReadonlyArray<LibraryItem>): LibraryListResponse {
  return {
    items,
    next_cursor: null,
    counts_by_kind: { file: 0, page: 0, dataset: 0 },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

function fetchMockReturning(
  responder: (
    input: RequestInfo | URL,
    init: RequestInit | undefined,
  ) => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    responder(input, init),
  );
}

// ===========================================================================
// LIST + RECENTS — happy + error + filter encoding
// ===========================================================================

describe("fetchLibrary", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/library with identity and no extras when called bare", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(listFixture([fileFixture()])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchLibrary(IDENTITY);

    expect(res.items).toHaveLength(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/library");
    expect(url).toContain("org_id=org_test");
    expect(url).toContain("user_id=user_test");
    expect(url).not.toContain("filter%5B");
    // Facade-only invariant: caller never sees an absolute backend URL.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("encodes every filter axis + q + sort + cursor + limit", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchLibrary(IDENTITY, {
      filters: {
        kind: "file",
        project_id: "project_1" as ProjectId,
        source_kind: "user_upload",
        tag: "design",
        index_status: "indexed",
        owner_user_id: "user_member" as UserId,
        file_kind: "pdf",
      },
      q: "launch",
      sort: "name:asc",
      after: "cursor_xyz",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(encodeURIComponent("filter[kind]") + "=file");
    expect(url).toContain(
      encodeURIComponent("filter[project_id]") + "=project_1",
    );
    expect(url).toContain(
      encodeURIComponent("filter[source.kind]") + "=user_upload",
    );
    expect(url).toContain(encodeURIComponent("filter[tag]") + "=design");
    expect(url).toContain(
      encodeURIComponent("filter[index_status]") + "=indexed",
    );
    expect(url).toContain(
      encodeURIComponent("filter[owner_user_id]") + "=user_member",
    );
    expect(url).toContain(encodeURIComponent("filter[file_kind]") + "=pdf");
    expect(url).toContain("q=launch");
    expect(url).toContain("sort=" + encodeURIComponent("name:asc"));
    expect(url).toContain("after=cursor_xyz");
    expect(url).toContain("limit=25");
  });

  it("omits the q param entirely when the search string is empty", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture([])));
    vi.stubGlobal("fetch", fetchMock);

    await fetchLibrary(IDENTITY, { q: "" });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).not.toContain("q=");
  });

  it("surfaces server error messages from FastAPI's `detail` envelope", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "tenant_mismatch" }, 403),
      ),
    );

    await expect(fetchLibrary(IDENTITY)).rejects.toThrow("tenant_mismatch");
  });
});

describe("fetchLibraryRecents", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/library/recents", async () => {
    const recents: LibraryRecentsResponse = {
      recently_saved: [fileFixture()],
      recently_accessed: [],
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(recents));
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchLibraryRecents(IDENTITY);
    expect(res.recently_saved).toHaveLength(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/library/recents");
  });
});

// ===========================================================================
// DETAIL
// ===========================================================================

describe("fetchLibraryItem", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/library/{id} with URL-encoded id", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(fileFixture({ id: "lib/1 special" as LibraryFileId })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchLibraryItem(IDENTITY, "lib/1 special" as LibraryEntityId);

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/library/lib%2F1%20special");
  });

  it("propagates 404 as an Error (sub-PRD §1.3 cross-tenant returns 404 not 403)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "library_item_not_found" }, 404),
      ),
    );

    await expect(
      fetchLibraryItem(IDENTITY, "missing" as LibraryEntityId),
    ).rejects.toThrow("library_item_not_found");
  });
});

// ===========================================================================
// UPLOAD FLOW — 3 stages: init → PUT signed URL → finalize
// ===========================================================================

describe("upload flow", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("initLibraryUpload POSTs /v1/library/files and returns the grant", async () => {
    const grant: LibraryUploadInitResponse = {
      file_id: "lib_file_1" as LibraryFileId,
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: { "content-type": "application/pdf" },
      expires_at: "2026-05-18T10:00:00Z",
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(grant));
    vi.stubGlobal("fetch", fetchMock);

    const res = await initLibraryUpload(IDENTITY, {
      name: "Q3 strategy.pdf",
      mime: "application/pdf",
      size_bytes: 1234,
    });

    expect(res.upload_url).toContain("https://signed.example");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/library/files");
    // Body is the init payload (server signs over name+mime+size_bytes).
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({
      name: "Q3 strategy.pdf",
      mime: "application/pdf",
      size_bytes: 1234,
    });
  });

  it("putLibraryBlob PUTs bytes DIRECT to the signed URL (NOT through the facade)", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(200));
    vi.stubGlobal("fetch", fetchMock);

    const bytes = new Uint8Array([1, 2, 3, 4]);
    await putLibraryBlob(
      "https://signed.example/abc?sig=xyz",
      {
        "content-type": "application/pdf",
        "x-amz-server-side-encryption": "AES256",
      },
      bytes,
    );

    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toBe("https://signed.example/abc?sig=xyz");
    // Crucial: the call target is the object store, NOT /v1/library/*.
    expect(String(call[0])).not.toContain("/v1/");
    const init = call[1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["content-type"]).toBe(
      "application/pdf",
    );
    // The signed-URL handshake forbids auth-header tampering; verify the
    // bearer is NOT forwarded to the object store.
    expect(
      (init.headers as Record<string, string>)["authorization"],
    ).toBeUndefined();
    expect(init.body).toBe(bytes);
  });

  it("putLibraryBlob throws on a 403 signed-URL signature mismatch", async () => {
    const fetchMock = fetchMockReturning(
      () =>
        new Response("<Error>SignatureDoesNotMatch</Error>", { status: 403 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      putLibraryBlob(
        "https://signed.example/abc?sig=xyz",
        { "content-type": "application/pdf" },
        new Uint8Array([1, 2, 3]),
      ),
    ).rejects.toThrow(/403/);
  });

  it("finalizeLibraryFile POSTs /v1/library/files/{id}/finalize", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(fileFixture({ index_status: "pending" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await finalizeLibraryFile(
      IDENTITY,
      "lib_file_1" as LibraryFileId,
    );

    expect(res.kind).toBe("file");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/files/lib_file_1/finalize",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("end-to-end shape: init → PUT signed URL → finalize, byte path stays direct", async () => {
    const grant: LibraryUploadInitResponse = {
      file_id: "lib_file_1" as LibraryFileId,
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: { "content-type": "application/pdf" },
      expires_at: "2026-05-18T10:00:00Z",
    };
    // Use a single fetch mock that branches on URL — verifies that all
    // three stages call `fetch` separately and that stage 2 hits the
    // signed URL, NOT the facade.
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/v1/library/files/") && url.endsWith("/finalize")) {
          return jsonResponse(fileFixture());
        }
        if (url.includes("/v1/library/files")) {
          return jsonResponse(grant);
        }
        if (url.startsWith("https://signed.example/")) {
          // The signed-URL request MUST NOT carry the facade bearer
          // (S3 enforces this).
          expect(
            (init?.headers as Record<string, string> | undefined)?.[
              "authorization"
            ],
          ).toBeUndefined();
          return emptyResponse(200);
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const g = await initLibraryUpload(IDENTITY, {
      name: "Q3 strategy.pdf",
      mime: "application/pdf",
      size_bytes: 1234,
    });
    await putLibraryBlob(
      g.upload_url,
      g.upload_headers,
      new Uint8Array([1, 2, 3]),
    );
    await finalizeLibraryFile(IDENTITY, g.file_id);

    // 3 distinct fetches: init (facade), PUT (signed URL), finalize (facade).
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls[0]).toContain("/v1/library/files");
    expect(urls[1]).toBe("https://signed.example/abc?sig=xyz");
    expect(urls[2]).toContain("/v1/library/files/lib_file_1/finalize");
  });
});

// ===========================================================================
// DATASETS — init + finalize
// ===========================================================================

describe("dataset ingest", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("initLibraryDataset POSTs /v1/library/datasets and returns the grant", async () => {
    const grant: LibraryDatasetIngestResponse = {
      dataset_id: "lib_ds_1" as LibraryDatasetId,
      upload_url: "https://signed.example/ds-abc?sig=xyz",
      upload_headers: { "content-type": "application/octet-stream" },
      expires_at: "2026-05-18T10:00:00Z",
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(grant));
    vi.stubGlobal("fetch", fetchMock);

    const res = await initLibraryDataset(IDENTITY, {
      name: "Q3 forecast",
      format: "parquet",
      size_bytes: 4096,
    });

    expect(res.dataset_id).toBe("lib_ds_1");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/datasets",
    );
  });

  it("finalizeLibraryDataset POSTs /v1/library/datasets/{id}/finalize", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(datasetFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await finalizeLibraryDataset(
      IDENTITY,
      "lib_ds_1" as LibraryDatasetId,
    );
    expect(res.kind).toBe("dataset");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/datasets/lib_ds_1/finalize",
    );
  });
});

// ===========================================================================
// PAGES — create + body PATCH with If-Match
// ===========================================================================

describe("pages", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("createLibraryPage POSTs /v1/library/pages with the body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(pageFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await createLibraryPage(IDENTITY, {
      title: "Launch notes",
      markdown: "# Launch notes",
    });

    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/library/pages");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({ title: "Launch notes" });
  });

  it("patchLibraryPageBody PATCHes /v1/library/{id} with If-Match header", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(pageFixture({ version: 2, version_etag: 'W/"v2"' })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await patchLibraryPageBody(
      IDENTITY,
      "lib_page_1" as LibraryPageId,
      { markdown: "# Updated" },
      'W/"v1"',
    );

    const call = fetchMock.mock.calls[0];
    expect((call[1] as RequestInit).method).toBe("PATCH");
    expect(String(call[0])).toContain("/v1/library/lib_page_1");
    const headers = (call[1] as RequestInit).headers as Record<string, string>;
    // Header keys are lowercased by the transport — assert
    // case-insensitively.
    const headerKeys = Object.keys(headers).map((k) => k.toLowerCase());
    expect(headerKeys).toContain("if-match");
    const ifMatch =
      headers["if-match"] ?? headers["If-Match"] ?? headers["IF-MATCH"];
    expect(ifMatch).toBe('W/"v1"');
  });

  it("patchLibraryPageBody surfaces a 412 conflict (stale etag)", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "page_version_conflict" }, 412),
      ),
    );

    await expect(
      patchLibraryPageBody(
        IDENTITY,
        "lib_page_1" as LibraryPageId,
        { markdown: "x" },
        'W/"stale"',
      ),
    ).rejects.toThrow("page_version_conflict");
  });
});

// ===========================================================================
// METADATA MUTATIONS — patch + delete
// ===========================================================================

describe("metadata mutations", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("patchLibraryItem PATCHes /v1/library/{id}", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse(fileFixture({ name: "Renamed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await patchLibraryItem(
      IDENTITY,
      "lib_file_1" as LibraryEntityId,
      { name: "Renamed" },
    );

    expect(res.kind).toBe("file");
    expect(res.kind === "file" && res.name).toBe("Renamed");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
  });

  it("deleteLibraryItem DELETEs /v1/library/{id}", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await deleteLibraryItem(IDENTITY, "lib_file_1" as LibraryEntityId);

    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/lib_file_1",
    );
  });
});

// ===========================================================================
// SEARCH
// ===========================================================================

describe("searchLibrary", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/library/search with the query body", async () => {
    const response: LibrarySearchResponse = {
      query: "launch",
      hits: [],
      took_ms: 12,
      retrieval_strategy: "hybrid",
      reranker_used: true,
      partial: {
        status: "ok",
        missing: [],
        details: {
          bm25_complete: true,
          vector_complete: true,
          rerank_complete: true,
        },
      },
    };
    const fetchMock = fetchMockReturning(() => jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    const res = await searchLibrary(IDENTITY, {
      query: "launch",
      max_hits: 20,
      rerank: true,
    });

    expect(res.query).toBe("launch");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/library/search");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({ query: "launch", max_hits: 20, rerank: true });
  });
});

// ===========================================================================
// PREVIEW + DOWNLOAD (signed-URL exit)
// ===========================================================================

describe("preview + download", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("fetchLibraryPreview GETs /v1/library/{id}/preview", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        kind: "file",
        file_preview: {
          thumbnail_signed_url: "https://signed.example/thumb",
          page_count: 1,
          first_page_signed_url: "https://signed.example/page-1",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchLibraryPreview(
      IDENTITY,
      "lib_file_1" as LibraryEntityId,
    );
    expect(res.kind).toBe("file");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/lib_file_1/preview",
    );
  });

  it("fetchLibraryDownload GETs /v1/library/{id}/download and returns a signed URL", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        signed_url: "https://signed.example/dl?sig=xyz",
        expires_at: "2026-05-18T10:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchLibraryDownload(
      IDENTITY,
      "lib_file_1" as LibraryEntityId,
    );

    expect(res.signed_url).toContain("https://signed.example");
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/lib_file_1/download",
    );
    // The download response is just a URL — the caller dereferences it
    // DIRECT to the object store, NOT through the facade.
    expect(res.signed_url).not.toContain("/v1/library");
  });
});

// ===========================================================================
// PIN + CITE + VERSIONS
// ===========================================================================

describe("pin + cite + versions", () => {
  beforeEach(() => {
    configureAuthBearerProvider(() => "test-bearer");
  });
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("pinLibraryItem POSTs /v1/library/{id}/pin", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(fileFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await pinLibraryItem(IDENTITY, "lib_file_1" as LibraryEntityId);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/lib_file_1/pin",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("unpinLibraryItem POSTs /v1/library/{id}/pin with unpin:true body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(fileFixture()));
    vi.stubGlobal("fetch", fetchMock);

    await unpinLibraryItem(IDENTITY, "lib_file_1" as LibraryEntityId);

    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ unpin: true });
  });

  it("citeLibraryItem POSTs /v1/library/{id}/cite", async () => {
    const fetchMock = fetchMockReturning(() => emptyResponse(204));
    vi.stubGlobal("fetch", fetchMock);

    await citeLibraryItem(IDENTITY, "lib_file_1" as LibraryEntityId, {
      conversation_id: "conv_1",
    });

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/lib_file_1/cite",
    );
    expect(
      JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string),
    ).toMatchObject({ conversation_id: "conv_1" });
  });

  it("fetchLibraryPageVersions GETs the versions list", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({ items: [{ version: 1, etag: "W/v1" }] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchLibraryPageVersions(IDENTITY, "lib_page_1" as LibraryPageId);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/pages/lib_page_1/versions",
    );
  });

  it("fetchLibraryPageVersion GETs a specific version", async () => {
    const fetchMock = fetchMockReturning(() =>
      jsonResponse({
        version: 2,
        etag: "W/v2",
        markdown: "# v2",
        created_at: "2026-05-18T09:00:00Z",
        created_by: "user_test",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchLibraryPageVersion(IDENTITY, "lib_page_1" as LibraryPageId, 2);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/library/pages/lib_page_1/versions/2",
    );
  });
});
