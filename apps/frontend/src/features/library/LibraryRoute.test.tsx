import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type {
  LibraryDataset,
  LibraryDatasetId,
  LibraryFile,
  LibraryFileId,
  LibraryItem,
  LibraryEntityId,
  LibraryListResponse,
  LibraryPage,
  LibraryPageId,
  LibraryStreamEnvelope,
  TenantId,
  UserId,
} from "../../api/_library-stub";

// Mock the libraryApi module so the tests don't have to drive the real
// fetch / SSE plumbing — that surface is covered in `libraryApi.test.ts`.
const libraryApiMocks = vi.hoisted(() => ({
  fetchLibrary: vi.fn(),
  fetchLibraryItem: vi.fn(),
  initLibraryUpload: vi.fn(),
  putLibraryBlob: vi.fn(),
  finalizeLibraryFile: vi.fn(),
  deleteLibraryItem: vi.fn(),
  patchLibraryPageBody: vi.fn(),
  searchLibrary: vi.fn(),
  streamLibraryEvents: vi.fn(),
}));
vi.mock("../../api/libraryApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/libraryApi")>(
    "../../api/libraryApi",
  );
  return {
    ...actual,
    fetchLibrary: libraryApiMocks.fetchLibrary,
    fetchLibraryItem: libraryApiMocks.fetchLibraryItem,
    initLibraryUpload: libraryApiMocks.initLibraryUpload,
    putLibraryBlob: libraryApiMocks.putLibraryBlob,
    finalizeLibraryFile: libraryApiMocks.finalizeLibraryFile,
    deleteLibraryItem: libraryApiMocks.deleteLibraryItem,
    patchLibraryPageBody: libraryApiMocks.patchLibraryPageBody,
    searchLibrary: libraryApiMocks.searchLibrary,
    streamLibraryEvents: libraryApiMocks.streamLibraryEvents,
  };
});

// Imports below this line resolve through the mocks above.
import {
  LibraryRoute,
  applyLibraryEnvelope,
  uploadLibraryFile,
} from "./LibraryRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

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
    markdown: "# Launch notes",
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

function listResponse(items: ReadonlyArray<LibraryItem>): LibraryListResponse {
  return {
    items,
    next_cursor: null,
    counts_by_kind: { file: 0, page: 0, dataset: 0 },
  };
}

