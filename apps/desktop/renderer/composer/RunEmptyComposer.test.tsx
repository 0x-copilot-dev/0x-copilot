// @vitest-environment jsdom
import {
  TransportProvider,
  type RunEmptyComposerCtx,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunEmptyComposer } from "./RunEmptyComposer";

// globals: false in the desktop vitest config → register cleanup explicitly.
afterEach(() => {
  cleanup();
});

// jsdom ships no IntersectionObserver; the composer's caret path wants one.
class NoopIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): unknown[] {
    return [];
  }
}
if (typeof globalThis.IntersectionObserver === "undefined") {
  (
    globalThis as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = NoopIntersectionObserver;
}

function payloadFor(path: string): Record<string, unknown> {
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/mcp/servers")) return { servers: [] };
  if (path.includes("/v1/settings/provider-keys")) {
    return { keys: [{ provider: "openai" }] };
  }
  if (path.includes("/v1/local-models")) return { models: [] };
  if (path.includes("/v1/agent/workspace/defaults")) return {};
  return {};
}

function fakeTransport(): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> =>
      Promise.resolve(payloadFor(req.path) as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "desktop-webview",
      nativeSecretStorage: true,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function makeCtx(over: Partial<RunEmptyComposerCtx> = {}): RunEmptyComposerCtx {
  return {
    onStartRun: vi.fn(),
    submitting: false,
    startError: null,
    dismissError: vi.fn(),
    modelReady: true,
    onOpenModelSettings: vi.fn(),
    ...over,
  };
}

function renderEmpty(ctx: RunEmptyComposerCtx): { container: HTMLElement } {
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport()}>
      <RunEmptyComposer ctx={ctx} />
    </TransportProvider>
  );
  return render(ui);
}

function textarea(container: HTMLElement): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
}

describe("RunEmptyComposer", () => {
  it("renders the design's 'What should we run first?' hero + starter chips", async () => {
    const { container } = renderEmpty(makeCtx());
    await waitFor(() => {
      expect(
        container.querySelector("[data-testid='first-run-composer-h1']"),
      ).not.toBeNull();
    });
    expect(
      container.querySelector("[data-testid='first-run-composer-h1']")
        ?.textContent,
    ).toBe("What should we run first?");
    // The three starter chips render (design parity with the FTUE composer).
    expect(
      container.querySelector("[data-testid='first-run-chip-watch-wallet']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='first-run-chip-explain-csv']"),
    ).not.toBeNull();
  });

  it("a suggestion chip fills the composer with its verbatim prompt", async () => {
    const { container } = renderEmpty(makeCtx());
    await waitFor(() => expect(textarea(container)).not.toBeNull());

    fireEvent.click(
      container.querySelector(
        "[data-testid='first-run-chip-watch-wallet']",
      ) as HTMLButtonElement,
    );

    await waitFor(() => {
      expect(textarea(container)?.value).toContain(
        "Watch 0x7f3C…a92C and alert me",
      );
    });
  });

  it("send forwards the rich payload (goal + model + web-search) to the cockpit seam", async () => {
    const ctx = makeCtx();
    const { container } = renderEmpty(ctx);
    await waitFor(() => expect(textarea(container)).not.toBeNull());

    const ta = textarea(container) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "Draft the launch thread" } });
    const send = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Send message']",
    );
    fireEvent.click(send as HTMLButtonElement);

    await waitFor(() => {
      expect(ctx.onStartRun).toHaveBeenCalledTimes(1);
    });
    const arg = (ctx.onStartRun as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg).toMatchObject({
      goal: "Draft the launch thread",
      webSearchEnabled: true,
    });
    // A configured provider (openai) resolves a concrete model selection.
    expect(arg.model).toBeTruthy();
  });

  it("disables the composer when no model is configured (readiness gate)", async () => {
    const { container } = renderEmpty(makeCtx({ modelReady: false }));
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    expect(textarea(container)?.disabled).toBe(true);
  });

  it("surfaces the cockpit's start error inline", async () => {
    const { container } = renderEmpty(
      makeCtx({
        startError: {
          message:
            "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
          code: "configuration_error",
        },
      }),
    );
    await waitFor(() => {
      const msg = container.querySelector(
        "[data-testid='first-run-composer-error-message']",
      );
      expect(msg?.textContent).toContain("Missing API key");
    });
  });
});
