// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingWorkResponse } from "@0x-copilot/api-types";
import type {
  Session,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import type { PendingCard } from "./pendingCardsProjection";
import { usePendingWork } from "./usePendingWork";

const OPEN_RUN = "run_open";

function liveGate(runId = OPEN_RUN, gateId = "g_live"): PendingCard {
  return {
    itemKind: "gate",
    runId,
    gateId,
    stageId: null,
    surfaceId: null,
    title: "live gate",
    connector: "linear",
    ledgerId: "ra7f·001",
    openedSeq: 1,
    rowsPending: null,
    rowsTotal: null,
  };
}

function response(
  items: PendingWorkResponse["items"],
  agents: PendingWorkResponse["agents"] = [],
): PendingWorkResponse {
  return { v: 1, items, agents };
}

function otherRunItem(
  runId: string,
  gateId: string,
): PendingWorkResponse["items"][number] {
  return {
    v: 1,
    item_kind: "gate",
    run_id: runId,
    conversation_id: `conv_${runId}`,
    conversation_title: "Other",
    gate_id: gateId,
    stage_id: null,
    surface_id: null,
    title: "other gate",
    connector: "gmail",
    op: null,
    ledger_id: "rb00·002",
    opened_sequence_no: 2,
    opened_at: "2026-07-24T00:00:00+00:00",
    rows_pending: null,
    rows_total: null,
  };
}

function makeTransport(
  respond: () => PendingWorkResponse | Promise<PendingWorkResponse>,
  onCall?: (path: string) => void,
): Transport {
  return {
    request: (async (req: TypedRequest) => {
      onCall?.(req.path);
      if (req.path === "/v1/agent/pending-work") return respond();
      return {};
    }) as Transport["request"],
    subscribeServerSentEvents: () => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

describe("usePendingWork", () => {
  it("disabled: issues no request and passes the live cards through", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => response([]),
      () => request(),
    );
    const live = [liveGate()];
    const { result } = renderHook(() =>
      usePendingWork(transport, false, OPEN_RUN, live, 1),
    );
    // Give any (erroneous) effect a tick.
    await Promise.resolve();
    expect(request).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    expect(result.current.cards).toEqual(live);
  });

  it("enabled: fetches and merges — open-run items are replaced by live cards", async () => {
    // The server returns an item for the OPEN run (stale) + one for another run.
    const items = [
      otherRunItem(OPEN_RUN, "g_live"),
      otherRunItem("run_b", "g_b"),
    ];
    const transport = makeTransport(() => response(items));
    const live = [liveGate(OPEN_RUN, "g_live")];
    const { result } = renderHook(() =>
      usePendingWork(transport, true, OPEN_RUN, live, 1),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    // The open-run item is de-duped in favour of the live card; the other run's
    // item is added. Exactly two cards, live first.
    expect(result.current.cards).toHaveLength(2);
    expect(result.current.cards[0]).toEqual(live[0]);
    expect(result.current.cards[1].runId).toBe("run_b");
    expect(result.current.cards[1].gateId).toBe("g_b");
  });

  it("refetches when refreshKey advances (coalesced)", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => response([]),
      () => request(),
    );
    const { result, rerender } = renderHook(
      ({ key }: { key: number }) =>
        usePendingWork(transport, true, OPEN_RUN, [], key),
      { initialProps: { key: 1 } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(request).toHaveBeenCalledTimes(1);
    rerender({ key: 2 });
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it("refresh() triggers a refetch", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => response([]),
      () => request(),
    );
    const { result } = renderHook(() =>
      usePendingWork(transport, true, OPEN_RUN, [], 1),
    );
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    result.current.refresh();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it("fails soft: an error keeps the last data and sets status error, no throw", async () => {
    let mode: "ok" | "boom" = "ok";
    const transport = makeTransport(() => {
      if (mode === "boom") throw new Error("network down");
      return response([otherRunItem("run_b", "g_b")]);
    });
    const { result, rerender } = renderHook(
      ({ key }: { key: number }) =>
        usePendingWork(transport, true, OPEN_RUN, [], key),
      { initialProps: { key: 1 } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.cards).toHaveLength(1);
    // Next fetch throws; the hook keeps the last data + flips to "error".
    mode = "boom";
    rerender({ key: 2 });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.cards).toHaveLength(1); // last good data retained
  });
});
