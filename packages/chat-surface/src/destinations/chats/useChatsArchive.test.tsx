// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type {
  ChatsArchive,
  Conversation,
  ProjectId,
  SectionResult,
} from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../../providers/TransportProvider";
import { useChatsArchive, type ChatsArchiveOptions } from "./useChatsArchive";

const PROJECT_FILTER_KEY = "filter[project_id]";

function conv(
  partial: Partial<Conversation> & { conversation_id: string },
): Conversation {
  return {
    org_id: "org",
    user_id: "user",
    assistant_id: "assistant",
    title: partial.conversation_id,
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    pinned: false,
    ...partial,
  } as Conversation;
}

interface FakeConfig {
  pinned?: Conversation[];
  recent?: Conversation[];
  archived?: Conversation[];
  archivedNext?: { conversations: Conversation[]; next_cursor: string | null };
  rejectPin?: boolean;
}

interface Recorder {
  transport: Transport;
  calls: TypedRequest[];
  sse: { onMessage?: (raw: string) => void };
  patchBodies: unknown[];
}

function makeTransport(config: FakeConfig): Recorder {
  const calls: TypedRequest[] = [];
  const patchBodies: unknown[] = [];
  const sse: { onMessage?: (raw: string) => void } = {};

  // Stand in for the server's project scoping: when the request carries
  // `filter[project_id]`, only rows filed under it come back. Applied to the
  // SAME dataset every bucket serves, so a scoped keyset page behaves like the
  // real one instead of needing a second fixture shape.
  const scoped = (rows: Conversation[], project: unknown): Conversation[] =>
    typeof project === "string"
      ? rows.filter((c) => c.project_id === project)
      : rows;

  const listResponse = (bucket: string, cursor: unknown, project: unknown) => {
    if (bucket === "pinned") {
      return {
        conversations: scoped(config.pinned ?? [], project),
        next_cursor: null,
        has_more: false,
      };
    }
    if (bucket === "archived") {
      if (cursor !== undefined && config.archivedNext) {
        return {
          conversations: scoped(config.archivedNext.conversations, project),
          next_cursor: config.archivedNext.next_cursor,
          has_more: config.archivedNext.next_cursor !== null,
        };
      }
      return {
        conversations: scoped(config.archived ?? [], project),
        next_cursor: config.archivedNext ? "arch-cursor-1" : null,
        has_more: config.archivedNext !== undefined,
      };
    }
    return {
      conversations: scoped(config.recent ?? [], project),
      next_cursor: null,
      has_more: false,
    };
  };

  const transport: Transport = {
    request: (async (req: TypedRequest) => {
      calls.push(req);
      const path = req.path;
      if (path === "/v1/agent/conversations" && req.method === "GET") {
        return listResponse(
          String(req.query?.bucket),
          req.query?.cursor,
          req.query?.[PROJECT_FILTER_KEY],
        );
      }
      if (path.endsWith("/pin")) {
        if (config.rejectPin) throw new Error("pin failed");
        return {};
      }
      if (req.method === "PATCH") {
        patchBodies.push(req.body);
        return {};
      }
      return {};
    }) as Transport["request"],
    subscribeServerSentEvents: (opts: SseSubscribeOptions) => {
      sse.onMessage = opts.onMessage;
      return { close: () => undefined };
    },
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, calls, sse, patchBodies };
}

function wrapper(transport: Transport) {
  return ({ children }: { children: ReactNode }) =>
    createElement(TransportProvider, { transport, children });
}

/**
 * Render the controller and wait for the first load. `options` is threaded as a
 * rerenderable prop (rather than closed over) so a test can change the project
 * scope on a MOUNTED hook — the refetch-on-scope-change path only exists on a
 * rerender, and a fresh `renderHook` would silently test a fresh mount instead.
 */
