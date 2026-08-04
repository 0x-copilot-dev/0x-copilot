// useChatsArchive — the D1 controller for the Chats surface (PRD-09).
//
// ONE transport-backed data hook owns the whole Chats read/write model so the
// behaviour is written once and cannot drift per host (it already had: the web
// `chatsApi.ts` and desktop `destinationBinders.tsx` converged on the bucket
// rule but diverged on field reads). Both hosts' binders collapse to navigation
// callbacks; `ChatsArchive.tsx` stays pure-presentation.
//
// Owns: three bucket-scoped cursored fetches (D3), an SSE store-tail merge (D4),
// `setPinned`/`setArchived` optimistic mutations with rollback (D2), and
// `loadMore(bucket)` keyset pagination. It consumes PRD-03's per-row
// `toChatArchiveRow` projector — the ONLY place a `Conversation` becomes a row.
//
// It also owns the PROJECT SCOPE axis (PRD-07): the same three buckets, narrowed
// to one project. Scoping lives here rather than in a wrapper hook because all
// three of the hook's moving parts have to agree on it — the page-1 fetch, every
// keyset page, and the live tail — and a wrapper could only have filtered the
// last one, after the server had already spent the page budget on rows the
// caller cannot see.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  ChatArchiveRow,
  ChatsArchive as ChatsArchiveData,
  Conversation,
  ConversationId,
  ConversationListResponse,
  ProjectId,
  SectionResult,
} from "@0x-copilot/api-types";

// Structural shape of the D4 `conversation_changed` SSE frame. The canonical
// contract is `ConversationStreamEnvelope` in `@0x-copilot/api-types`; the hook
// only consumes it structurally (parsed JSON), so it declares the minimal shape
// locally rather than coupling the hook's typecheck to a freshly-added
// cross-package type.
interface ConversationStreamEnvelope {
  readonly event_type: "conversation_changed";
  readonly conversation: Conversation;
  readonly cursor: string;
}

import { toChatArchiveRow } from "../../projections/chats";
import { useTransport } from "../../providers/TransportProvider";
import type { Transport } from "../../ports/Transport";

import { CHATS_SECTION_ORDER, type ChatsSectionKey } from "./ChatsArchive";

const PAGE_LIMIT = 30;
const CONVERSATIONS_PATH = "/v1/agent/conversations";

/**
 * The project-scope query key. Bracketed because that is the filter convention
 * the FACADE speaks (`filter[<axis>]`, as on `/v1/library`, `/v1/team`,
 * `/v1/memory`); it translates the alias to ai-backend's plain `project_id`.
 * Apps only ever talk to the facade, so this is the spelling that belongs here.
 */
const PROJECT_FILTER_KEY = "filter[project_id]";

/** Which server `bucket` each Chats section maps to. */
const BUCKET_PARAM: Readonly<Record<ChatsSectionKey, string>> = {
  pinned: "pinned",
  recent: "recent",
  archived: "archived",
};

export interface ChatsArchiveOptions {
  /**
   * Narrow every bucket to one project.
   *
   * `undefined`/`null` means UNSCOPED (every chat) — deliberately NOT
   * "unfiled". The list endpoint has no way to ask for `project_id IS NULL`:
   * omitting the filter is how you say "no filter", so there is no query that
   * returns only unfiled chats, and filtering a keyset page client-side would
   * silently drop rows the server had already counted against the page limit.
   * The axis is therefore exactly "one project, or everything".
   */
  readonly projectId?: ProjectId | null;
}

export interface ChatsArchiveController {
  /** 4-state driver for `<ChatsArchive>`: `null` while first-loading. */
  readonly archive: SectionResult<ChatsArchiveData> | null;
  /** Whether each bucket has an older keyset page to load. */
  readonly hasMore: Readonly<Record<ChatsSectionKey, boolean>>;
  /** Fetch the next keyset page for one bucket and append it. */
  readonly onLoadMore: (bucket: ChatsSectionKey) => void;
  /** Pin / unpin a conversation (optimistic; rolls back on failure). */
  readonly onTogglePin: (id: ConversationId, pinned: boolean) => void;
  /** Archive / unarchive a conversation (optimistic; rolls back on failure). */
  readonly onToggleArchive: (id: ConversationId, archived: boolean) => void;
  /** Re-run the initial three-bucket fetch (error-state Retry). */
  readonly retry: () => void;
}

