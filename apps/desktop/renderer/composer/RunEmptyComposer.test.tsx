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
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CAPABILITY_CHANNELS } from "../../main/capabilities/channels";
import type { WindowBridge } from "../../preload/window-bridge-types";
import { RunEmptyComposer } from "./RunEmptyComposer";

// globals: false in the desktop vitest config → register cleanup explicitly.
afterEach(() => {
  cleanup();
  // The grant port memoizes per BRIDGE, so dropping it between tests is what
  // keeps one test's grant list out of the next one's composer.
  delete (globalThis.window as unknown as { bridge?: WindowBridge }).bridge;
});

/** The Electron bridge, answering the capability channels however a test says. */
function installBridge(answers: Record<string, unknown>): {
  readonly invoke: ReturnType<typeof vi.fn>;
} {
  const invoke = vi.fn(async (channel: string, _payload: unknown) => {
    const answer = answers[channel];
    if (typeof answer === "function") return (answer as () => unknown)();
    return answer ?? null;
  });
  (globalThis.window as unknown as { bridge?: WindowBridge }).bridge = {
    ipc: {
      invoke: invoke as unknown as WindowBridge["ipc"]["invoke"],
      on: () => () => {},
    },
  };
  return { invoke };
}

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
  if (path.includes("/v1/agent/models")) {
    // The one backend catalog — a configured model so the picker has a
    // selectable default and a send resolves a concrete `model`.
    return {
      default_model_id: "gpt-5.4-mini",
      models: [
        {
          id: "gpt-5.4-mini",
          provider: "openai",
          model_name: "gpt-5.4-mini",
          name: "GPT-5.4 Mini",
          configured: true,
          supports_streaming: true,
        },
      ],
    };
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
    beginAuth: () => Promise.resolve(),
    deleteServer: () => Promise.reject(new Error("unused")),
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

  it("renders the portal-safe Tools pill when a connectorsPort is provided", async () => {
    const { container } = renderEmpty(makeCtx(), fakeConnectorsPort());
    await waitFor(() => expect(textarea(container)).not.toBeNull());
    fireEvent.click(
      container.querySelector(
        "[data-testid='first-run-tools-button']",
      ) as HTMLButtonElement,
    );
    expect(
      document.querySelector("[data-testid='first-run-tools-websearch']"),
    ).not.toBeNull();
  });

  it("routes the empty-composer model footer to Settings instead of an inline key form", async () => {
    const ctx = makeCtx();
    const { container } = renderEmpty(ctx);
    const modelButton = await waitFor(() => {
      const button = container.querySelector<HTMLButtonElement>(
        "button[aria-label^='Model:']",
      );
      expect(button).not.toBeNull();
      return button as HTMLButtonElement;
    });
    fireEvent.click(modelButton);
    const addKey = await waitFor(() => {
      const link = [...document.querySelectorAll<HTMLAnchorElement>("a")].find(
        (candidate) => candidate.textContent === "Add a provider key →",
      );
      expect(link).not.toBeUndefined();
      return link as HTMLAnchorElement;
    });

    fireEvent.click(addKey);
    expect(ctx.onOpenModelSettings).toHaveBeenCalledTimes(1);
    // The popover closes and never mounts its inline plaintext-key form.
    expect(document.querySelector("[data-testid='key-form']")).toBeNull();
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
      const t = document.querySelector(
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

  // PRD-FS-10 §7. This is the cockpit's PRE-first-message composer, so it is
  // one of the two mounts that must carry the folder-grant port. The port is
  // bridged off `window.bridge`, so this drives the real capability channel
  // rather than injecting a port — a wire that only exists when a test supplies
  // it is exactly the wire that goes missing in the app.
  it("names the granted folder above the composer (folder bar)", async () => {
    const { invoke } = installBridge({
      [CAPABILITY_CHANNELS.listGrants]: [
        {
          grantId: "grant_ke",
          label: "kaleidoscope",
          mode: "read_only",
          status: "active",
        },
      ],
    });
    renderEmpty(makeCtx());

    expect(
      await screen.findByRole("button", { name: /^kaleidoscope/ }),
    ).not.toBeNull();
    expect(invoke).toHaveBeenCalledWith(
      CAPABILITY_CHANNELS.listGrants,
      expect.anything(),
    );
  });

  it("offers the empty affordance when no folder is granted yet", async () => {
    installBridge({ [CAPABILITY_CHANNELS.listGrants]: [] });
    renderEmpty(makeCtx());

    expect(
      await screen.findByRole("button", { name: /Attach a folder/i }),
    ).not.toBeNull();
  });

  it("keeps Attach Folder out of the `+` menu — one entry point, not two", async () => {
    installBridge({ [CAPABILITY_CHANNELS.listGrants]: [] });
    const { container } = renderEmpty(makeCtx());
    await waitFor(() => expect(textarea(container)).not.toBeNull());

    fireEvent.click(
      container.querySelector<HTMLButtonElement>(
        "button[aria-label='Open attachment and tools menu']",
      ) as HTMLButtonElement,
    );

    expect(
      screen.queryByRole("menuitem", { name: /Attach Folder/i }),
    ).toBeNull();
    expect(screen.getByRole("menuitem", { name: /Attach File/i })).toBeTruthy();
  });
});
