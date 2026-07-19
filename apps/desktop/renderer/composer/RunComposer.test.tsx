// @vitest-environment jsdom
import {
  TransportProvider,
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
import { cleanup, render, waitFor } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

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

  it("disables the composer textarea when the cockpit is scrubbed off-live", async () => {
    const { container } = renderComposer({ disabled: true });
    await waitFor(() => {
      expect(textarea(container)).not.toBeNull();
    });
    expect(textarea(container)?.disabled).toBe(true);
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
