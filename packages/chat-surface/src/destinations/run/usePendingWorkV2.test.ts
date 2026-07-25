// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingWorkV2Response } from "@0x-copilot/api-types";
import type {
  Session,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { usePendingWorkV2 } from "./usePendingWorkV2";

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

function response(
  items: PendingWorkV2Response["items"] = [],
  over: Partial<PendingWorkV2Response> = {},
): PendingWorkV2Response {
  return {
    v: 2,
    items,
    warnings: [],
    next_cursor: null,
    has_more: false,
    ...over,
  };
}

function effect(
  over: Partial<PendingWorkV2Response["items"][number]> = {},
): PendingWorkV2Response["items"][number] {
  return {
    run_id: "run_a",
    subject_kind: "effect",
    subject_id: "stage_a",
    status: "held",
    opened_sequence_no: 2,
    latest_sequence_no: 2,
    ...over,
  };
}

function makeTransport(
  handler: (request: TypedRequest) => unknown | Promise<unknown>,
): Transport {
  return {
    request: (async (request: TypedRequest) =>
      handler(request)) as Transport["request"],
    subscribeServerSentEvents: () => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => CAPABILITIES,
  };
}

describe("usePendingWorkV2", () => {
  it("is inert when disabled", async () => {
    const calls = vi.fn();
    const transport = makeTransport((request) => {
      calls(request.path);
      return response();
    });
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, false, "run_a", 1),
    );
    await Promise.resolve();
    expect(calls).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("fetches through Transport and projects a verified response", async () => {
    const calls: TypedRequest[] = [];
    const transport = makeTransport((request) => {
      calls.push(request);
      return response([effect()]);
    });
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, true, "run_a", 1),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(calls).toEqual([
      expect.objectContaining({
        method: "GET",
        path: "/v1/agent/pending-work-v2",
      }),
    ]);
    expect(calls[0]).not.toHaveProperty("query");
    expect(result.current.cards).toEqual([
      expect.objectContaining({
        runId: "run_a",
        subjectKind: "effect",
        subjectId: "stage_a",
      }),
    ]);
  });

  it("rejects hostile response content without claiming an empty queue", async () => {
    const hostile = {
      v: 2,
      items: [
        {
          ...effect(),
          title: '<img src=x onerror="alert(1)">',
        },
      ],
      warnings: [],
      next_cursor: null,
      has_more: false,
    };
    const transport = makeTransport(() => hostile);
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, true, "run_a", 1),
    );

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.cards).toEqual([]);
  });

  it("fails soft when the cohort endpoint is unavailable", async () => {
    const unavailable = Object.assign(new Error("not found"), { status: 404 });
    const transport = makeTransport(() => Promise.reject(unavailable));
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, true, "run_a", 1),
    );

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.cards).toEqual([]);
    expect(result.current.hasOmittedRuns).toBe(false);
  });

  it("keeps last verified cards when a later request fails", async () => {
    let fail = false;
    const transport = makeTransport(() => {
      if (fail) throw new Error("endpoint unavailable");
      return response([effect()]);
    });
    const { result, rerender } = renderHook(
      ({ refreshKey }: { refreshKey: number }) =>
        usePendingWorkV2(transport, true, "run_a", refreshKey),
      { initialProps: { refreshKey: 1 } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    fail = true;
    rerender({ refreshKey: 2 });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.cards).toHaveLength(1);
  });

  it("drops a late response after the active run changes", async () => {
    const resolvers: Array<(value: unknown) => void> = [];
    const transport = makeTransport(
      () =>
        new Promise<unknown>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    const { result, rerender } = renderHook(
      ({ runId }: { runId: string }) =>
        usePendingWorkV2(transport, true, runId, 1),
      { initialProps: { runId: "run_a" } },
    );
    await waitFor(() => expect(resolvers).toHaveLength(1));

    rerender({ runId: "run_b" });
    // Resolve the old run's request. The hook must not publish it into B.
    resolvers[0]?.(response([effect({ run_id: "run_a" })]));
    await waitFor(() => expect(resolvers).toHaveLength(2));
    resolvers[1]?.(
      response([effect({ run_id: "run_b", subject_id: "stage_b" })]),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.cards).toEqual([
      expect.objectContaining({ runId: "run_b", subjectId: "stage_b" }),
    ]);
  });

  it("appends a cursor page only when explicitly requested", async () => {
    const calls: TypedRequest[] = [];
    const transport = makeTransport((request) => {
      calls.push(request);
      if (request.query?.cursor === "next+token") {
        return response([effect({ subject_id: "stage_b" })]);
      }
      return response([effect()], {
        next_cursor: "next+token",
        has_more: true,
      });
    });
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, true, "run_a", 1),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.hasMore).toBe(true);
    result.current.loadMore();
    await waitFor(() => expect(result.current.cards).toHaveLength(2));
    expect(calls).toEqual([
      expect.objectContaining({
        path: "/v1/agent/pending-work-v2",
      }),
      expect.objectContaining({
        path: "/v1/agent/pending-work-v2",
        query: { cursor: "next+token" },
      }),
    ]);
    expect(calls[0]).not.toHaveProperty("query");
  });

  it("retains a safe partial-result marker without exposing omitted run ids", async () => {
    const transport = makeTransport(() =>
      response([effect()], {
        warnings: [{ run_id: "run_omitted", status: "omitted" }],
      }),
    );
    const { result } = renderHook(() =>
      usePendingWorkV2(transport, true, "run_a", 1),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.hasOmittedRuns).toBe(true);
    expect(result.current.cards).toHaveLength(1);
  });
});