function envelope(
  type: LibraryStreamEnvelope["event_type"],
  payload: LibraryStreamEnvelope["payload"],
  itemId: LibraryEntityId,
  sequenceNo = 1,
): LibraryStreamEnvelope {
  return {
    sequence_no: sequenceNo,
    event_type: type,
    item_id: itemId,
    payload,
    emitted_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: LibraryStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: LibraryStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  libraryApiMocks.streamLibraryEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: LibraryStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

// ===========================================================================
// PURE REDUCER — applyLibraryEnvelope
// ===========================================================================

describe("applyLibraryEnvelope", () => {
  it("prepends library.item_created when payload is a full item", () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    const b = fileFixture({ id: "b" as LibraryFileId });
    const next = applyLibraryEnvelope(
      [a],
      envelope("library.item_created", b, "b" as LibraryEntityId),
    );
    expect(next.map((it) => it.id)).toEqual(["b", "a"]);
  });

  it("replaces in place on library.item_indexed", () => {
    const a = fileFixture({
      id: "a" as LibraryFileId,
      index_status: "pending",
    });
    const aIndexed = fileFixture({
      id: "a" as LibraryFileId,
      index_status: "indexed",
    });
    const next = applyLibraryEnvelope(
      [a],
      envelope("library.item_indexed", aIndexed, "a" as LibraryEntityId),
    );
    expect(next[0].index_status).toBe("indexed");
  });

  it("replaces in place on library.item_index_failed and library.item_updated", () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    const aFailed = fileFixture({
      id: "a" as LibraryFileId,
      index_status: "failed",
      index_error: "OOM",
    });
    const afterFail = applyLibraryEnvelope(
      [a],
      envelope("library.item_index_failed", aFailed, "a" as LibraryEntityId),
    );
    expect(afterFail[0].index_status).toBe("failed");

    const renamed = fileFixture({ id: "a" as LibraryFileId, name: "Renamed" });
    const afterUpdate = applyLibraryEnvelope(
      afterFail,
      envelope("library.item_updated", renamed, "a" as LibraryEntityId),
    );
    expect((afterUpdate[0] as LibraryFile).name).toBe("Renamed");
  });

  it("drops a row on library.item_deleted", () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    const b = fileFixture({ id: "b" as LibraryFileId });
    const next = applyLibraryEnvelope(
      [a, b],
      envelope(
        "library.item_deleted",
        { item_id: "b" as LibraryEntityId },
        "b" as LibraryEntityId,
      ),
    );
    expect(next.map((it) => it.id)).toEqual(["a"]);
  });

  it("returns the same array on library.item_deleted for an unknown id", () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    const before = [a];
    const after = applyLibraryEnvelope(
      before,
      envelope(
        "library.item_deleted",
        { item_id: "missing" as LibraryEntityId },
        "missing" as LibraryEntityId,
      ),
    );
    expect(after).toBe(before);
  });

  it("ignores envelopes whose payload is not a full LibraryItem shape", () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    const before = [a];
    const after = applyLibraryEnvelope(
      before,
      envelope(
        "library.item_updated",
        { item_id: "a" as LibraryEntityId, error: "?" },
        "a" as LibraryEntityId,
      ),
    );
    expect(after).toBe(before);
  });
});

// ===========================================================================
// PURE ORCHESTRATOR — uploadLibraryFile drives 3 stages in order
// ===========================================================================