interface BucketState {
  rows: ChatArchiveRow[];
  cursor: string | null;
  hasMore: boolean;
}

type BucketMap = Record<ChatsSectionKey, BucketState>;

function emptyBuckets(): BucketMap {
  return {
    pinned: { rows: [], cursor: null, hasMore: false },
    recent: { rows: [], cursor: null, hasMore: false },
    archived: { rows: [], cursor: null, hasMore: false },
  };
}

/** Classify a projected row into its bucket (archived wins; then pinned). */
function bucketFor(row: ChatArchiveRow): ChatsSectionKey {
  if (row.status === "archived") return "archived";
  if (row.pinned) return "pinned";
  return "recent";
}

/** Newest-first by ISO `updated_at`, id as a stable tiebreaker. */
function byUpdatedDesc(a: ChatArchiveRow, b: ChatArchiveRow): number {
  if (a.updated_at !== b.updated_at) {
    return a.updated_at < b.updated_at ? 1 : -1;
  }
  return a.id < b.id ? 1 : -1;
}

function toArchiveData(buckets: BucketMap): ChatsArchiveData {
  return {
    pinned: buckets.pinned.rows,
    recent: buckets.recent.rows,
    archived: buckets.archived.rows,
  };
}

async function fetchBucket(
  transport: Transport,
  bucket: ChatsSectionKey,
  cursor: string | null,
  projectId: ProjectId | null,
  signal?: AbortSignal,
): Promise<ConversationListResponse> {
  return transport.request<ConversationListResponse>({
    method: "GET",
    path: CONVERSATIONS_PATH,
    query: {
      bucket: BUCKET_PARAM[bucket],
      limit: PAGE_LIMIT,
      ...(cursor !== null ? { cursor } : {}),
      // Spread-when-set rather than `: projectId ?? undefined` so an unscoped
      // caller's query object is byte-identical to the pre-scope one — the key
      // is absent, not present-and-undefined for the transport to reason about.
      ...(projectId !== null ? { [PROJECT_FILTER_KEY]: projectId } : {}),
    },
    signal,
  });
}

/** Remove a row id from every bucket, returning the removed row if found. */
function detach(buckets: BucketMap, id: ConversationId): ChatArchiveRow | null {
  let found: ChatArchiveRow | null = null;
  for (const key of CHATS_SECTION_ORDER) {
    const idx = buckets[key].rows.findIndex((row) => row.id === id);
    if (idx >= 0) {
      found = buckets[key].rows[idx];
      buckets[key].rows = [
        ...buckets[key].rows.slice(0, idx),
        ...buckets[key].rows.slice(idx + 1),
      ];
    }
  }
  return found;
}

/** Insert a row into its bucket, keeping newest-first order. */
function attach(buckets: BucketMap, row: ChatArchiveRow): void {
  const key = bucketFor(row);
  buckets[key].rows = [...buckets[key].rows, row].sort(byUpdatedDesc);
}

function cloneBuckets(buckets: BucketMap): BucketMap {
  const next = emptyBuckets();
  for (const key of CHATS_SECTION_ORDER) {
    next[key] = { ...buckets[key], rows: [...buckets[key].rows] };
  }
  return next;
}

