// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CHANNELS,
  type RendererSession,
  type WindowBridge,
} from "@enterprise-search/chat-transport";

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
  });

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

  it("renders all three sign-in buttons when anonymous", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
    });
    await mount(bridge);

    expect(
      container.querySelector("[data-testid='sign-in-button']"),
    ).not.toBeNull();
    const google = container.querySelector(
      "[data-testid='sign-in-google-button']",
    );
    expect(google).not.toBeNull();
    expect(google?.textContent).toContain("Continue with Google");
    const wallet = container.querySelector(
      "[data-testid='sign-in-wallet-button']",
    );
    expect(wallet).not.toBeNull();
    expect(wallet?.textContent).toContain("Connect wallet");
  });

  it("clicking Continue with Google invokes auth.sign-in-google and lands signed-in", async () => {
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInGoogle]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-google-button");
    // While main drives the system-browser round-trip we show progress.
    expect(container.textContent).toContain("Opening browser…");
    expect(bridge.calls.at(-1)).toEqual({
      channel: CHANNELS.authSignInGoogle,
      payload: { workspaceId: "org_acme" },
    });

    await act(async () => {
      invoke.resolve(SESSION);
      await Promise.resolve();
    });
    expect(
      container.querySelector("[data-testid='app']")?.textContent,
    ).toContain("sarah@acme.test");
  });

  it("a failed Google sign-in shows the error with a retry that returns to anon", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInGoogle]: async () => {
        throw new Error("loopback redirect timed out");
      },
    });
    await mount(bridge);

    click("sign-in-google-button");
    await act(async () => {
      await Promise.resolve();
    });
    const error = container.querySelector("[data-testid='sign-in-error']");
    expect(error?.textContent).toContain("loopback redirect timed out");

    // Try again returns to the anon screen with both buttons.
    const retry = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent === "Try again",
    );
    expect(retry).toBeDefined();
    act(() => {
      retry?.click();
    });
    expect(
      container.querySelector("[data-testid='sign-in-google-button']"),
    ).not.toBeNull();
  });

  it("clicking Connect wallet invokes auth.sign-in-wallet and lands signed-in", async () => {
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    // While main drives the system-browser round-trip we show progress.
    expect(container.textContent).toContain("Opening browser…");
    expect(bridge.calls.at(-1)).toEqual({
      channel: CHANNELS.authSignInWallet,
      payload: { workspaceId: "org_acme" },
    });

    await act(async () => {
      invoke.resolve(SESSION);
      await Promise.resolve();
    });
    expect(
      container.querySelector("[data-testid='app']")?.textContent,
    ).toContain("sarah@acme.test");
  });

  it("a failed wallet sign-in shows the error with a retry that returns to anon", async () => {
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: async () => {
        throw new Error("wallet handoff state mismatch");
      },
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    await act(async () => {
      await Promise.resolve();
    });
    const error = container.querySelector("[data-testid='sign-in-error']");
    expect(error?.textContent).toContain("wallet handoff state mismatch");

    // Try again returns to the anon screen with the wallet button back.
    const retry = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent === "Try again",
    );
    expect(retry).toBeDefined();
    act(() => {
      retry?.click();
    });
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
    ).not.toBeNull();
  });

  it("legacy sign-in button still uses auth.sign-in", async () => {
    const signIn = vi.fn(async () => SESSION);
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignIn]: signIn,
    });
    await mount(bridge);

    click("sign-in-button");
    await act(async () => {
      await Promise.resolve();
    });
    expect(signIn).toHaveBeenCalledWith({ workspaceId: "org_acme" });
    expect(container.querySelector("[data-testid='app']")).not.toBeNull();
  });
});
