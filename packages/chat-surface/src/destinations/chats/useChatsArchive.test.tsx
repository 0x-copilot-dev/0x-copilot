// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type {
  ChatsArchive,
  Conversation,
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
import { useChatsArchive } from "./useChatsArchive";

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

  const listResponse = (bucket: string, cursor: unknown) => {
    if (bucket === "pinned") {
      return {
        conversations: config.pinned ?? [],
        next_cursor: null,
        has_more: false,
      };
    }
    if (bucket === "archived") {
      if (cursor !== undefined && config.archivedNext) {
        return {
          conversations: config.archivedNext.conversations,
          next_cursor: config.archivedNext.next_cursor,
          has_more: config.archivedNext.next_cursor !== null,
        };
      }
      return {
        conversations: config.archived ?? [],
        next_cursor: config.archivedNext ? "arch-cursor-1" : null,
        has_more: config.archivedNext !== undefined,
      };
    }
    return {
      conversations: config.recent ?? [],
      next_cursor: null,
      has_more: false,
    };
  };

  const transport: Transport = {
    request: (async (req: TypedRequest) => {
      calls.push(req);
      const path = req.path;
      if (path === "/v1/agent/conversations" && req.method === "GET") {
        return listResponse(String(req.query?.bucket), req.query?.cursor);
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

async function renderChats(config: FakeConfig) {
  const rec = makeTransport(config);
  const hook = renderHook(() => useChatsArchive(), {
    wrapper: wrapper(rec.transport),
  });
  await waitFor(() => expect(hook.result.current.archive?.status).toBe("ok"));
  return { rec, hook };
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
});
