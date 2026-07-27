// The consent card's four states, and who moves them.
//
// `ConnectorConsentCard` has always drawn pending / connecting / connected /
// denied, and `TcChat` hardcoded `pending` because the run stream cannot see a
// popup. Three of four states were unreachable: a user pressed Connect and the
// card sat still, which reads as a dead button.
//
// The property that matters most here is the bypass one. The hook only works if
// callers use the WRAPPED port; handing out the original would leave the card
// inert in exactly the way this hook exists to fix, and nothing else would fail.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useConnectorConsentStates } from "./useConnectorConsentStates";
import type { McpAuthPort } from "./mcpAuthPort";

function makePort(): McpAuthPort & {
  beginAuth: ReturnType<typeof vi.fn>;
  skipAuth: ReturnType<typeof vi.fn>;
} {
  return {
    beginAuth: vi.fn(),
    deleteServer: vi.fn(),
    skipAuth: vi.fn(),
    installFromCatalog: vi.fn(),
  } as unknown as McpAuthPort & {
    beginAuth: ReturnType<typeof vi.fn>;
    skipAuth: ReturnType<typeof vi.fn>;
  };
}

describe("useConnectorConsentStates", () => {
  it("starts with nothing said about any connector", () => {
    const { result } = renderHook(() => useConnectorConsentStates(makePort()));
    // Absent, not "pending" — the card supplies that default, so an untouched
    // connector and a hook-less host render identically.
    expect(result.current.states).toEqual({});
  });

  it("moves to connecting on Connect, and still calls the host", () => {
    const port = makePort();
    const { result } = renderHook(() => useConnectorConsentStates(port));

    act(() => result.current.port?.beginAuth("seed:linear"));

    // Optimistic: the browser is about to leave for the vendor's consent
    // screen, and waiting for the return would leave the card dead meanwhile.
    expect(result.current.states["seed:linear"]).toBe("connecting");
    expect(port.beginAuth).toHaveBeenCalledWith("seed:linear");
  });

  it("moves to denied on Deny, and still calls the host", () => {
    const port = makePort();
    const { result } = renderHook(() => useConnectorConsentStates(port));

    act(() => result.current.port?.skipAuth("seed:notion"));

    expect(result.current.states["seed:notion"]).toBe("denied");
    expect(port.skipAuth).toHaveBeenCalledWith("seed:notion");
  });

  it("reaches connected only when the host says the return succeeded", () => {
    const port = makePort();
    const { result } = renderHook(() => useConnectorConsentStates(port));

    act(() => result.current.port?.beginAuth("seed:linear"));
    expect(result.current.states["seed:linear"]).toBe("connecting");

    // Nothing the hook can observe distinguishes a granted consent from an
    // abandoned tab; only the host's OAuth return knows.
    act(() => result.current.markConnected("seed:linear"));
    expect(result.current.states["seed:linear"]).toBe("connected");
  });

  it("stays at connecting while the host has not reported back", () => {
    // The honest state — "we opened the consent screen and have not heard" —
    // rather than a `connected` we would be guessing at.
    const { result } = renderHook(() => useConnectorConsentStates(makePort()));
    act(() => result.current.port?.beginAuth("seed:asana"));
    expect(result.current.states["seed:asana"]).toBe("connecting");
  });

  it("tracks connectors independently", () => {
    const { result } = renderHook(() => useConnectorConsentStates(makePort()));

    act(() => result.current.port?.beginAuth("seed:linear"));
    act(() => result.current.port?.skipAuth("seed:notion"));

    expect(result.current.states).toEqual({
      "seed:linear": "connecting",
      "seed:notion": "denied",
    });
  });

  it("lets a denied connector be reconsidered", () => {
    // `denied` is the state whose entire point is that it is reversible.
    const { result } = renderHook(() => useConnectorConsentStates(makePort()));

    act(() => result.current.port?.skipAuth("seed:linear"));
    expect(result.current.states["seed:linear"]).toBe("denied");

    act(() => result.current.port?.beginAuth("seed:linear"));
    expect(result.current.states["seed:linear"]).toBe("connecting");
  });

  it("is absent when the host wires no port at all", () => {
    // Desktop shipped without a mid-run launcher; the gate must stay visible
    // and inert rather than crash.
    const { result } = renderHook(() => useConnectorConsentStates(undefined));
    expect(result.current.port).toBeUndefined();
    expect(result.current.states).toEqual({});
  });

  it("keeps a stable port identity across re-renders", () => {
    const port = makePort();
    const { result, rerender } = renderHook(() =>
      useConnectorConsentStates(port),
    );
    const first = result.current.port;
    rerender();
    expect(result.current.port).toBe(first);
  });

  // Web's flow destroys its own evidence: `beginAuth` full-page-redirects, so
  // the cockpit is unmounted when consent is granted and remounts with an empty
  // map. The host's callback route is the only survivor, and hands the result
  // back through this argument.
  describe("the host's observed OAuth return", () => {
    it("marks the returned connector connected on mount", () => {
      const { result } = renderHook(() =>
        useConnectorConsentStates(makePort(), "seed:linear"),
      );
      expect(result.current.states["seed:linear"]).toBe("connected");
    });

    it("marks it when the host reports one later", () => {
      const { result, rerender } = renderHook(
        ({ id }: { id: string | null }) =>
          useConnectorConsentStates(makePort(), id),
        { initialProps: { id: null as string | null } },
      );
      expect(result.current.states).toEqual({});

      rerender({ id: "seed:notion" });
      expect(result.current.states["seed:notion"]).toBe("connected");
    });

    it("leaves other connectors alone", () => {
      const { result } = renderHook(() =>
        useConnectorConsentStates(makePort(), "seed:linear"),
      );
      act(() => result.current.port?.skipAuth("seed:notion"));
      expect(result.current.states).toEqual({
        "seed:linear": "connected",
        "seed:notion": "denied",
      });
    });

    it("does not fight a later user action on the same connector", () => {
      // A host that holds the completion across renders must not re-assert it
      // over what the user did next.
      const { result, rerender } = renderHook(() =>
        useConnectorConsentStates(makePort(), "seed:linear"),
      );
      expect(result.current.states["seed:linear"]).toBe("connected");

      act(() => result.current.port?.skipAuth("seed:linear"));
      rerender();
      expect(result.current.states["seed:linear"]).toBe("denied");
    });
  });

  it("does NOT move state when the original port is called directly", () => {
    // The bypass guard. Callers must take the wrapped port — this test exists
    // so that requirement is enforced somewhere other than a code comment.
    const port = makePort();
    const { result } = renderHook(() => useConnectorConsentStates(port));

    act(() => port.beginAuth("seed:linear"));

    expect(port.beginAuth).toHaveBeenCalled();
    expect(result.current.states["seed:linear"]).toBeUndefined();
  });
});
