// @vitest-environment jsdom
import {
  TransportProvider,
  type ComposerConnectorsPort,
  type CompleteAttachment,
  type RunStartRequest,
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

import { RunComposer } from "./RunComposer";
import { createDesktopAttachmentAdapter } from "./desktopAttachmentAdapter";

// globals: false in the desktop vitest config → register cleanup explicitly.
afterEach(() => {
  cleanup();
});

// jsdom ships no IntersectionObserver; the composer's markdown/caret path wants
// one. A no-op keeps the tree renderable.
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

interface Recorder {
  readonly calls: TypedRequest[];
}

// Phase 5b: RunComposer no longer POSTs its own run — it routes every send
// through the cockpit's ONE `dispatch` (injected via the renderComposer ctx),
// which starts AND binds the run. The transport here only serves the composer's
// mount GETs (skills / MCP servers / provider keys / local models / workspace
// defaults); run-create success/failure is modeled on the `dispatch` mock.
function fakeTransport(recorder: Recorder): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      const body = payloadFor(req.path);
      return Promise.resolve(body as unknown as TRes);
    },
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

function payloadFor(path: string): Record<string, unknown> {
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/mcp/servers")) return { servers: [] };
  if (path.includes("/v1/settings/provider-keys")) {
    return { keys: [{ provider: "openai" }] };
  }
  if (path.includes("/v1/local-models")) return { models: [] };
  if (path.includes("/v1/agent/workspace/defaults")) {
    // The persisted wizard pick: an OpenAI model OUTSIDE the curated set, so
    // seeding must synthesize a picker entry rather than match one.
    return { default_model: { provider: "openai", model_name: "gpt-4o" } };
  }
  return {};
}

function renderComposer(
  props: Partial<React.ComponentProps<typeof RunComposer>> = {},
): {
  recorder: Recorder;
  container: HTMLElement;
  dispatch: ReturnType<typeof vi.fn>;
} {
  const recorder: Recorder = { calls: [] };
  // Default dispatch resolves (a successful send); tests override it to reject.
  const dispatch = vi.fn(
    async (_request: RunStartRequest): Promise<void> => {},
  );
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport(recorder)}>
      <RunComposer
        dispatch={dispatch}
        disabled={false}
        placeholder="Send a message…"
        {...props}
      />
    </TransportProvider>
  );
  const { container } = render(ui);
  return { recorder, container, dispatch };
}

function textarea(container: HTMLElement): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
}

const CONFIG_ERROR_MESSAGE =
  "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.";

// The facade envelope for the missing-provider-key configuration error, wrapped
// under `detail` exactly as the runtime → facade returns it. `dispatch` rejects
// with this (the run-create failure now lives inside the cockpit dispatch), so
// parseTransportError has a real envelope to recover `code`/`safe_message` from.
function configErrorEnvelope(): Error {
  return new Error(
    JSON.stringify({
      detail: {
        code: "configuration_error",
        safe_message: CONFIG_ERROR_MESSAGE,
        correlation_id: "abc123",
      },
    }),
  );
}

// Type a goal into the base composer textarea and click AssistantComposer's
// Send control (aria-label "Send message"; it drives the imperative submit()).
function typeAndSend(container: HTMLElement, text: string): void {
  const ta = textarea(container);
  if (ta === null) throw new Error("composer textarea not mounted");
  fireEvent.change(ta, { target: { value: text } });
  const send = container.querySelector<HTMLButtonElement>(
    "button[aria-label='Send message']",
  );
  if (send === null) throw new Error("composer send button not mounted");
  fireEvent.click(send);
}

