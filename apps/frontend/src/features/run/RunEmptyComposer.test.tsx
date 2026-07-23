// RunEmptyComposer (web) — the design's "What should we run first?" empty
// composer, bound to the web onboarding substrate. Mirrors the desktop
// RunEmptyComposer.test: renders hero + chips, a chip fills the composer, and a
// send forwards the rich payload (goal + model + web-search) to the cockpit
// seam. The live `/v1/agent/models` catalog is mocked so the model pill resolves
// a concrete selection.

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

// The live catalog read the web composer's model pill uses. Mock it so a
// configured model resolves without a real facade.
vi.mock("../../api/agentApi", () => ({
  listModels: async () => ({
    default_model_id: "gpt-5.2",
    models: [
      {
        id: "gpt-5.2",
        provider: "openai",
        model_name: "gpt-5.2",
        name: "GPT-5.2",
        configured: true,
        supports_streaming: true,
      },
    ],
  }),
}));

import type { RequestIdentity } from "../../api/config";
import { RunEmptyComposer } from "./RunEmptyComposer";

afterEach(() => {
  cleanup();
});

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

const IDENTITY: RequestIdentity = { orgId: "org-1", userId: "user-1" };

function fakeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({} as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => undefined }),
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
      <RunEmptyComposer ctx={ctx} identity={IDENTITY} />
    </TransportProvider>
  );
  return render(ui);
}

function textarea(container: HTMLElement): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
}

describe("RunEmptyComposer (web)", () => {
  it("renders the 'What should we run first?' hero + starter chips", async () => {
    const { container } = renderEmpty(makeCtx());
    await waitFor(() =>
      expect(
        container.querySelector("[data-testid='first-run-composer-h1']"),
      ).not.toBeNull(),
    );
    expect(
      container.querySelector("[data-testid='first-run-composer-h1']")
        ?.textContent,
    ).toBe("What should we run first?");
    expect(
      container.querySelector("[data-testid='first-run-chip-watch-wallet']"),
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
    await waitFor(() =>
      expect(textarea(container)?.value).toContain(
        "Watch 0x7f3C…a92C and alert me",
      ),
    );
  });

  it("send forwards the rich payload (goal + model + web-search) to the cockpit seam", async () => {
    const ctx = makeCtx();
    const { container } = renderEmpty(ctx);
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    fireEvent.change(textarea(container) as HTMLTextAreaElement, {
      target: { value: "Draft the launch thread" },
    });
    fireEvent.click(
      container.querySelector(
        "button[aria-label='Send message']",
      ) as HTMLButtonElement,
    );
    await waitFor(() => expect(ctx.onStartRun).toHaveBeenCalledTimes(1));
    const arg = (ctx.onStartRun as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg).toMatchObject({
      goal: "Draft the launch thread",
      webSearchEnabled: true,
    });
    expect(arg.model).toBeTruthy();
  });

  it("stays LIVE with no model configured — the send still reaches the cockpit seam", async () => {
    const ctx = makeCtx({ modelReady: false });
    const { container } = renderEmpty(ctx);
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    // Not greyed out: the composer is never disabled on readiness alone. The
    // cockpit answers an unconfigured model with the inline error strip below.
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