describe("uploadLibraryFile orchestrator", () => {
  beforeEach(() => {
    libraryApiMocks.initLibraryUpload.mockReset();
    libraryApiMocks.putLibraryBlob.mockReset();
    libraryApiMocks.finalizeLibraryFile.mockReset();
  });

  it("calls init → PUT → finalize in order with the signed URL preserved", async () => {
    libraryApiMocks.initLibraryUpload.mockResolvedValueOnce({
      file_id: "lib_file_1",
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: { "content-type": "application/pdf" },
      expires_at: "2026-05-18T10:00:00Z",
    });
    libraryApiMocks.putLibraryBlob.mockResolvedValueOnce(undefined);
    libraryApiMocks.finalizeLibraryFile.mockResolvedValueOnce(
      fileFixture({ id: "lib_file_1" as LibraryFileId }),
    );

    const stages: string[] = [];
    const file = new File(["abc"], "Q3 strategy.pdf", {
      type: "application/pdf",
    });
    const result = await uploadLibraryFile(IDENTITY, file, (s) =>
      stages.push(s.stage),
    );

    expect(result.id).toBe("lib_file_1");
    expect(stages).toEqual(["grant", "uploading", "finalizing", "done"]);
    expect(libraryApiMocks.initLibraryUpload).toHaveBeenCalledWith(IDENTITY, {
      name: "Q3 strategy.pdf",
      mime: "application/pdf",
      size_bytes: 3,
    });
    // Verify the signed URL was passed verbatim to putLibraryBlob — the
    // bytes go DIRECT, NOT through the facade.
    expect(libraryApiMocks.putLibraryBlob).toHaveBeenCalledWith(
      "https://signed.example/abc?sig=xyz",
      { "content-type": "application/pdf" },
      file,
    );
    expect(libraryApiMocks.finalizeLibraryFile).toHaveBeenCalledWith(
      IDENTITY,
      "lib_file_1",
    );
  });

  it("propagates grant-stage errors and emits no PUT / finalize", async () => {
    libraryApiMocks.initLibraryUpload.mockRejectedValueOnce(
      new Error("size_limit_exceeded"),
    );

    const stages: string[] = [];
    await expect(
      uploadLibraryFile(
        IDENTITY,
        new File(["abc"], "x.pdf", { type: "application/pdf" }),
        (s) => stages.push(s.stage),
      ),
    ).rejects.toThrow("size_limit_exceeded");
    expect(libraryApiMocks.putLibraryBlob).not.toHaveBeenCalled();
    expect(libraryApiMocks.finalizeLibraryFile).not.toHaveBeenCalled();
    expect(stages).toEqual(["grant"]);
  });

  it("propagates PUT-stage errors and emits no finalize", async () => {
    libraryApiMocks.initLibraryUpload.mockResolvedValueOnce({
      file_id: "lib_file_1",
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: {},
      expires_at: "2026-05-18T10:00:00Z",
    });
    libraryApiMocks.putLibraryBlob.mockRejectedValueOnce(
      new Error("Upload to signed URL failed (403)"),
    );

    await expect(
      uploadLibraryFile(
        IDENTITY,
        new File(["abc"], "x.pdf", { type: "application/pdf" }),
        () => undefined,
      ),
    ).rejects.toThrow(/403/);
    expect(libraryApiMocks.finalizeLibraryFile).not.toHaveBeenCalled();
  });

  it("propagates finalize-stage errors after the bytes uploaded successfully", async () => {
    libraryApiMocks.initLibraryUpload.mockResolvedValueOnce({
      file_id: "lib_file_1",
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: {},
      expires_at: "2026-05-18T10:00:00Z",
    });
    libraryApiMocks.putLibraryBlob.mockResolvedValueOnce(undefined);
    libraryApiMocks.finalizeLibraryFile.mockRejectedValueOnce(
      new Error("checksum_mismatch"),
    );

    await expect(
      uploadLibraryFile(
        IDENTITY,
        new File(["abc"], "x.pdf", { type: "application/pdf" }),
        () => undefined,
      ),
    ).rejects.toThrow("checksum_mismatch");
    expect(libraryApiMocks.putLibraryBlob).toHaveBeenCalled();
  });
});

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("LibraryRoute render", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.fetchLibraryItem.mockReset();
    libraryApiMocks.initLibraryUpload.mockReset();
    libraryApiMocks.putLibraryBlob.mockReset();
    libraryApiMocks.finalizeLibraryFile.mockReset();
    libraryApiMocks.deleteLibraryItem.mockReset();
    libraryApiMocks.patchLibraryPageBody.mockReset();
    libraryApiMocks.searchLibrary.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the loading state, then the ready list", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([fileFixture({ name: "Q3 strategy.pdf" })]),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    expect(screen.getByTestId("library-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Q3 strategy.pdf")).toBeInTheDocument();
  });

  it("renders the empty state when the server returns no items", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));
    render(<LibraryRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("library-route-empty")).toBeInTheDocument();
    });
  });

  it("renders the error state on fetch failure and retries on click", async () => {
    libraryApiMocks.fetchLibrary.mockRejectedValueOnce(new Error("boom"));
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([fileFixture()]),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route-error")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("library-route-error-message").textContent,
    ).toContain("boom");

    fireEvent.click(screen.getByTestId("library-route-retry"));

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(libraryApiMocks.fetchLibrary).toHaveBeenCalledTimes(2);
  });

  it("renders mixed file + page + dataset rows with correct kinds", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([fileFixture(), pageFixture(), datasetFixture()]),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("library-route-row")).toHaveLength(3);
    });
    const rows = screen.getAllByTestId("library-route-row");
    expect(rows.map((r) => r.getAttribute("data-item-kind"))).toEqual([
      "file",
      "page",
      "dataset",
    ]);
  });
});

// ===========================================================================
// SSE — deltas merge into the local list
// ===========================================================================