describe("RunComposer", () => {
  it("mounts the shared AssistantComposer (base composer textarea present)", async () => {
    const { container } = renderComposer();
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
  });

  it("fetches skills, MCP servers, provider keys, and local models on mount", async () => {
    const { recorder } = renderComposer();
    await waitFor(() => {
      const paths = recorder.calls.map((c) => c.path);
      expect(paths).toContain("/v1/skills");
      expect(paths).toContain("/v1/mcp/servers");
      expect(paths).toContain("/v1/settings/provider-keys");
      expect(paths).toContain("/v1/local-models");
    });
  });

  it("seeds the picker from the persisted workspace default model", async () => {
    const { recorder, container } = renderComposer();
    await waitFor(() => {
      expect(recorder.calls.map((c) => c.path)).toContain(
        "/v1/agent/workspace/defaults",
      );
    });
    // gpt-4o is outside the curated set → appended as a synthetic entry and
    // selected, so the model pill announces it.
    await waitFor(() => {
      expect(
        container.querySelector('[aria-label="Model: gpt-4o"]'),
      ).not.toBeNull();
    });
  });

  it("disables the composer textarea when the cockpit is scrubbed off-live", async () => {
    const { container } = renderComposer({ disabled: true });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
    expect(textarea(container)?.disabled).toBe(true);
  });

  it("routes a send through the cockpit dispatch with the RunStartRequest (goal + model), not its own POST", async () => {
    const { recorder, container, dispatch } = renderComposer();
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    typeAndSend(container, "Draft the launch note");

    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledTimes(1);
    });
    const request = dispatch.mock.calls[0][0] as RunStartRequest;
    expect(request).toMatchObject({ goal: "Draft the launch note" });
    // A configured provider (openai) resolves a concrete model selection.
    expect(request.model).toBeTruthy();
    // The composer never POSTs its own run — the dispatch owns run creation.
    expect(
      recorder.calls.some(
        (c) => c.method === "POST" && c.path === "/v1/agent/runs",
      ),
    ).toBe(false);
  });

  it("surfaces a missing-key run-create failure as an 'Add a provider key' CTA that routes to provider-key settings (no silent dead end)", async () => {
    const onOpenModelSettings = vi.fn();
    // The cockpit dispatch rejects (keyless run-create) — the rejection routes
    // to the composer's onSubmitError channel instead of vanishing.
    const dispatch = vi.fn(async (): Promise<void> => {
      throw configErrorEnvelope();
    });
    const { container } = renderComposer({ dispatch, onOpenModelSettings });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
    // No notice before a send — the default path is unchanged.
    expect(
      container.querySelector("[data-testid='run-composer-error']"),
    ).toBeNull();

    typeAndSend(container, "Draft the launch note");

    // The rejection that used to be swallowed is now surfaced: the actionable
    // safe_message as the primary line — never the raw JSON envelope.
    const message = await waitFor(() => {
      const m = container.querySelector(
        "[data-testid='run-composer-error-message']",
      );
      expect(m).not.toBeNull();
      return m as HTMLElement;
    });
    expect(message.textContent).toContain(
      "Missing API key for model provider 'openai'",
    );
    expect(message.textContent).not.toContain("{");

    // The config-error CTA deep-links into Settings → Provider keys.
    const cta = container.querySelector(
      "[data-testid='run-composer-error-cta']",
    ) as HTMLButtonElement | null;
    expect(cta).not.toBeNull();
    fireEvent.click(cta as HTMLButtonElement);
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  it("shows the message but hides the provider-key CTA for a non-configuration failure", async () => {
    const onOpenModelSettings = vi.fn();
    const dispatch = vi.fn(async (): Promise<void> => {
      throw new Error(
        JSON.stringify({
          detail: { code: "internal_error", safe_message: "Server error." },
        }),
      );
    });
    const { container } = renderComposer({ dispatch, onOpenModelSettings });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    typeAndSend(container, "Do a thing");

    await waitFor(() => {
      expect(
        container.querySelector("[data-testid='run-composer-error-message']")
          ?.textContent,
      ).toContain("Server error.");
    });
    // The provider-key CTA is gated to the configuration error only.
    expect(
      container.querySelector("[data-testid='run-composer-error-cta']"),
    ).toBeNull();
    expect(onOpenModelSettings).not.toHaveBeenCalled();
  });

  it("clears the run-create error notice once a later send succeeds", async () => {
    // Reject only the first dispatch (run-create); the retry resolves.
    let firstSend = true;
    const dispatch = vi.fn(async (): Promise<void> => {
      if (firstSend) {
        firstSend = false;
        throw configErrorEnvelope();
      }
    });
    const { container } = renderComposer({ dispatch });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    typeAndSend(container, "first try");
    await waitFor(() => {
      expect(
        container.querySelector("[data-testid='run-composer-error']"),
      ).not.toBeNull();
    });

    typeAndSend(container, "second try");
    await waitFor(() => {
      expect(
        container.querySelector("[data-testid='run-composer-error']"),
      ).toBeNull();
    });
  });
});