async function renderChats(config: FakeConfig, options?: ChatsArchiveOptions) {
  const rec = makeTransport(config);
  const hook = renderHook(
    (props: { options?: ChatsArchiveOptions }) =>
      useChatsArchive(props.options),
    { wrapper: wrapper(rec.transport), initialProps: { options } },
  );
  await waitFor(() => expect(hook.result.current.archive?.status).toBe("ok"));
  return { rec, hook };
}

/** Every `GET /v1/agent/conversations` the fake recorded, in order. */
function listCalls(rec: Recorder): TypedRequest[] {
  return rec.calls.filter(
    (c) => c.path === "/v1/agent/conversations" && c.method === "GET",
  );
}

/**
 * Assert the controller is in its `ok` section-state and return the loaded
 * buckets. `SectionResult` is not a discriminated union (`data` is optional
 * even when `status === "ok"`), so the assertions read the buckets through this
 * guard rather than a `status === "ok" &&` short-circuit that leaves `data`
 * typed as possibly-undefined. Inside a `waitFor`, a throw here simply retries.
 */
function archiveData(
  archive: SectionResult<ChatsArchive> | null,
): ChatsArchive {
  expect(archive?.status).toBe("ok");
  if (!archive || archive.status !== "ok" || archive.data === undefined) {
    throw new Error("expected archive to be ok with loaded data");
  }
  return archive.data;
}

/** Every loaded row id across the three buckets — the "is it on screen" set. */
function allRowIds(archive: ChatsArchive): string[] {
  return [...archive.pinned, ...archive.recent, ...archive.archived].map(
    (row) => row.id as string,
  );
}

/** Push one `conversation_changed` frame through the fake's live tail. */
function emit(rec: Recorder, conversation: Conversation): void {
  act(() => {
    rec.sse.onMessage?.(
      JSON.stringify({
        event_type: "conversation_changed",
        cursor: "c1",
        conversation,
      }),
    );
  });
}

