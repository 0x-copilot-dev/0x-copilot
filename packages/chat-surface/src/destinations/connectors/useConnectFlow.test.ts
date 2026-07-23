// useConnectFlow — host-neutral connect orchestration (PRD-11 D4).
// Phase/pending/error state, the `authorize` dispatch (catalog vs custom),
// custom-server add over the injected port, and host-driven completion.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorSlug } from "@0x-copilot/api-types";

import { useConnectFlow, type UseConnectFlowOptions } from "./useConnectFlow";

/** A deferred promise so a test can hold the flow in its `pending` phase. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function setup(overrides: Partial<UseConnectFlowOptions> = {}) {
  const authorize = vi.fn(() => Promise.resolve());
  const onConnect = vi.fn(() => Promise.resolve());
  const options: UseConnectFlowOptions = {
    authorize,
    onConnect,
    ...overrides,
  };
  const view = renderHook(
    (props: UseConnectFlowOptions) => useConnectFlow(props),
    {
      initialProps: options,
    },
  );
  return { authorize, onConnect, view };
}

describe("useConnectFlow", () => {
  it("openConnect opens the modal; closeConnect resets and closes", () => {
    const { view } = setup();
    expect(view.result.current.open).toBe(false);
    act(() => view.result.current.openConnect());
    expect(view.result.current.open).toBe(true);
    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.error).toBeNull();
    act(() => view.result.current.closeConnect());
    expect(view.result.current.open).toBe(false);
  });

  it("onSelectEntry sets pending and authorizes the picked slug", () => {
    const authorize = vi.fn(() => deferred<void>().promise);
    const { view } = setup({ authorize });
    act(() => view.result.current.openConnect());
    act(() => view.result.current.onSelectEntry("notion" as ConnectorSlug));
    expect(view.result.current.pending).toBe(true);
    expect(authorize).toHaveBeenCalledWith({ slug: "notion" });
  });

  it("markConnected clears pending for the authorizing slug", () => {
    const { view } = setup();
    act(() => view.result.current.openConnect());
    act(() => view.result.current.onSelectEntry("notion" as ConnectorSlug));
    // Complete the catalog OAuth from the host signal.
    act(() => view.result.current.markConnected("notion" as ConnectorSlug));
    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.error).toBeNull();
  });

  it("markConnected ignores a non-matching slug", () => {
    const authorize = vi.fn(() => deferred<void>().promise);
    const { view } = setup({ authorize });
    act(() => view.result.current.onSelectEntry("notion" as ConnectorSlug));
    act(() => view.result.current.markConnected("slack" as ConnectorSlug));
    // Still authorizing Notion — a stray Slack completion must not resolve it.
    expect(view.result.current.pending).toBe(true);
  });

  it("a rejected catalog authorize surfaces the error and clears pending", async () => {
    const dfd = deferred<void>();
    const authorize = vi.fn(() => dfd.promise);
    const { view } = setup({ authorize });
    act(() => view.result.current.onSelectEntry("notion" as ConnectorSlug));
    await act(async () => {
      dfd.reject(new Error("window closed"));
      await Promise.resolve();
    });
    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.error).toBe("window closed");
  });

  it("onConnect persists the permission via the injected onConnect then closes", async () => {
    const onConnect = vi.fn(() => Promise.resolve());
    const { view } = setup({ onConnect });
    act(() => view.result.current.openConnect());
    await act(async () => {
      view.result.current.onConnect("notion" as ConnectorSlug, "read");
      await Promise.resolve();
    });
    expect(onConnect).toHaveBeenCalledWith("notion", "read");
    expect(view.result.current.open).toBe(false);
  });

  it("a rejected terminal onConnect surfaces the error without closing", async () => {
    const onConnect = vi.fn(() => Promise.reject(new Error("nope")));
    const { view } = setup({ onConnect });
    act(() => view.result.current.openConnect());
    await act(async () => {
      view.result.current.onConnect("notion" as ConnectorSlug, "read");
      await Promise.resolve();
    });
    expect(view.result.current.open).toBe(true);
    expect(view.result.current.error).toBe("nope");
  });

  describe("custom-server add", () => {
    it("onAddCustomServer is undefined when no addCustomServer option is supplied", () => {
      const { view } = setup();
      expect(view.result.current.onAddCustomServer).toBeUndefined();
    });

    it("creates the server, then authorizes its OAuth url when one is returned", async () => {
      const addCustomServer = vi.fn(() =>
        Promise.resolve({ authorizeUrl: "https://auth.example.com" }),
      );
      const authorize = vi.fn(() => Promise.resolve());
      const { view } = setup({ addCustomServer, authorize });
      await act(async () => {
        view.result.current.onAddCustomServer?.({
          url: "https://mcp.example.com",
        });
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(addCustomServer).toHaveBeenCalledTimes(1);
      expect(addCustomServer).toHaveBeenCalledWith({
        url: "https://mcp.example.com",
      });
      expect(authorize).toHaveBeenCalledWith({
        url: "https://auth.example.com",
      });
      // Completion still lands via markConnected — pending stays true.
      expect(view.result.current.pending).toBe(true);
    });

    it("a no-auth custom add clears pending immediately (modal closes)", async () => {
      const addCustomServer = vi.fn(() => Promise.resolve({}));
      const authorize = vi.fn(() => Promise.resolve());
      const { view } = setup({ addCustomServer, authorize });
      await act(async () => {
        view.result.current.onAddCustomServer?.({
          url: "https://mcp.example.com",
        });
        await Promise.resolve();
      });
      expect(authorize).not.toHaveBeenCalled();
      expect(view.result.current.pending).toBe(false);
    });

    it("markConnected clears a pending custom add regardless of slug", async () => {
      const addCustomServer = vi.fn(() =>
        Promise.resolve({ authorizeUrl: "https://auth.example.com" }),
      );
      const { view } = setup({ addCustomServer });
      await act(async () => {
        view.result.current.onAddCustomServer?.({
          url: "https://mcp.example.com",
        });
        await Promise.resolve();
        await Promise.resolve();
      });
      act(() => view.result.current.markConnected());
      expect(view.result.current.pending).toBe(false);
    });

    it("a rejected create surfaces the error and clears pending", async () => {
      const addCustomServer = vi.fn(() =>
        Promise.reject(new Error("create_failed")),
      );
      const { view } = setup({ addCustomServer });
      await act(async () => {
        view.result.current.onAddCustomServer?.({
          url: "https://mcp.example.com",
        });
        await Promise.resolve();
      });
      expect(view.result.current.error).toBe("create_failed");
      expect(view.result.current.pending).toBe(false);
    });
  });
});
