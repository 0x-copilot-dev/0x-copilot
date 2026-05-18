// LibraryRoute — data binder for the Phase 7 Library destination
// (the 14th destination per
// `docs/atlas-new-design/destinations/library-prd.md`).
//
// Mirrors the P6-C ProjectsRoute pattern:
//   1. Fetches `GET /v1/library` via `libraryApi` and owns
//      loading / error / ready states (sub-PRD §3.2 list view).
//   2. Owns the upload flow's 3-stage handshake:
//      grant (`initLibraryUpload`) → PUT to signed URL
//      (`putLibraryBlob`) → finalize (`finalizeLibraryFile`).
//      Bytes go DIRECT to the signed URL — never proxied through the
//      facade. Sub-PRD §5.5: this keeps large uploads off the API hot
//      path.
//   3. For the Page editor: autosave via debounced PATCH against
//      `/v1/library/{id}` with `If-Match` header carrying the latest
//      `version_etag` (sub-PRD §3.4.2). The reducer holds an in-flight
//      pending state so the user keeps typing while a save is mid-air.
//   4. Hosts a search input that calls `searchLibrary` against the
//      facade (sub-PRD §3.5) — same loading/error pattern as the list.
//   5. Renders a host-side scaffolding today; the package-shipped
//      `<LibraryDestination>` already exists but reads through its
//      own Transport hook — this route is the feature-binder that
//      owns upload + autosave + search, behaviour the destination
//      placeholder does not yet own. Once the chat-surface component
//      accepts controlled `items` / `onUpload` / `onSearch` props,
//      swap the inner renderer here.
//
// Network rule (apps/frontend/CLAUDE.md): every facade call goes
// through `libraryApi`; signed-URL PUTs go DIRECT via
// `putLibraryBlob` — never through `libraryApi`'s facade-only helpers.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type { RequestIdentity } from "../../api/config";
import {
  deleteLibraryItem,
  fetchLibrary,
  fetchLibraryItem,
  finalizeLibraryFile,
  initLibraryUpload,
  patchLibraryPageBody,
  putLibraryBlob,
  searchLibrary,
  streamLibraryEvents,
} from "../../api/libraryApi";
import type {
  LibraryEntityId,
  LibraryFileMime,
  LibraryItem,
  LibraryListResponse,
  LibraryPage,
  LibraryPageId,
  LibrarySearchResponse,
  LibraryStreamEnvelope,
} from "../../api/_library-stub";
import { errorMessage } from "../../utils/errors";

/** Reconnect backoff bounds (mirrors ProjectsRoute / sub-PRD §3.8 conventions). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

/** Debounce for the Page editor autosave (sub-PRD §3.4.2). */
const PAGE_AUTOSAVE_DEBOUNCE_MS = 750;

interface LibraryRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<LibraryItem>;
      readonly highestSequenceNo: number;
    };

type UploadStatus =
  | { readonly stage: "idle" }
  | { readonly stage: "grant" }
  | { readonly stage: "uploading" }
  | { readonly stage: "finalizing" }
  | { readonly stage: "done"; readonly itemId: LibraryEntityId }
  | { readonly stage: "error"; readonly message: string };

type SearchState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly query: string }
  | { readonly kind: "ready"; readonly response: LibrarySearchResponse }
  | { readonly kind: "error"; readonly message: string };

/**
 * Pure reducer for SSE deltas — testable without a mounted component.
 *
 * Semantics (sub-PRD §4.2 event types):
 *  - `library.item_created`        → prepend if payload is a full item.
 *  - `library.item_indexed` /
 *    `library.item_index_failed` /
 *    `library.item_updated`        → in-place replace by id.
 *  - `library.item_deleted`        → drop the matching id.
 *
 * Does not call any side effects; the component above does the
 * "highest sequence" tracking + reconnect.
 */
export function applyLibraryEnvelope(
  items: ReadonlyArray<LibraryItem>,
  envelope: LibraryStreamEnvelope,
): ReadonlyArray<LibraryItem> {
  const idx = items.findIndex((it) => it.id === envelope.item_id);

  if (envelope.event_type === "library.item_deleted") {
    if (idx === -1) return items;
    return items.slice(0, idx).concat(items.slice(idx + 1));
  }

  if (
    envelope.event_type === "library.item_created" ||
    envelope.event_type === "library.item_indexed" ||
    envelope.event_type === "library.item_index_failed" ||
    envelope.event_type === "library.item_updated"
  ) {
    if (!isLibraryItemShape(envelope.payload)) {
      return items;
    }
    const item = envelope.payload as LibraryItem;
    if (idx === -1) {
      return [item, ...items];
    }
    const next = items.slice();
    next[idx] = item;
    return next;
  }

  return items;
}

/** Loose structural check: does this payload look like a full LibraryItem? */
function isLibraryItemShape(value: unknown): boolean {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.kind === "string" &&
    (v.kind === "file" || v.kind === "page" || v.kind === "dataset")
  );
}

