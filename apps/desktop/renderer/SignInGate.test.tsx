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

  it("clicking Continue with a wallet invokes auth.sign-in-wallet and lands signed-in", async () => {
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInWallet]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-wallet-button");
    // While main drives the external wallet round-trip we show a waiting state.
    expect(
      container.querySelector("[data-testid='sign-in-waiting']"),
    ).not.toBeNull();
    expect(container.textContent).toContain("Waiting for your wallet…");
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

  it("clicking Continue with Google invokes auth.sign-in-google and lands signed-in", async () => {
    const invoke = deferred();
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignInGoogle]: () =>
        invoke.promise as Promise<RendererSession>,
    });
    await mount(bridge);

    click("sign-in-google-button");
    expect(container.textContent).toContain("Opening your browser…");
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

  it("using locally still invokes auth.sign-in", async () => {
    const signIn = vi.fn(async () => SESSION);
    const bridge = makeBridge({
      [CHANNELS.authGetSession]: async () => null,
      [CHANNELS.authSignIn]: signIn,
    });
    await mount(bridge);

    click("sign-in-button");
    expect(container.textContent).toContain("Setting up your workspace…");
    await act(async () => {
      await Promise.resolve();
    });
    expect(signIn).toHaveBeenCalledWith({ workspaceId: "org_acme" });
    expect(container.querySelector("[data-testid='app']")).not.toBeNull();
  });

  it("a failed Google sign-in shows the error with a retry that returns to the pick screen", async () => {
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

    // Try again returns to the pick screen with all three options back.
    click("sign-in-retry-button");
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

  it("a failed wallet sign-in shows the error with a retry that returns to the pick screen", async () => {
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

    click("sign-in-retry-button");
    expect(
      container.querySelector("[data-testid='sign-in-wallet-button']"),
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
});
