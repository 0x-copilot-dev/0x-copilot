// @vitest-environment jsdom
import {
  TransportProvider,
  type ComposerConnectorsPort,
  type CompleteAttachment,
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
): { recorder: Recorder; container: HTMLElement } {
  const recorder: Recorder = { calls: [] };
  const ui: ReactElement = (
    <TransportProvider transport={fakeTransport(recorder)}>
      <RunComposer
        conversationId="conv-1"
        disabled={false}
        placeholder="Send a message…"
        {...props}
      />
    </TransportProvider>
  );
  const { container } = render(ui);
  return { recorder, container };
}

function textarea(container: HTMLElement): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
}

const CONFIG_ERROR_MESSAGE =
  "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.";

// A transport whose mount GETs resolve normally but whose run-create POST
// REJECTS — the keyless dead end. `rejection.message` is the stringified error
// body both the web WebTransport and the desktop IPC bridge throw, so
// parseTransportError has a real envelope to recover `code`/`safe_message` from.
function fakeTransportRejectingRuns(
  recorder: Recorder,
  rejection: Error,
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      if (req.method === "POST" && req.path === "/v1/agent/runs") {
        return Promise.reject(rejection);
      }
      return Promise.resolve(payloadFor(req.path) as unknown as TRes);
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

// The facade envelope for the missing-provider-key configuration error, wrapped
// under `detail` exactly as the runtime → facade returns it.
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

  it("surfaces a missing-key run-create failure as an 'Add a provider key' CTA that routes to provider-key settings (no silent dead end)", async () => {
    const recorder: Recorder = { calls: [] };
    const onOpenModelSettings = vi.fn();
    const { container } = render(
      <TransportProvider
        transport={fakeTransportRejectingRuns(recorder, configErrorEnvelope())}
      >
        <RunComposer
          conversationId="conv-1"
          disabled={false}
          placeholder="Send a message…"
          onOpenModelSettings={onOpenModelSettings}
        />
      </TransportProvider>,
    );
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
    const recorder: Recorder = { calls: [] };
    const onOpenModelSettings = vi.fn();
    const { container } = render(
      <TransportProvider
        transport={fakeTransportRejectingRuns(
          recorder,
          new Error(
            JSON.stringify({
              detail: { code: "internal_error", safe_message: "Server error." },
            }),
          ),
        )}
      >
        <RunComposer
          conversationId="conv-1"
          disabled={false}
          placeholder="Send a message…"
          onOpenModelSettings={onOpenModelSettings}
        />
      </TransportProvider>,
    );
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
    const recorder: Recorder = { calls: [] };
    // Reject only the first run-create POST; the retry resolves.
    let firstPost = true;
    const transport: Transport = {
      request: <TRes,>(req: TypedRequest): Promise<TRes> => {
        recorder.calls.push(req);
        if (req.method === "POST" && req.path === "/v1/agent/runs") {
          if (firstPost) {
            firstPost = false;
            return Promise.reject(configErrorEnvelope());
          }
          return Promise.resolve({ run_id: "run-1" } as unknown as TRes);
        }
        return Promise.resolve(payloadFor(req.path) as unknown as TRes);
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
    const { container } = render(
      <TransportProvider transport={transport}>
        <RunComposer
          conversationId="conv-1"
          disabled={false}
          placeholder="Send a message…"
        />
      </TransportProvider>,
    );
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

  it("threads an explicit web_search_enabled=false into the run body when web search is toggled off", async () => {
    const { recorder, container } = renderComposer({
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

    await waitFor(() => {
      const post = recorder.calls.find(
        (c) => c.method === "POST" && c.path === "/v1/agent/runs",
      );
      expect(post).toBeDefined();
      expect((post?.body as Record<string, unknown>).web_search_enabled).toBe(
        false,
      );
    });
  });

  it("omits web_search_enabled from the run body when web search stays on (runtime default)", async () => {
    const { recorder, container } = renderComposer({
      connectorsPort: fakeConnectorsPort(),
    });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });

    typeAndSend(container, "Do the thing");

    await waitFor(() => {
      const post = recorder.calls.find(
        (c) => c.method === "POST" && c.path === "/v1/agent/runs",
      );
      expect(post).toBeDefined();
      expect(
        "web_search_enabled" in (post?.body as Record<string, unknown>),
      ).toBe(false);
    });
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
