// @vitest-environment jsdom
import {
  TransportProvider,
  type ComposerConnectorsPort,
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

function renderEmpty(
  ctx: RunEmptyComposerCtx,
  connectorsPort?: ComposerConnectorsPort,
): { container: HTMLElement } {
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport()}>
      <RunEmptyComposer ctx={ctx} connectorsPort={connectorsPort} />
    </TransportProvider>
  );
  return render(ui);
}

// A connectors port whose reads resolve to an empty MCP surface — enough to
// mount the Tools popover (empty state; no connect/auth exercised).
function fakeConnectorsPort(): ComposerConnectorsPort {
  return {
    listServers: () => Promise.resolve([]),
    listCatalog: () => Promise.resolve([]),
    installFromCatalog: () => Promise.reject(new Error("unused")),
    addCustomServer: () => Promise.reject(new Error("unused")),
    beginAuth: () => Promise.resolve(),
  };
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

  it("stays LIVE with no model configured — the send still reaches the cockpit seam", async () => {
    const ctx = makeCtx({ modelReady: false });
    const { container } = renderEmpty(ctx);
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    // Not greyed out: readiness alone never disables the composer. The cockpit
    // answers an unconfigured model with the inline error strip below.
    expect(textarea(container)?.disabled).toBe(false);

    fireEvent.change(textarea(container) as HTMLTextAreaElement, {
      target: { value: "Watch my wallet" },
    });
    fireEvent.click(
      container.querySelector(
        "button[aria-label='Send message']",
      ) as HTMLButtonElement,
    );
    await waitFor(() => expect(ctx.onStartRun).toHaveBeenCalledTimes(1));
  });

  it("disables the composer only while a start is in flight", async () => {
    const { container } = renderEmpty(makeCtx({ submitting: true }));
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    expect(textarea(container)?.disabled).toBe(true);
  });

  it("mounts the inline Tools popover trigger when a connectorsPort is provided", async () => {
    const { container } = renderEmpty(makeCtx(), fakeConnectorsPort());
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    expect(
      container.querySelector("[data-testid='first-run-tools-button']"),
    ).not.toBeNull();
  });

  it("threads webSearchEnabled=false into the start-run payload when web search is toggled off", async () => {
    const ctx = makeCtx();
    const { container } = renderEmpty(ctx, fakeConnectorsPort());
    await waitFor(() => expect(textarea(container)).not.toBeNull());

    fireEvent.click(
      container.querySelector(
        "[data-testid='first-run-tools-button']",
      ) as HTMLButtonElement,
    );
    const toggle = await waitFor(() => {
      const t = container.querySelector(
        "[data-testid='first-run-tools-websearch']",
      );
      expect(t).not.toBeNull();
      return t as HTMLButtonElement;
    });
    fireEvent.click(toggle);

    const ta = textarea(container) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "Run offline please" } });
    fireEvent.click(
      container.querySelector<HTMLButtonElement>(
        "button[aria-label='Send message']",
      ) as HTMLButtonElement,
    );

    await waitFor(() => {
      expect(ctx.onStartRun).toHaveBeenCalledTimes(1);
    });
    const arg = (ctx.onStartRun as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg.webSearchEnabled).toBe(false);
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

  it("renders the cockpit's no-model error as the inline strip + 'Add a key' CTA", async () => {
    const ctx = makeCtx({
      modelReady: false,
      startError: {
        message: "No model configured — connect one to run.",
        code: "configuration_error",
      },
    });
    const { container } = renderEmpty(ctx);
    const cta = await waitFor(() => {
      const el = container.querySelector<HTMLButtonElement>(
        "[data-testid='first-run-composer-error-cta']",
      );
      expect(el).not.toBeNull();
      return el as HTMLButtonElement;
    });
    expect(
      container.querySelector("[data-testid='first-run-composer-error']")
        ?.className,
    ).toContain("fr-cerr");
    expect(
      container.querySelector(
        "[data-testid='first-run-composer-error-message']",
      )?.textContent,
    ).toBe("No model configured — connect one to run.");
    fireEvent.click(cta);
    expect(ctx.onOpenModelSettings).toHaveBeenCalledTimes(1);
  });
});