// A connectors port whose reads resolve to an empty MCP surface — enough to
// mount the Tools popover (it shows its empty state, no connect/auth needed).
function fakeConnectorsPort(): ComposerConnectorsPort {
  return {
    listServers: () => Promise.resolve([]),
    listCatalog: () => Promise.resolve([]),
    installFromCatalog: () =>
      Promise.reject(new Error("unused in these tests")),
    addCustomServer: () => Promise.reject(new Error("unused in these tests")),
    beginAuth: () => Promise.resolve(),
  };
}

describe("RunComposer inline Tools popover", () => {
  it("renders the connector-aware Tools button (not the flat connectors button) when a connectorsPort is provided", async () => {
    const { container } = renderComposer({
      connectorsPort: fakeConnectorsPort(),
    });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
    expect(
      container.querySelector("[data-testid='first-run-tools-button']"),
    ).not.toBeNull();
  });

  it("falls back to the flat connectors button when no connectorsPort is provided", async () => {
    const { container } = renderComposer();
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
    expect(
      container.querySelector("[data-testid='first-run-tools-button']"),
    ).toBeNull();
  });

  it("threads webSearchEnabled=false into the dispatched request when web search is toggled off", async () => {
    const { container, dispatch } = renderComposer({
      connectorsPort: fakeConnectorsPort(),
    });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    // Open the popover, then turn the default-on web-search toggle OFF.
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

    typeAndSend(container, "Summarize without the web");

    // Run-create routes through the cockpit dispatch (§D3), not a direct POST;
    // the explicit opt-out rides on the RunStartRequest (buildRunCreateBody then
    // emits `web_search_enabled: false`).
    await waitFor(() => expect(dispatch).toHaveBeenCalledTimes(1));
    const request = dispatch.mock.calls[0][0] as RunStartRequest;
    expect(request.webSearchEnabled).toBe(false);
  });

  it("leaves webSearchEnabled on (runtime default) in the dispatched request when web search stays on", async () => {
    const { container, dispatch } = renderComposer({
      connectorsPort: fakeConnectorsPort(),
    });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    typeAndSend(container, "Do the thing");

    await waitFor(() => expect(dispatch).toHaveBeenCalledTimes(1));
    const request = dispatch.mock.calls[0][0] as RunStartRequest;
    // Not an explicit opt-out → stays true; buildRunCreateBody omits `true`.
    expect(request.webSearchEnabled).not.toBe(false);
  });
});

describe("createDesktopAttachmentAdapter", () => {
  it("reads an image into an image content part", async () => {
    const adapter = createDesktopAttachmentAdapter();
    const file = new File(["hello"], "shot.png", { type: "image/png" });
    const att = (await adapter.add(file)) as CompleteAttachment;
    expect(att.name).toBe("shot.png");
    expect(att.type).toBe("image/png");
    expect(att.content?.[0]).toMatchObject({ type: "image" });
    expect(att.status).toEqual({ type: "complete" });
  });

  it("reads a non-image into a file content part", async () => {
    const adapter = createDesktopAttachmentAdapter();
    const file = new File(["col1,col2"], "data.csv", { type: "text/csv" });
    const att = (await adapter.add(file)) as CompleteAttachment;
    expect(att.content?.[0]).toMatchObject({
      type: "file",
      name: "data.csv",
      mime: "text/csv",
    });
  });
});