describe("LibraryRoute SSE", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes after the initial load and merges library.item_created deltas", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([fileFixture({ id: "a" as LibraryFileId, name: "Alpha" })]),
    );
    const sse = captureStreamCallbacks();

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(libraryApiMocks.streamLibraryEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "library.item_created",
            fileFixture({ id: "b" as LibraryFileId, name: "Bravo" }),
            "b" as LibraryEntityId,
            1,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.getByText("Bravo")).toBeInTheDocument();
    });
  });

  it("drops a row on library.item_deleted", async () => {
    const a = fileFixture({ id: "a" as LibraryFileId, name: "Alpha" });
    const b = fileFixture({ id: "b" as LibraryFileId, name: "Bravo" });
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([a, b]));
    const sse = captureStreamCallbacks();

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeInTheDocument();
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "library.item_deleted",
            { item_id: "b" as LibraryEntityId },
            "b" as LibraryEntityId,
            2,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.queryByText("Bravo")).not.toBeInTheDocument();
    });
  });

  it("closes the active stream when the stream errors out", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([fileFixture()]),
    );
    const sse = captureStreamCallbacks();

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(libraryApiMocks.streamLibraryEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
  });
});

// ===========================================================================
// UPLOAD — file input drives 3-stage flow
// ===========================================================================

describe("LibraryRoute upload", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.initLibraryUpload.mockReset();
    libraryApiMocks.putLibraryBlob.mockReset();
    libraryApiMocks.finalizeLibraryFile.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("file input triggers init → PUT → finalize", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));
    libraryApiMocks.initLibraryUpload.mockResolvedValueOnce({
      file_id: "lib_file_1",
      upload_url: "https://signed.example/abc?sig=xyz",
      upload_headers: { "content-type": "application/pdf" },
      expires_at: "2026-05-18T10:00:00Z",
    });
    libraryApiMocks.putLibraryBlob.mockResolvedValueOnce(undefined);
    libraryApiMocks.finalizeLibraryFile.mockResolvedValueOnce(
      fileFixture({ name: "Q3 strategy.pdf" }),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    const input = screen.getByTestId(
      "library-route-upload-input",
    ) as HTMLInputElement;
    const file = new File(["abc"], "Q3 strategy.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(libraryApiMocks.initLibraryUpload).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(libraryApiMocks.putLibraryBlob).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(libraryApiMocks.finalizeLibraryFile).toHaveBeenCalled();
    });
    // Once finalized, the row is optimistically prepended.
    await waitFor(() => {
      expect(screen.getByText("Q3 strategy.pdf")).toBeInTheDocument();
    });
  });

  it("surfaces an upload error on grant failure", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));
    libraryApiMocks.initLibraryUpload.mockRejectedValueOnce(
      new Error("size_limit_exceeded"),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    const input = screen.getByTestId(
      "library-route-upload-input",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(["x"], "big.pdf", { type: "application/pdf" })],
      },
    });

    await waitFor(() => {
      expect(
        screen.getByTestId("library-route-pending-error").textContent,
      ).toContain("size_limit_exceeded");
    });
    // Stage marker reflects the error.
    expect(screen.getByTestId("library-route-upload-stage").textContent).toBe(
      "error",
    );
    // No PUT was attempted.
    expect(libraryApiMocks.putLibraryBlob).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// SEARCH
// ===========================================================================

describe("LibraryRoute search", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.searchLibrary.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("submitting a query calls searchLibrary and renders the result count", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));
    libraryApiMocks.searchLibrary.mockResolvedValueOnce({
      query: "launch",
      hits: [],
      took_ms: 42,
      retrieval_strategy: "hybrid",
      reranker_used: false,
      partial: {
        status: "ok",
        missing: [],
        details: {
          bm25_complete: true,
          vector_complete: true,
          rerank_complete: true,
        },
      },
    });

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.change(screen.getByTestId("library-route-search-input"), {
      target: { value: "launch" },
    });
    fireEvent.click(screen.getByTestId("library-route-search-submit"));

    await waitFor(() => {
      expect(libraryApiMocks.searchLibrary).toHaveBeenCalledWith(IDENTITY, {
        query: "launch",
      });
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("library-route-search-results"),
      ).toBeInTheDocument();
    });
  });

  it("surfaces a search error in a non-fatal banner", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));
    libraryApiMocks.searchLibrary.mockRejectedValueOnce(
      new Error("search_unavailable"),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.change(screen.getByTestId("library-route-search-input"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByTestId("library-route-search-submit"));

    await waitFor(() => {
      expect(
        screen.getByTestId("library-route-search-error").textContent,
      ).toContain("search_unavailable");
    });
  });

  it("does not fire a search request when the input is empty", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([]));

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("library-route-search-submit"));
    // No async assertion needed — synchronous gating.
    expect(libraryApiMocks.searchLibrary).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// PAGE AUTOSAVE
