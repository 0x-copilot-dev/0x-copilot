// The suggestion appetite, and the mute list that makes a card's Deny safe.
//
// The property this file exists for is the merge one. Two surfaces edit the same
// `discoverable_connectors` block — the appetite Select here, and the suggestion
// card's Deny in the Run cockpit — so each write must be scoped to the field it
// owns. A PUT that sent the whole block would let a Select change silently
// resurrect every muted connector, which nothing else in the system would catch.

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "../ports/Transport";
import { useConnectorSuggestions } from "./useConnectorSuggestions";

function makeTransport(prefs: unknown): {
  transport: Transport;
  requests: TypedRequest[];
} {
  const requests: TypedRequest[] = [];
  const transport = {
    request: async (req: TypedRequest) => {
      requests.push(req);
      return prefs;
    },
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web" as const,
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  } as unknown as Transport;
  return { transport, requests };
}

const PREFS = (overrides: Record<string, boolean>) => ({
  discoverable_connectors: { mode: "unblock_only", overrides },
  updated_at: "2026-07-26T00:00:00Z",
});

describe("useConnectorSuggestions — the muted list", () => {
  it("lists the slugs the user muted", async () => {
    const { transport } = makeTransport(
      PREFS({ linear: false, notion: false }),
    );
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted.map((m) => m.slug)).toEqual([
      "linear",
      "notion",
    ]);
  });

  it("ignores a `true` override — that is the opposite decision", async () => {
    // `true` is the user asking for a connector the catalog does not suggest by
    // default. Listing it as "muted" would invite them to undo an opt-IN.
    const { transport } = makeTransport(PREFS({ linear: false, asana: true }));
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted.map((m) => m.slug)).toEqual(["linear"]);
  });

  it("derives a readable name from the slug", async () => {
    const { transport } = makeTransport(PREFS({ "google-drive": false }));
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted[0].displayName).toBe("Google Drive");
  });

  it("sorts by name so the list does not reshuffle between loads", async () => {
    const { transport } = makeTransport(
      PREFS({ zulip: false, asana: false, linear: false }),
    );
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted.map((m) => m.displayName)).toEqual([
      "Asana",
      "Linear",
      "Zulip",
    ]);
  });

  it("unmute sends only that slug, and drops the row at once", async () => {
    const { transport, requests } = makeTransport(
      PREFS({ linear: false, notion: false }),
    );
    const { result } = renderHook(() => useConnectorSuggestions(transport));
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.unmute("linear"));

    expect(result.current.muted.map((m) => m.slug)).toEqual(["notion"]);
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].method).toBe("PUT");
    // Slug-scoped. The merge is depth-2 and recursive, so `notion` and the
    // appetite `mode` are left exactly as they were.
    expect(requests[1].body).toEqual({
      discoverable_connectors: { overrides: { linear: true } },
    });
  });

  it("restores the row when the unmute fails", async () => {
    // A row that vanished without saving would leave the user believing a mute
    // was lifted — the one failure mode worth an optimistic revert here.
    const requests: TypedRequest[] = [];
    const transport = {
      request: async (req: TypedRequest) => {
        requests.push(req);
        if (req.method === "PUT") throw new Error("nope");
        return PREFS({ linear: false });
      },
      subscribeServerSentEvents: () => ({ close: () => {} }),
      getSession: () => ({ bearer: null }),
      capabilities: () => ({
        substrate: "web" as const,
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      }),
    } as unknown as Transport;

    const { result } = renderHook(() => useConnectorSuggestions(transport));
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.unmute("linear"));

    await waitFor(() =>
      expect(result.current.muted.map((m) => m.slug)).toEqual(["linear"]),
    );
    expect(result.current.error).not.toBeNull();
  });

  it("changing the appetite never sends the overrides", async () => {
    // The clobber guard. Both controls write `discoverable_connectors`; if this
    // PUT carried the block wholesale, picking a mode would resurrect every
    // muted connector and nothing would report it.
    const { transport, requests } = makeTransport(PREFS({ linear: false }));
    const { result } = renderHook(() => useConnectorSuggestions(transport));
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.change("always"));

    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].body).toEqual({
      discoverable_connectors: { mode: "always" },
    });
  });

  it("shows no muted rows when the user has muted nothing", async () => {
    const { transport } = makeTransport(PREFS({}));
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted).toEqual([]);
  });

  it("survives a preferences payload with no connector block at all", async () => {
    const { transport } = makeTransport({ updated_at: "2026-07-26T00:00:00Z" });
    const { result } = renderHook(() => useConnectorSuggestions(transport));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.muted).toEqual([]);
    expect(result.current.value).toBe("unblock_only");
  });
});
