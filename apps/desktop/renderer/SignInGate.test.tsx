// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CHANNELS,
  type RendererSession,
  type WindowBridge,
} from "@0x-copilot/chat-transport";

import { SignInGate } from "./SignInGate";

const SESSION: RendererSession = {
  workspaceId: "org_acme",
  expiresAt: Date.now() + 60_000,
  displayName: "Sarah",
  email: "sarah@acme.test",
};

interface Deferred {
  promise: Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (err: unknown) => void;
}

function deferred(): Deferred {
  let resolve: (value: unknown) => void = () => {};
  let reject: (err: unknown) => void = () => {};
  const promise = new Promise<unknown>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeBridge(
  handlers: Record<string, (payload: unknown) => Promise<unknown>>,
): WindowBridge & { calls: Array<{ channel: string; payload: unknown }> } {
  const calls: Array<{ channel: string; payload: unknown }> = [];
  return {
    calls,
    ipc: {
      invoke<T>(channel: string, payload: unknown): Promise<T> {
        calls.push({ channel, payload });
        const handler = handlers[channel];
        if (!handler) return Promise.resolve(null as T);
        return handler(payload) as Promise<T>;
      },
      on: () => () => undefined,
    },
  };
}

describe("SignInGate", () => {
  let container: HTMLElement;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (root !== null) {
      const r = root;
      act(() => {
        r.unmount();
      });
    }
    root = null;
    container.remove();
    vi.useRealTimers();
  });

  /** Advance past the design's post-sign-in "Signed in" beat (fake timers). */
  function passDoneBeat(): void {
    expect(
      container.querySelector("[data-testid='sign-in-done']"),
    ).not.toBeNull();
    expect(container.textContent).toContain("Signed in");
    expect(container.textContent).toContain("Opening your workspace…");
    act(() => {
      vi.advanceTimersByTime(900);
    });
  }

  async function mount(bridge: WindowBridge): Promise<void> {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <SignInGate bridge={bridge} workspaceId="org_acme">
          {(session) => <div data-testid="app">hello {session.email}</div>}
        </SignInGate>,
      );
    });
    await act(async () => {
      await Promise.resolve();
    });
  }

  function click(testid: string): void {
    const el = container.querySelector(
      `[data-testid='${testid}']`,
    ) as HTMLButtonElement | null;
    expect(el).not.toBeNull();
    act(() => {
      el?.click();
    });
  }

  it("renders the three v2 options with wallet first and primary", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
    });
    await mount(bridge);

    // Heading + honest sub-copy.
    expect(container.textContent).toContain("Welcome to");
    expect(container.textContent).toContain("it runs on your machine");

    const wallet = container.querySelector(
      "[data-testid='sign-in-wallet-button']",
    );
    const google = container.querySelector(
      "[data-testid='sign-in-google-button']",
    );
    const local = container.querySelector("[data-testid='sign-in-button']");
    expect(wallet).not.toBeNull();
    expect(google).not.toBeNull();
    expect(local).not.toBeNull();

    // Wallet is the accent-filled primary and comes first in the DOM.
    expect(wallet?.getAttribute("data-variant")).toBe("primary");
    expect(google?.getAttribute("data-variant")).toBe("secondary");
    expect(local?.getAttribute("data-variant")).toBe("secondary");
    const options = Array.from(
      container.querySelectorAll(".loginx-opt"),
    ) as HTMLElement[];
    expect(options[0]?.getAttribute("data-testid")).toBe(
      "sign-in-wallet-button",
    );

    // Labels/subtitles from the v2 design.
    expect(wallet?.textContent).toContain("Continue with a wallet");
    expect(wallet?.textContent).toContain("MetaMask");
    expect(google?.textContent).toContain("Continue with Google");
    expect(local?.textContent).toContain("Use locally, no account");

    // Footer honesty note.
    expect(container.textContent).toContain("No seed phrase, ever.");
  });

  it("clicking Continue with a wallet invokes auth.sign-in-wallet, shows the done beat, and lands signed-in", async () => {
    vi.useFakeTimers();
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    // While main drives the external wallet round-trip we show a waiting state
    // WITH the design's Cancel affordance.
    expect(
      container.querySelector("[data-testid='sign-in-waiting']"),
    ).not.toBeNull();
    expect(container.textContent).toContain("Waiting for your wallet…");
    expect(
      container.querySelector("[data-testid='sign-in-cancel-button']")
        ?.textContent,
    ).toBe("Cancel");
    expect(bridge.calls.at(-1)).toEqual({
      channel: CHANNELS.authSignInWallet,
      payload: { workspaceId: "org_acme" },
    });

    await act(async () => {
      invoke.resolve(SESSION);
      await Promise.resolve();
    });
    passDoneBeat();
    expect(
      container.querySelector("[data-testid='app']")?.textContent,
    ).toContain("sarah@acme.test");
  });

  it("clicking Continue with Google invokes auth.sign-in-google, shows the done beat, and lands signed-in", async () => {
    vi.useFakeTimers();
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInGoogle]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-google-button");
    // Design `google` view copy + the backlink-style cancel.
    expect(container.textContent).toContain("Authorizing with Google…");
    expect(
      container.querySelector("[data-testid='sign-in-cancel-button']")
        ?.textContent,
    ).toBe("Cancel — use a different method");
    expect(bridge.calls.at(-1)).toEqual({
      channel: CHANNELS.authSignInGoogle,
      payload: { workspaceId: "org_acme" },
    });

    await act(async () => {
      invoke.resolve(SESSION);
      await Promise.resolve();
    });
    passDoneBeat();
    expect(
      container.querySelector("[data-testid='app']")?.textContent,
    ).toContain("sarah@acme.test");
  });

  it("using locally still invokes auth.sign-in (no cancel affordance)", async () => {
    vi.useFakeTimers();
    const signIn = vi.fn(async () => SESSION);
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignIn]: signIn,
    });
    await mount(bridge);

    click("sign-in-button");
    expect(container.textContent).toContain("Setting up your workspace…");
    // Local mints instantly on this device — nothing external to cancel.
    expect(
      container.querySelector("[data-testid='sign-in-cancel-button']"),
    ).toBeNull();
    await act(async () => {
      await Promise.resolve();
    });
    expect(signIn).toHaveBeenCalledWith({ workspaceId: "org_acme" });
    passDoneBeat();
    expect(container.querySelector("[data-testid='app']")).not.toBeNull();
  });

  it("Cancel on the wallet wait closes the flow quietly — pick screen, no error", async () => {
    const invoke = deferred();
    const cancel = vi.fn(async () => undefined);
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: () =>
        invoke.promise as Promise<RendererSession>,
      [CHANNELS.authCancelSignIn]: cancel,
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    click("sign-in-cancel-button");
    expect(cancel).toHaveBeenCalled();

    // Main closes the loopback → the pending sign-in promise rejects. A
    // canceled flow must land back on pick, NOT on the failure screen.
    await act(async () => {
      invoke.reject(new Error("loopback closed before the redirect"));
      await Promise.resolve();
    });
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='sign-in-error']")).toBeNull();
  });

  it("a failed Google sign-in shows the design's gerr state — retry, wallet fallback, back to sign-in", async () => {
    const googleSignIn = vi.fn(async () => {
      throw new Error("loopback redirect timed out");
    });
    const walletInvoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInGoogle]: googleSignIn,
      [CHANNELS.authSignInWallet]: () =>
        walletInvoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-google-button");
    await act(async () => {
      await Promise.resolve();
    });
    // Design gerr copy + the honest detail line.
    expect(container.textContent).toContain("Google didn’t finish");
    expect(container.textContent).toContain(
      "The browser window closed or timed out before confirming.",
    );
    const error = container.querySelector("[data-testid='sign-in-error']");
    expect(error?.textContent).toContain("loopback redirect timed out");

    // Try again retries GOOGLE (design), not the pick screen.
    click("sign-in-retry-button");
    expect(googleSignIn).toHaveBeenCalledTimes(2);
    await act(async () => {
      await Promise.resolve();
    });

    // "Use a wallet instead" jumps straight into the wallet flow.
    click("sign-in-wallet-fallback-button");
    expect(bridge.calls.at(-1)?.channel).toBe(CHANNELS.authSignInWallet);
    expect(container.textContent).toContain("Waiting for your wallet…");

    // Cancel that, reject, and use the gerr backlink to reach pick.
    await act(async () => {
      walletInvoke.reject(new Error("closed"));
      await Promise.resolve();
    });
    click("sign-in-back-button");
    expect(
      container.querySelector("[data-testid='sign-in-google-button']"),
    ).not.toBeNull();
  });

  it("a failed wallet sign-in shows the design's werr state — retry retries, backlink returns to pick", async () => {
    const walletSignIn = vi.fn(async () => {
      throw new Error("wallet handoff state mismatch");
    });
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: walletSignIn,
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    await act(async () => {
      await Promise.resolve();
    });
    // Design werr copy + honest detail.
    expect(container.textContent).toContain("No response from your wallet");
    expect(container.textContent).toContain("Nothing was signed.");
    const error = container.querySelector("[data-testid='sign-in-error']");
    expect(error?.textContent).toContain("wallet handoff state mismatch");

    // Try again retries the WALLET flow (design), landing back on werr.
    click("sign-in-retry-button");
    expect(walletSignIn).toHaveBeenCalledTimes(2);
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.textContent).toContain("No response from your wallet");

    // The backlink is the way back to the pick screen.
    click("sign-in-back-button");
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-google-button']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-button']"),
    ).not.toBeNull();
  });

  it("a failed session lookup surfaces the error state", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => {
        throw new Error("session store unreachable");
      },
    });
    await mount(bridge);

    const error = container.querySelector("[data-testid='sign-in-error']");
    expect(error?.textContent).toContain("session store unreachable");
  });

  it("offers all three options — including 'Use locally' — in every posture", async () => {
    // 'Use locally, no account' is no longer gated on posture; a packaged
    // (production) install shows wallet + Google + local, same as dev.
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authGetPosture]: async () => ({ productionPosture: true }),
    });
    await mount(bridge);

    expect(
      container.querySelector("[data-testid='sign-in-gate']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-google-button']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-button']"),
    ).not.toBeNull();
  });

  it("an existing session skips the pick screen entirely", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => SESSION,
    });
    await mount(bridge);

    expect(
      container.querySelector("[data-testid='app']")?.textContent,
    ).toContain("sarah@acme.test");
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).toBeNull();
  });

  it("signOut invokes auth.sign-out and returns to the pick screen", async () => {
    // Regression: the render prop's signOut must clear the PERSISTED session
    // (authSignOut IPC), not just the view — otherwise the app boots straight
    // back in. This is what wires the Settings "Sign out" button end-to-end.
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => SESSION,
      [CHANNELS.authSignOut]: async () => undefined,
    });
    await act(async () => {
      root = createRoot(container);
      root.render(
        <SignInGate bridge={bridge} workspaceId="org_acme">
          {(session, signOut) => (
            <button type="button" data-testid="do-sign-out" onClick={signOut}>
              out {session.email}
            </button>
          )}
        </SignInGate>,
      );
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Signed in first (session present).
    expect(
      container.querySelector("[data-testid='do-sign-out']"),
    ).not.toBeNull();

    click("do-sign-out");
    await act(async () => {
      await Promise.resolve();
    });

    // The persisted session was cleared via the IPC…
    expect(
      bridge.calls.some(
        (c) =>
          c.channel === CHANNELS.authSignOut &&
          (c.payload as { workspaceId?: string }).workspaceId === "org_acme",
      ),
    ).toBe(true);
    // …and the gate is back on the pick screen (all three options shown).
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='sign-in-button']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='do-sign-out']")).toBeNull();
  });
});