// ===========================================================================

describe("LibraryRoute page autosave", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.patchLibraryPageBody.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReturnValue({ close: vi.fn() });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("debounces typing and PATCHes /v1/library/{id} with the latest etag", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([pageFixture()]),
    );
    libraryApiMocks.patchLibraryPageBody.mockResolvedValueOnce(
      pageFixture({ version: 2, version_etag: 'W/"v2"', markdown: "Updated" }),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await vi.waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    const textarea = screen.getByTestId(
      "library-route-page-editor-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Updated" } });

    // Before debounce elapses, no PATCH yet.
    expect(libraryApiMocks.patchLibraryPageBody).not.toHaveBeenCalled();

    // Advance past the autosave debounce.
    await act(async () => {
      vi.advanceTimersByTime(800);
    });

    await vi.waitFor(() => {
      expect(libraryApiMocks.patchLibraryPageBody).toHaveBeenCalledWith(
        IDENTITY,
        "lib_page_1",
        { markdown: "Updated" },
        'W/"v1"',
      );
    });
  });

  it("surfaces a 412 stale-etag error in the pending-error banner", async () => {
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(
      listResponse([pageFixture()]),
    );
    libraryApiMocks.patchLibraryPageBody.mockRejectedValueOnce(
      new Error("page_version_conflict"),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await vi.waitFor(() => {
      expect(screen.getByTestId("library-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.change(screen.getByTestId("library-route-page-editor-textarea"), {
      target: { value: "Stale" },
    });
    await act(async () => {
      vi.advanceTimersByTime(800);
    });

    await vi.waitFor(() => {
      expect(
        screen.getByTestId("library-route-pending-error").textContent,
      ).toContain("page_version_conflict");
    });
  });
});

// ===========================================================================
// DELETE
// ===========================================================================

describe("LibraryRoute delete", () => {
  beforeEach(() => {
    libraryApiMocks.fetchLibrary.mockReset();
    libraryApiMocks.deleteLibraryItem.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReset();
    libraryApiMocks.streamLibraryEvents.mockReturnValue({ close: vi.fn() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("calls deleteLibraryItem and removes the row from the local list", async () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([a]));
    libraryApiMocks.deleteLibraryItem.mockResolvedValueOnce(undefined);

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route-delete")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("library-route-delete"));

    await waitFor(() => {
      expect(libraryApiMocks.deleteLibraryItem).toHaveBeenCalledWith(
        IDENTITY,
        "a",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("library-route-empty")).toBeInTheDocument();
    });
  });

  it("surfaces a pending-error banner when delete fails", async () => {
    const a = fileFixture({ id: "a" as LibraryFileId });
    libraryApiMocks.fetchLibrary.mockResolvedValueOnce(listResponse([a]));
    libraryApiMocks.deleteLibraryItem.mockRejectedValueOnce(
      new Error("delete_forbidden"),
    );

    render(<LibraryRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByTestId("library-route-delete")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("library-route-delete"));

    await waitFor(() => {
      expect(
        screen.getByTestId("library-route-pending-error").textContent,
      ).toContain("delete_forbidden");
    });
    expect(screen.getByTestId("library-route-row")).toBeInTheDocument();
  });
});