/**
 * Orchestrate the 3-stage upload handshake. Extracted as a pure
 * function so a test can drive it end-to-end without rendering.
 *
 * Bytes flow DIRECT to the signed URL via `putLibraryBlob` — they
 * do NOT proxy through the facade. The facade only mediates the
 * grant (stage 1) and the finalize (stage 3). Sub-PRD §5.5.
 */
export async function uploadLibraryFile(
  identity: RequestIdentity,
  file: File,
  onStage: (status: UploadStatus) => void,
): Promise<LibraryItem> {
  onStage({ stage: "grant" });
  const grant = await initLibraryUpload(identity, {
    name: file.name,
    mime: file.type as LibraryFileMime,
    size_bytes: file.size,
  });

  onStage({ stage: "uploading" });
  await putLibraryBlob(grant.upload_url, grant.upload_headers, file);

  onStage({ stage: "finalizing" });
  const finalized = await finalizeLibraryFile(identity, grant.file_id);

  onStage({ stage: "done", itemId: finalized.id as LibraryEntityId });
  return finalized;
}

export function LibraryRoute({ identity }: LibraryRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadStatus>({ stage: "idle" });
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState<SearchState>({ kind: "idle" });

  // ---- Initial fetch ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchLibrary(identity, { limit: 50 })
      .then((list: LibraryListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.items,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load library."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamLibraryEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyLibraryEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
          });
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          const delay = backoffRef.current;
          backoffRef.current = Math.min(
            backoffRef.current * 2,
            RECONNECT_BACKOFF_MAX_MS,
          );
          reconnectTimer = setTimeout(open, delay);
        },
      });
    }

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
    // Gate on `state.kind` (not the whole `state`) so an SSE-driven merge
    // does NOT tear down + reopen the stream.
  }, [identity, state.kind]);

  // ---- Upload (3-stage) --------------------------------------------
  const handleUpload = useCallback(
    async (file: File): Promise<void> => {
      setPendingError(null);
      try {
        const finalized = await uploadLibraryFile(identity, file, setUpload);
        // Optimistically prepend the finalized row; the SSE
        // `library.item_created` will reconcile if the server's view
        // differs.
        setState((prev) => {
          if (prev.kind !== "ready") return prev;
          const idx = prev.items.findIndex((it) => it.id === finalized.id);
          if (idx !== -1) {
            const next = prev.items.slice();
            next[idx] = finalized;
            return { ...prev, items: next };
          }
          return { ...prev, items: [finalized, ...prev.items] };
        });
      } catch (error: unknown) {
        const message = errorMessage(error, "Upload failed.");
        setUpload({ stage: "error", message });
        setPendingError(message);
      }
    },
    [identity],
  );

  const handleFileInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>): void => {
      const file = event.target.files?.[0];
      if (!file) return;
      void handleUpload(file);
      // Reset so the same file can be re-selected.
      event.target.value = "";
    },
    [handleUpload],
  );

  // ---- Delete ------------------------------------------------------
  const handleDelete = useCallback(
    async (id: LibraryEntityId): Promise<void> => {
      setPendingError(null);
      try {
        await deleteLibraryItem(identity, id);
        setState((prev) => {
          if (prev.kind !== "ready") return prev;
          const idx = prev.items.findIndex((it) => it.id === id);
          if (idx === -1) return prev;
          return {
            ...prev,
            items: prev.items.slice(0, idx).concat(prev.items.slice(idx + 1)),
          };
        });
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete item."));
      }
    },
    [identity],
  );

  // ---- Search ------------------------------------------------------
  const handleSearchChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setSearchInput(e.target.value);
  }, []);

  const handleSearchSubmit = useCallback(async (): Promise<void> => {
    const query = searchInput.trim();
    if (query.length === 0) {
      setSearch({ kind: "idle" });
      return;
    }
    setSearch({ kind: "loading", query });
    try {
      const response = await searchLibrary(identity, { query });
      setSearch({ kind: "ready", response });
    } catch (error: unknown) {
      setSearch({
        kind: "error",
        message: errorMessage(error, "Search failed."),
      });
    }
  }, [identity, searchInput]);

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Library destination"
        data-testid="library-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
        }}
      >
        <div
          role="alert"
          data-testid="library-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load library
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="library-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="library-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  const items = state.kind === "ready" ? state.items : [];

  return (
    <section
      aria-label="Library destination"
      data-testid="library-route"
      data-state={state.kind}
      data-item-count={items.length}
      style={{
        height: "100%",
        width: "100%",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      {pendingError !== null && (
        <div
          role="status"
          data-testid="library-route-pending-error"
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            backgroundColor: "var(--color-surface)",
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}

      <div
        data-testid="library-route-toolbar"
        style={{ display: "flex", gap: 12, marginBottom: 16 }}
      >
        <input
          type="file"
          data-testid="library-route-upload-input"
          aria-label="Upload to library"
          onChange={handleFileInputChange}
        />
        <span data-testid="library-route-upload-stage">{upload.stage}</span>
        <input
          type="search"
          data-testid="library-route-search-input"
          aria-label="Search library"
          value={searchInput}
          onChange={handleSearchChange}
          placeholder="Search library…"
        />
        <button
          type="button"
          data-testid="library-route-search-submit"
          onClick={() => {
            void handleSearchSubmit();
          }}
        >
          Search
        </button>
      </div>

      {search.kind === "loading" && (
        <div data-testid="library-route-search-loading">Searching…</div>
      )}
      {search.kind === "error" && (
        <div role="alert" data-testid="library-route-search-error">
          {search.message}
        </div>
      )}
      {search.kind === "ready" && (
        <div data-testid="library-route-search-results">
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            {search.response.hits.length} hits in {search.response.took_ms}ms
          </div>
        </div>
      )}

      {state.kind === "loading" ? (
        <div data-testid="library-route-loading">Loading library…</div>
      ) : items.length === 0 ? (
        <div data-testid="library-route-empty">No items yet.</div>
      ) : (
        <ul
          data-testid="library-route-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {items.map((item) => (
            <li
              key={item.id}
              data-testid="library-route-row"
              data-item-id={item.id}
              data-item-kind={item.kind}
              data-item-index-status={item.index_status}
              style={{
                padding: "12px 0",
                borderBottom: "1px solid var(--color-border)",
                display: "flex",
                gap: 12,
                alignItems: "center",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {item.kind === "page" ? item.title : item.name}
                </div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  {item.kind} · {item.index_status}
                </div>
              </div>
              {item.kind === "page" && (
                <PageAutosaveEditor
                  identity={identity}
                  page={item}
                  onSaved={(updated) => {
                    setState((prev) => {
                      if (prev.kind !== "ready") return prev;
                      const idx = prev.items.findIndex(
                        (it) => it.id === updated.id,
                      );
                      if (idx === -1) return prev;
                      const next = prev.items.slice();
                      next[idx] = updated;
                      return { ...prev, items: next };
                    });
                  }}
                  onError={(message) => setPendingError(message)}
                />
              )}
              <button
                type="button"
                data-testid="library-route-delete"
                data-item-id={item.id}
                onClick={() => {
                  void handleDelete(item.id as LibraryEntityId);
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// PageAutosaveEditor — debounced PATCH for the Page detail body
// ===========================================================================

interface PageAutosaveEditorProps {
  readonly identity: RequestIdentity;
  readonly page: LibraryPage;
  readonly onSaved: (updated: LibraryItem) => void;
  readonly onError: (message: string) => void;
}

/**
 * Debounced PATCH against `/v1/library/{id}` for the page body.
 *
 * Sub-PRD §3.4.2: the editor keeps the user's typing local and fires
 * a save 750ms after the last keystroke. The latest `version_etag`
 * rides as `If-Match` for optimistic concurrency; a 412 reply means
 * the user must refetch the page — surfaced via `onError` so the
 * surrounding LibraryRoute can show the pending banner.
 *
 * Extracted as a per-row component so each page gets its own debounce
 * timer + autosave-in-flight state without cross-talk.
 */
function PageAutosaveEditor({
  identity,
  page,
  onSaved,
  onError,
}: PageAutosaveEditorProps): ReactElement {
  const [markdown, setMarkdown] = useState(page.markdown);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const etagRef = useRef(page.version_etag);

  useEffect(() => {
    etagRef.current = page.version_etag;
  }, [page.version_etag]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    },
    [],
  );

  const flushSave = useCallback(
    async (next: string): Promise<void> => {
      setSaving(true);
      try {
        const updated = await patchLibraryPageBody(
          identity,
          page.id as LibraryPageId,
          { markdown: next },
          etagRef.current,
        );
        onSaved(updated);
      } catch (error: unknown) {
        onError(errorMessage(error, "Could not autosave page."));
      } finally {
        setSaving(false);
      }
    },
    [identity, page.id, onSaved, onError],
  );

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      const next = e.target.value;
      setMarkdown(next);
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(() => {
        void flushSave(next);
      }, PAGE_AUTOSAVE_DEBOUNCE_MS);
    },
    [flushSave],
  );

  return (
    <div
      data-testid="library-route-page-editor"
      data-page-id={page.id}
      data-page-saving={saving ? "true" : "false"}
    >
      <textarea
        data-testid="library-route-page-editor-textarea"
        aria-label={`Edit ${page.title}`}
        value={markdown}
        onChange={handleChange}
        rows={3}
        style={{ minWidth: 200 }}
      />
    </div>
  );
}

// Re-export so external callers (and tests) can drive the orchestrator
// directly without rendering.
export { fetchLibraryItem };