describe("useChatsArchive", () => {
  it("(a) issues three bucket-scoped requests on mount", async () => {
    const { rec } = await renderChats({});
    const buckets = rec.calls
      .filter((c) => c.path === "/v1/agent/conversations")
      .map((c) => c.query?.bucket);
    expect(new Set(buckets)).toEqual(new Set(["pinned", "recent", "archived"]));
  });

  it("(b) loadMore(archived) appends and issues no second page-1 request", async () => {
    const { rec, hook } = await renderChats({
      archived: [conv({ conversation_id: "a1", status: "archived" })],
      archivedNext: {
        conversations: [conv({ conversation_id: "a2", status: "archived" })],
        next_cursor: null,
      },
    });
    const page1Archived = () =>
      rec.calls.filter(
        (c) =>
          c.path === "/v1/agent/conversations" &&
          c.query?.bucket === "archived" &&
          c.query?.cursor === undefined,
      ).length;
    expect(page1Archived()).toBe(1);

    act(() => hook.result.current.onLoadMore("archived"));
    await waitFor(() =>
      expect(archiveData(hook.result.current.archive).archived.length).toBe(2),
    );
    // The append used the cursor; no SECOND page-1 (cursor-less) archived fetch.
    expect(page1Archived()).toBe(1);
    const withCursor = rec.calls.filter(
      (c) =>
        c.query?.bucket === "archived" && c.query?.cursor === "arch-cursor-1",
    );
    expect(withCursor.length).toBe(1);
  });

  it("(c) a run-cleared SSE envelope re-renders the row as done with no extra call", async () => {
    const { rec, hook } = await renderChats({
      recent: [conv({ conversation_id: "r1", latest_run_status: "running" })],
    });
    const beforeCalls = rec.calls.length;
    const rowBefore = archiveData(hook.result.current.archive).recent[0];
    expect(rowBefore?.status).toBe("running");

    act(() => {
      rec.sse.onMessage?.(
        JSON.stringify({
          event_type: "conversation_changed",
          cursor: "c1",
          conversation: conv({
            conversation_id: "r1",
            // A finished run clears the active-run projection to null — no
            // adapter emits a terminal `latest_run_status` (see
            // ACTIVE_AGENT_RUN_STATUSES). `null` projects the row to "done".
            latest_run_status: null,
            updated_at: "2026-01-02T00:00:00Z",
          }),
        }),
      );
    });

    await waitFor(() => {
      const row = archiveData(hook.result.current.archive).recent.find(
        (r) => r.id === "r1",
      );
      expect(row?.status).toBe("done");
    });
    // No additional transport request happened for the merge.
    expect(rec.calls.length).toBe(beforeCalls);
  });

  it("(d) setPinned moves the row to pinned optimistically and rolls back on failure", async () => {
    const { hook } = await renderChats({
      recent: [conv({ conversation_id: "r1" })],
      rejectPin: true,
    });
    act(() => hook.result.current.onTogglePin("r1" as never, true));
    // Optimistic: it left recent and joined pinned immediately.
    expect(
      archiveData(hook.result.current.archive).pinned.some(
        (r) => r.id === "r1",
      ),
    ).toBe(true);
    // The request rejects → rollback to recent.
    await waitFor(() => {
      const inRecent = archiveData(hook.result.current.archive).recent.some(
        (r) => r.id === "r1",
      );
      expect(inRecent).toBe(true);
    });
    expect(archiveData(hook.result.current.archive).pinned.length).toBe(0);
  });

  it("(e) setArchived toggles buckets, each with exactly one PATCH", async () => {
    const { rec, hook } = await renderChats({
      recent: [conv({ conversation_id: "r1" })],
    });
    act(() => hook.result.current.onToggleArchive("r1" as never, true));
    await waitFor(() =>
      expect(
        archiveData(hook.result.current.archive).archived.some(
          (r) => r.id === "r1",
        ),
      ).toBe(true),
    );
    expect(rec.patchBodies).toEqual([{ archived: true }]);

    act(() => hook.result.current.onToggleArchive("r1" as never, false));
    await waitFor(() =>
      expect(
        archiveData(hook.result.current.archive).recent.some(
          (r) => r.id === "r1",
        ),
      ).toBe(true),
    );
    expect(rec.patchBodies).toEqual([{ archived: true }, { archived: false }]);
  });

  // === Project scope (PRD-07) ============================================

  it("(f) a scoped controller sends filter[project_id] on page 1 and on every keyset page", async () => {
    const { rec, hook } = await renderChats(
      {
        recent: [
          conv({ conversation_id: "p1-a", project_id: "p1" }),
          conv({ conversation_id: "p2-a", project_id: "p2" }),
        ],
        archived: [
          conv({
            conversation_id: "p1-old",
            status: "archived",
            project_id: "p1",
          }),
        ],
        archivedNext: {
          conversations: [
            conv({
              conversation_id: "p1-older",
              status: "archived",
              project_id: "p1",
            }),
          ],
          next_cursor: null,
        },
      },
      { projectId: "p1" as ProjectId },
    );

    const page1 = listCalls(rec);
    expect(page1.length).toBe(3);
    expect(page1.every((c) => c.query?.[PROJECT_FILTER_KEY] === "p1")).toBe(
      true,
    );
    expect(allRowIds(archiveData(hook.result.current.archive)).sort()).toEqual([
      "p1-a",
      "p1-old",
    ]);

    // The scope has to ride the cursor too: a page-2 fetch that dropped it
    // would paginate the unscoped list and splice foreign rows onto the end.
    act(() => hook.result.current.onLoadMore("archived"));
    await waitFor(() =>
      expect(archiveData(hook.result.current.archive).archived.length).toBe(2),
    );
    const paged = listCalls(rec).filter(
      (c) => c.query?.cursor === "arch-cursor-1",
    );
    expect(paged.length).toBe(1);
    expect(paged[0]?.query?.[PROJECT_FILTER_KEY]).toBe("p1");
  });

  it("(g) an unscoped controller sends no project key at all and still merges filed chats", async () => {
    const { rec, hook } = await renderChats({
      recent: [conv({ conversation_id: "r1" })],
    });
    for (const call of listCalls(rec)) {
      expect(Object.keys(call.query ?? {})).not.toContain(PROJECT_FILTER_KEY);
      // Not merely the wrong spelling of the key — no project axis at all.
      expect(
        Object.keys(call.query ?? {}).some((k) => k.includes("project")),
      ).toBe(false);
    }

    // Unscoped means "everything", not "unfiled": a chat that IS filed under a
    // project must still merge off the tail.
    emit(
      rec,
      conv({
        conversation_id: "filed",
        project_id: "p9",
        updated_at: "2026-01-02T00:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(allRowIds(archiveData(hook.result.current.archive))).toContain(
        "filed",
      ),
    );
  });

  it("(h) changing the scope refetches from scratch and drops the previous scope's rows", async () => {
    const { rec, hook } = await renderChats(
      {
        recent: [
          conv({ conversation_id: "p1-a", project_id: "p1" }),
          conv({ conversation_id: "p2-a", project_id: "p2" }),
        ],
      },
      { projectId: "p1" as ProjectId },
    );
    expect(allRowIds(archiveData(hook.result.current.archive))).toEqual([
      "p1-a",
    ]);

    hook.rerender({ options: { projectId: "p2" as ProjectId } });

    await waitFor(() =>
      expect(allRowIds(archiveData(hook.result.current.archive))).toEqual([
        "p2-a",
      ]),
    );
    // Three fresh page-1 fetches under the new scope — the list is re-read from
    // the server, not re-filtered client-side out of the old page.
    const p2Page1 = listCalls(rec).filter(
      (c) =>
        c.query?.[PROJECT_FILTER_KEY] === "p2" && c.query?.cursor === undefined,
    );
    expect(p2Page1.length).toBe(3);
  });

  it("(i) the scoped tail ignores an out-of-scope envelope and merges an in-scope one", async () => {
    const { rec, hook } = await renderChats(
      { recent: [conv({ conversation_id: "p1-a", project_id: "p1" })] },
      { projectId: "p1" as ProjectId },
    );
    const beforeCalls = rec.calls.length;

    emit(
      rec,
      conv({
        conversation_id: "p2-new",
        project_id: "p2",
        updated_at: "2026-01-03T00:00:00Z",
      }),
    );
    emit(
      rec,
      conv({
        conversation_id: "p1-new",
        project_id: "p1",
        updated_at: "2026-01-02T00:00:00Z",
      }),
    );

    await waitFor(() =>
      expect(allRowIds(archiveData(hook.result.current.archive))).toContain(
        "p1-new",
      ),
    );
    expect(allRowIds(archiveData(hook.result.current.archive))).not.toContain(
      "p2-new",
    );
    // Scoping the tail is a projection decision — it costs no extra fetch.
    expect(rec.calls.length).toBe(beforeCalls);
  });

  it("(j) a chat refiled out of the scope leaves the scoped list", async () => {
    const { rec, hook } = await renderChats(
      {
        recent: [
          conv({ conversation_id: "moves", project_id: "p1" }),
          conv({ conversation_id: "stays", project_id: "p1" }),
        ],
      },
      { projectId: "p1" as ProjectId },
    );
    expect(allRowIds(archiveData(hook.result.current.archive)).sort()).toEqual([
      "moves",
      "stays",
    ]);

    emit(
      rec,
      conv({
        conversation_id: "moves",
        project_id: "p2",
        updated_at: "2026-01-04T00:00:00Z",
      }),
    );

    await waitFor(() =>
      expect(allRowIds(archiveData(hook.result.current.archive))).toEqual([
        "stays",
      ]),
    );
  });
});
