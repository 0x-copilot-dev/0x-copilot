// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  RunSurfacesResponse,
  SurfaceSnapshot,
} from "@0x-copilot/api-types";
import type {
  Session,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { useSurfacesV2 } from "./useSurfacesV2";

function snapshot(
  surface_id: string,
  extra: Record<string, unknown> = {},
): SurfaceSnapshot {
  return {
    surface_id,
    kind: "record",
    connector: "linear",
    op: "get_issue",
    title: `Title ${surface_id}`,
    payload_ref: `payload/${surface_id}`,
    view: null,
    first_sequence_no: 1,
    last_sequence_no: 1,
    ledger_id: "ra7f·001",
    ...extra,
  } as SurfaceSnapshot;
}

/** A transport whose `/surfaces` response comes from `respond`, with a call
 *  counter so coalescing can be asserted. */
function makeTransport(
  respond: () => RunSurfacesResponse | Promise<RunSurfacesResponse>,
  onCall?: (path: string) => void,
): Transport {
  return {
    request: (async (req: TypedRequest) => {
      onCall?.(req.path);
      if (typeof req.path === "string" && req.path.endsWith("/surfaces")) {
        return respond();
      }
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

describe("useSurfacesV2", () => {
  it("disabled: issues no request and stays idle", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => ({ run_id: "r1", surfaces: [], latest_sequence_no: 1 }),
      () => request(),
    );
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 5, false),
    );
    await Promise.resolve();
    expect(result.current.status).toBe("idle");
    expect(request).not.toHaveBeenCalled();
  });

  it("idle when lastLedgerSeq is 0 (no v2 events yet)", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => ({ run_id: "r1", surfaces: [], latest_sequence_no: 0 }),
      () => request(),
    );
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 0, true),
    );
    await Promise.resolve();
    expect(result.current.status).toBe("idle");
    expect(request).not.toHaveBeenCalled();
  });

  it("idle when runId is null", async () => {
    const request = vi.fn();
    const transport = makeTransport(
      () => ({ run_id: "r1", surfaces: [], latest_sequence_no: 1 }),
      () => request(),
    );
    renderHook(() => useSurfacesV2(transport, null, 5, true));
    await Promise.resolve();
    expect(request).not.toHaveBeenCalled();
  });

  it("fetches on first lastLedgerSeq > 0 and reaches ready", async () => {
    const transport = makeTransport(() => ({
      run_id: "r1",
      surfaces: [snapshot("s1")],
      latest_sequence_no: 1,
    }));
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 1, true),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
  });

  it("adapts snapshot entries carrying state to the SurfacePayload envelope", async () => {
    const withState = snapshot("s1", {
      state: { spec: { title: "T" }, data: { name: "ENG-142" } },
    });
    const transport = makeTransport(() => ({
      run_id: "r1",
      surfaces: [withState],
      latest_sequence_no: 1,
    }));
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 1, true),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.stateFor("s1")).toEqual({
      spec: { title: "T" },
      data: { name: "ENG-142" },
    });
    // metadata-only snapshots hydrate to undefined (honest not-yet-hydrated)
    expect(result.current.stateFor("missing")).toBeUndefined();
  });

  it("metadata-only snapshots leave stateFor undefined (tier-3 floor)", async () => {
    const transport = makeTransport(() => ({
      run_id: "r1",
      surfaces: [snapshot("s1")], // no state/data → metadata only
      latest_sequence_no: 1,
    }));
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 1, true),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.stateFor("s1")).toBeUndefined();
  });

  it("coalesces a seq advance during flight into exactly one follow-up", async () => {
    const box: { resolveFirst: ((r: RunSurfacesResponse) => void) | null } = {
      resolveFirst: null,
    };
    let calls = 0;
    const transport = makeTransport(
      () =>
        new Promise<RunSurfacesResponse>((resolve) => {
          if (calls === 1) {
            box.resolveFirst = resolve;
          } else {
            resolve({ run_id: "r1", surfaces: [], latest_sequence_no: calls });
          }
        }),
      () => {
        calls += 1;
      },
    );
    const { rerender } = renderHook(
      ({ seq }: { seq: number }) => useSurfacesV2(transport, "r1", seq, true),
      { initialProps: { seq: 1 } },
    );
    // First fetch is in flight (calls === 1). Advance twice while in flight.
    await waitFor(() => expect(calls).toBe(1));
    rerender({ seq: 2 });
    rerender({ seq: 3 });
    // Still exactly one in-flight request — no second call yet.
    expect(calls).toBe(1);
    // Resolve the first; the coalesced follow-up fires exactly once.
    box.resolveFirst?.({ run_id: "r1", surfaces: [], latest_sequence_no: 1 });
    await waitFor(() => expect(calls).toBe(2));
    // No further requests scheduled after the follow-up settles.
    await Promise.resolve();
    expect(calls).toBe(2);
  });

  it("HTTP error → status error, stateFor undefined, no throw", async () => {
    const transport = makeTransport(() => Promise.reject(new Error("boom")));
    const { result } = renderHook(() =>
      useSurfacesV2(transport, "r1", 1, true),
    );
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.stateFor("s1")).toBeUndefined();
  });
});
