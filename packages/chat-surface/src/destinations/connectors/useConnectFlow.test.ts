// useConnectFlow — host-neutral connect orchestration (PRD-11 D4).
// Phase/pending/error state, the `authorize` dispatch for a catalog pick, and
// host-driven completion.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorSlug } from "@0x-copilot/api-types";

import {
  ConnectOAuthClientRequiredError,
  ConnectSupersededError,
  useConnectFlow,
  type UseConnectFlowOptions,
} from "./useConnectFlow";

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
});

describe("useConnectFlow — pre-registered OAuth client", () => {
  const SLUG = "atlassian" as ConnectorSlug;

  it("holds the slug instead of surfacing an error when a client is required", async () => {
    // The distinction that makes the form possible: this is not "connect
    // failed", it is "connect needs one more input".
    const authorize = vi.fn(() =>
      Promise.reject(new ConnectOAuthClientRequiredError(SLUG)),
    );
    const { view } = setup({ authorize });
    await act(async () => {
      view.result.current.onSelectEntry(SLUG);
      await Promise.resolve();
    });
    expect(view.result.current.clientRequiredSlug).toBe(SLUG);
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.connectingSlug).toBeNull();
  });

  it("retries the SAME slug with the supplied client", async () => {
    const authorize = vi
      .fn()
      .mockRejectedValueOnce(new ConnectOAuthClientRequiredError(SLUG))
      .mockResolvedValueOnce(undefined);
    const { view } = setup({ authorize });
    await act(async () => {
      view.result.current.onSelectEntry(SLUG);
      await Promise.resolve();
    });
    await act(async () => {
      view.result.current.submitOAuthClient({ client_id: "cid" });
      await Promise.resolve();
    });
    expect(authorize).toHaveBeenNthCalledWith(2, {
      slug: SLUG,
      oauthClient: { client_id: "cid" },
    });
    expect(view.result.current.clientRequiredSlug).toBeNull();
  });

  it("ignores a client submitted when nothing is waiting for one", () => {
    const { authorize, view } = setup();
    act(() => view.result.current.submitOAuthClient({ client_id: "cid" }));
    expect(authorize).not.toHaveBeenCalled();
  });

  it("a non-client failure still surfaces as an error", async () => {
    const authorize = vi.fn(() => Promise.reject(new Error("boom")));
    const { view } = setup({ authorize });
    await act(async () => {
      view.result.current.onSelectEntry(SLUG);
      await Promise.resolve();
    });
    expect(view.result.current.error).toBe("boom");
    expect(view.result.current.clientRequiredSlug).toBeNull();
  });

  it("closing the flow clears the waiting-for-client state", async () => {
    const authorize = vi.fn(() =>
      Promise.reject(new ConnectOAuthClientRequiredError(SLUG)),
    );
    const { view } = setup({ authorize });
    await act(async () => {
      view.result.current.onSelectEntry(SLUG);
      await Promise.resolve();
    });
    act(() => view.result.current.closeConnect());
    expect(view.result.current.clientRequiredSlug).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------
//
// The modal's Cancel used to only close the dialog. Main kept the loopback
// armed for its full timeout, so a user who cancelled and then approved anyway
// in the still-open tab ended up connected — having been told the opposite.

describe("useConnectFlow — cancel", () => {
  const SLUG = "atlassian" as ConnectorSlug;

  it("is undefined when the host cannot abort, so no Cancel is offered", () => {
    const { view } = setup();
    // Expressed, not assumed: web's connect is a full-page redirect, and a
    // Cancel that only tidies the dialog would misinform the user.
    expect(view.result.current.cancelConnect).toBeUndefined();
  });

  it("calls the host abort and does not report the resulting rejection", async () => {
    const attempt = deferred<void>();
    const authorize = vi.fn(() => attempt.promise);
    const cancelAuthorize = vi.fn(() => Promise.resolve());
    const { view } = setup({ authorize, cancelAuthorize });

    await act(async () => {
      view.result.current.onSelectEntry(SLUG);
      await Promise.resolve();
    });
    expect(view.result.current.pending).toBe(true);

    act(() => view.result.current.cancelConnect?.());
    expect(cancelAuthorize).toHaveBeenCalledTimes(1);

    // Main aborting is what rejects the attempt.
    await act(async () => {
      attempt.reject(new Error("connect cancelled"));
      await Promise.resolve();
    });

    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.connectingSlug).toBeNull();
    // The user asked for this; showing them an error for it would be noise.
    expect(view.result.current.error).toBeNull();
  });

  it("does nothing when no authorization is in flight", () => {
    const cancelAuthorize = vi.fn(() => Promise.resolve());
    const { view } = setup({ cancelAuthorize });
    act(() => view.result.current.cancelConnect?.());
    expect(cancelAuthorize).not.toHaveBeenCalled();
  });
});

// A connect the HOST abandoned for a newer one is not a failure and is not the
// user's doing. Main holds ONE pending slot (newest-connect-wins), so starting a
// second connect rejects the first — and that rejection lands in a flow that
// never asked for it.
//
// It used to be indistinguishable from a user Cancel, and only Cancel was
// treated quietly, so the abandoned attempt fell through to the error branch and
// the user was told a connector they had just started had failed — quoting the
// internal string `connect cancelled` at them. Worse, both attempts share this
// flow's state, so the abandoned one's teardown cleared the LIVE attempt's
// spinner while its OAuth round-trip was still running.
describe("useConnectFlow — superseded by a newer connect", () => {
  const FIRST = "atlassian" as ConnectorSlug;
  const SECOND = "gmail" as ConnectorSlug;

  it("reports no error when the host abandoned the attempt", async () => {
    const attempt = deferred<void>();
    const authorize = vi.fn(() => attempt.promise);
    const { view } = setup({ authorize });

    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });

    await act(async () => {
      attempt.reject(new ConnectSupersededError(FIRST));
      await Promise.resolve();
    });

    // The user started this connector; telling them it broke would be a lie.
    expect(view.result.current.error).toBeNull();
    // Nothing newer in THIS flow to hand off to, so it must stop spinning.
    expect(view.result.current.pending).toBe(false);
    expect(view.result.current.connectingSlug).toBeNull();
  });

  it("never shows the raw IPC string to the user", async () => {
    const attempt = deferred<void>();
    const authorize = vi.fn(() => attempt.promise);
    const { view } = setup({ authorize });

    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });
    await act(async () => {
      attempt.reject(new ConnectSupersededError(FIRST));
      await Promise.resolve();
    });

    expect(view.result.current.error ?? "").not.toContain("connect cancelled");
    expect(view.result.current.error ?? "").not.toContain("superseded");
  });

  it("does not tear down the NEWER attempt that replaced it", async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    const authorize = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { view } = setup({ authorize });

    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });
    await act(async () => {
      view.result.current.onSelectEntry(SECOND);
      await Promise.resolve();
    });

    // The first attempt's rejection arrives AFTER the second took the flow.
    await act(async () => {
      first.reject(new ConnectSupersededError(FIRST));
      await Promise.resolve();
    });

    // The live connect keeps its spinner and its identity — this is the
    // clobbering that made a running connect look dead.
    expect(view.result.current.pending).toBe(true);
    expect(view.result.current.connectingSlug).toBe(SECOND);
    expect(view.result.current.error).toBeNull();
  });

  it("does not let a stale attempt clobber a RETRY of the same connector", async () => {
    // Slug identity cannot separate these two, which is why the guard is a
    // monotonic attempt token rather than a slug comparison.
    const first = deferred<void>();
    const second = deferred<void>();
    const authorize = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { view } = setup({ authorize });

    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });
    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });
    await act(async () => {
      first.reject(new ConnectSupersededError(FIRST));
      await Promise.resolve();
    });

    expect(view.result.current.pending).toBe(true);
    expect(view.result.current.connectingSlug).toBe(FIRST);
    expect(view.result.current.error).toBeNull();
  });

  it("still reports a GENUINE failure — the quiet path is not a blanket mute", async () => {
    const attempt = deferred<void>();
    const authorize = vi.fn(() => attempt.promise);
    const { view } = setup({ authorize });

    await act(async () => {
      view.result.current.onSelectEntry(FIRST);
      await Promise.resolve();
    });
    await act(async () => {
      attempt.reject(new Error("the provider refused"));
      await Promise.resolve();
    });

    expect(view.result.current.error).toContain("the provider refused");
    expect(view.result.current.pending).toBe(false);
  });
});