export function useChatsArchive(
  options?: ChatsArchiveOptions,
): ChatsArchiveController {
  const transport = useTransport();
  // Normalised to a PRIMITIVE before it reaches any dependency array. Callers
  // pass an object literal (`useChatsArchive({ projectId })`) whose identity is
  // new every render, so depending on `options` itself would refetch forever.
  // An empty id collapses to unscoped: `filter[project_id]=` is a filter that
  // matches nothing, which is never what an empty string was meant to say.
  const requestedProjectId = options?.projectId ?? null;
  const projectId =
    requestedProjectId !== null && requestedProjectId.length > 0
      ? requestedProjectId
      : null;

  const [buckets, setBuckets] = useState<BucketMap>(emptyBuckets);
  const [phase, setPhase] = useState<"loading" | "ok" | "error">("loading");
  const [error, setError] = useState<string | undefined>(undefined);
  const [reloadToken, setReloadToken] = useState(0);
  const bucketsRef = useRef(buckets);
  bucketsRef.current = buckets;
  // The scope the currently-held buckets were fetched under. Seeded with the
  // mount scope so the first load does not count as a change.
  const loadedScopeRef = useRef<ProjectId | null>(projectId);

  // === Initial three-bucket fetch (D3 / DoD #8a) ==========================
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setPhase("loading");
    if (loadedScopeRef.current !== projectId) {
      // A scope change invalidates the loaded rows AND their keyset cursors: a
      // watermark only orders rows within the scope it was issued for. Clear
      // both the state and the ref `onLoadMore` reads — the ref matters,
      // because a load-more firing before the refetch lands would otherwise
      // append the previous project's next page onto the new list.
      loadedScopeRef.current = projectId;
      const cleared = emptyBuckets();
      bucketsRef.current = cleared;
      setBuckets(cleared);
    }
    void (async () => {
      try {
        const responses = await Promise.all(
          CHATS_SECTION_ORDER.map((bucket) =>
            fetchBucket(transport, bucket, null, projectId, controller.signal),
          ),
        );
        if (cancelled) return;
        const next = emptyBuckets();
        CHATS_SECTION_ORDER.forEach((bucket, i) => {
          const response = responses[i];
          next[bucket] = {
            rows: (response?.conversations ?? [])
              .filter((c) => c.deleted_at == null)
              .map(toChatArchiveRow)
              .sort(byUpdatedDesc),
            cursor: response?.next_cursor ?? null,
            hasMore: response?.has_more ?? false,
          };
        });
        setBuckets(next);
        setPhase("ok");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Couldn't load chats.");
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [transport, reloadToken, projectId]);

  // === Live tail (D4 / DoD #8c) ===========================================
  // Merge each `conversation_changed` envelope through the same projector with
  // NO additional transport call. The subscription resumes from the newest
  // watermark the client holds.
  const initialAfter = useMemo(() => {
    const cursors = CHATS_SECTION_ORDER.map((k) => buckets[k].cursor).filter(
      (c): c is string => c !== null,
    );
    return cursors[0];
    // Deliberately NOT depending on `buckets`: only the loaded token and the
    // load settling recompute this, so `after` stays stable for the life of a
    // load. No `exhaustive-deps` disable belongs here — this package's eslint
    // config is boundary-enforcement only and loads no react-hooks plugin, so a
    // directive naming that rule is itself a lint error.
  }, [reloadToken, phase === "ok"]);

  useEffect(() => {
    if (phase !== "ok") return;
    const subscription = transport.subscribeServerSentEvents({
      path: `${CONVERSATIONS_PATH}/stream`,
      eventName: "conversation_changed",
      query: initialAfter !== undefined ? { after: initialAfter } : {},
      onMessage: (raw) => {
        let envelope: ConversationStreamEnvelope;
        try {
          envelope = JSON.parse(raw) as ConversationStreamEnvelope;
        } catch {
          return;
        }
        const conversation = envelope.conversation as Conversation | undefined;
        if (conversation?.conversation_id == null) return;
        const row = toChatArchiveRow(conversation);
        setBuckets((prev) => {
          const next = cloneBuckets(prev);
          const removed = detach(next, row.id);
          // The tail must be scoped in BOTH directions, and the second one is
          // the easy miss. The stream is account-wide — it carries no
          // `filter[project_id]`, so every chat the user touches arrives here —
          // which means a scoped list would grow rows from other projects the
          // moment they changed. That is the obvious direction. The other: a
          // chat REFILED out of this scope arrives as an ordinary update, and
          // leaving it attached would strand a row that no refetch would ever
          // return again. Detaching first and re-attaching only when the row
          // still belongs settles both with one rule.
          const belongs = projectId === null || row.project_id === projectId;
          const keep = belongs && conversation.deleted_at == null;
          // Nothing to add and nothing to drop: hand back `prev` so an
          // out-of-scope envelope costs no render at all.
          if (!keep && removed === null) return prev;
          if (keep) attach(next, row);
          return next;
        });
      },
    });
    return () => subscription.close();
  }, [transport, phase, initialAfter, projectId]);

  // === loadMore (D3 / DoD #8b) ============================================
  const onLoadMore = useCallback(
    (bucket: ChatsSectionKey) => {
      const state = bucketsRef.current[bucket];
      if (!state.hasMore || state.cursor === null) return;
      const cursor = state.cursor;
      void (async () => {
        try {
          const response = await fetchBucket(
            transport,
            bucket,
            cursor,
            projectId,
          );
          const incoming = (response?.conversations ?? [])
            .filter((c) => c.deleted_at == null)
            .map(toChatArchiveRow);
          setBuckets((prev) => {
            const next = cloneBuckets(prev);
            const existing = new Set(next[bucket].rows.map((r) => r.id));
            next[bucket] = {
              rows: [
                ...next[bucket].rows,
                ...incoming.filter((r) => !existing.has(r.id)),
              ].sort(byUpdatedDesc),
              cursor: response?.next_cursor ?? null,
              hasMore: response?.has_more ?? false,
            };
            return next;
          });
        } catch {
          // A failed "load more" leaves the current page intact; the ghost
          // button stays for a retry.
        }
      })();
    },
    [transport, projectId],
  );

  // === Optimistic mutations (D2 / DoD #8d, #8e) ===========================
  const runMutation = useCallback(
    (
      id: ConversationId,
      mutate: (row: ChatArchiveRow) => ChatArchiveRow,
      request: () => Promise<unknown>,
    ) => {
      const snapshot = cloneBuckets(bucketsRef.current);
      setBuckets((prev) => {
        const next = cloneBuckets(prev);
        const row = detach(next, id);
        if (row === null) return prev;
        attach(next, mutate(row));
        return next;
      });
      void request().catch(() => {
        // Rollback to the pre-mutation snapshot on server rejection.
        setBuckets(snapshot);
      });
    },
    [],
  );

  const onTogglePin = useCallback(
    (id: ConversationId, pinned: boolean) => {
      runMutation(
        id,
        (row) => ({ ...row, pinned }),
        () =>
          transport.request({
            method: "POST",
            path: `${CONVERSATIONS_PATH}/${id}/pin`,
            body: { pinned },
          }),
      );
    },
    [transport, runMutation],
  );

  const onToggleArchive = useCallback(
    (id: ConversationId, archived: boolean) => {
      runMutation(
        id,
        (row) => ({
          ...row,
          // Archive wins the chip; unarchiving drops to "done" until the tail
          // corrects it from the real run status.
          status: archived ? "archived" : "done",
        }),
        () =>
          transport.request({
            method: "PATCH",
            path: `${CONVERSATIONS_PATH}/${id}`,
            body: { archived },
          }),
      );
    },
    [transport, runMutation],
  );

  const retry = useCallback(() => setReloadToken((n) => n + 1), []);

  const archive = useMemo<SectionResult<ChatsArchiveData> | null>(() => {
    if (phase === "loading") return null;
    if (phase === "error") return { status: "error", error };
    return { status: "ok", data: toArchiveData(buckets) };
  }, [phase, error, buckets]);

  const hasMore = useMemo(
    () => ({
      pinned: buckets.pinned.hasMore,
      recent: buckets.recent.hasMore,
      archived: buckets.archived.hasMore,
    }),
    [buckets],
  );

  return { archive, hasMore, onLoadMore, onTogglePin, onToggleArchive, retry };
}
