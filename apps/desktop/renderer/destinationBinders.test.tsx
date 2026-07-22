// @vitest-environment jsdom
//
// desktop-run-identity Phase 5b — the desktop nav-identity contract for the two
// binders that changed:
//
//   • RunBinder no longer self-creates a conversation on mount (the racy
//     `GET conversations?limit=1`-else-`POST {title:"Desktop session"}` that
//     produced duplicate rows is gone). A brand-new chat (`conversationId=null`)
//     creates its conversation LAZILY on the first send via one
//     `POST /v1/agent/runs` that OMITS `conversation_id` and carries a stable
//     `conversation_idempotency_key`, then hands the created id back through
//     `onConversationCreated`. An existing conversation posts against its id.
//   • ChatsBinder threads the row's REAL id: reopen → `onOpenConversation(id)`.
import {
  KeyValueStoreProvider,
  RouterProvider,
  TransportProvider,
  type ArtifactRoute,
  type ConversationId,
  type KeyValueStore,
  type Router,
} from "@0x-copilot/chat-surface";
import type {
  Conversation,
  ConversationListResponse,
} from "@0x-copilot/api-types";
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

import { ChatsBinder, RunBinder } from "./destinationBinders";

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

interface Recorder {
  readonly calls: TypedRequest[];
}

// The mount GETs a Run cockpit + composer make; every list resolves empty so the
// session lands on its idle empty-state composer (no bound run) and a provider
// key keeps the readiness gate open (composer enabled → sendable).
function payloadFor(path: string): Record<string, unknown> {
  if (path.includes("/v1/skills")) return { skills: [] };
  if (path.includes("/v1/mcp/servers")) return { servers: [] };
  if (path.includes("/v1/settings/provider-keys")) {
    return { keys: [{ provider: "openai" }] };
  }
  if (path.includes("/v1/local-models")) return { models: [] };
  if (path.includes("/v1/agent/workspace/defaults")) {
    return { default_model: { provider: "openai", model_name: "gpt-4o" } };
  }
  if (path.includes("/messages")) return { messages: [] };
  if (path.includes("/v1/agent/conversations")) return { conversations: [] };
  return {};
}

function runTransport(
  recorder: Recorder,
  runResponse: { run_id: string; conversation_id?: string },
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      if (req.method === "POST" && req.path === "/v1/agent/runs") {
        return Promise.resolve(runResponse as unknown as TRes);
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

function chatsTransport(
  recorder: Recorder,
  conversations: readonly Conversation[],
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      const body: ConversationListResponse = {
        conversations: [...conversations],
        next_cursor: null,
        has_more: false,
      };
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

function fakeRouter(): Router<ArtifactRoute | null> {
  return {
    current: () => null,
    navigate: () => undefined,
    subscribe: () => () => undefined,
  };
}

function fakeKeyValueStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) map.delete(key);
      else map.set(key, value);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

function renderRunBinder(
  transport: Transport,
  props: {
    conversationId: ConversationId | null;
    onConversationCreated?: (id: ConversationId) => void;
  },
): HTMLElement {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={fakeKeyValueStore()}>
        <RouterProvider router={fakeRouter()}>
          <RunBinder
            conversationId={props.conversationId}
            onConversationCreated={props.onConversationCreated}
          />
        </RouterProvider>
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui).container;
}

function textarea(container: HTMLElement): HTMLTextAreaElement | null {
  return container.querySelector<HTMLTextAreaElement>(
    "[data-testid='composer-textarea']",
  );
}

async function typeAndSend(
  container: HTMLElement,
  text: string,
): Promise<void> {
  await waitFor(() => expect(textarea(container)).not.toBeNull());
  const ta = textarea(container) as HTMLTextAreaElement;
  fireEvent.change(ta, { target: { value: text } });
  const send = container.querySelector<HTMLButtonElement>(
    "button[aria-label='Send message']",
  );
  if (send === null) throw new Error("composer send button not mounted");
  fireEvent.click(send);
}

function lastRunPost(recorder: Recorder): Record<string, unknown> | null {
  for (let i = recorder.calls.length - 1; i >= 0; i--) {
    const c = recorder.calls[i];
    if (c.method === "POST" && c.path === "/v1/agent/runs") {
      return (c.body ?? {}) as Record<string, unknown>;
    }
  }
  return null;
}

describe("RunBinder — new-chat first send (lazy, idempotent creation)", () => {
  it("posts a run WITHOUT conversation_id + WITH an idempotency key, then navigates to the created conversation", async () => {
    const recorder: Recorder = { calls: [] };
    const onConversationCreated = vi.fn();
    const transport = runTransport(recorder, {
      run_id: "run-1",
      conversation_id: "conv-created",
    });
    // A brand-new chat: no conversation id from the nav.
    const container = renderRunBinder(transport, {
      conversationId: null,
      onConversationCreated,
    });

    await typeAndSend(container, "Watch this wallet");

    await waitFor(() => {
      expect(lastRunPost(recorder)).not.toBeNull();
    });
    const body = lastRunPost(recorder) as Record<string, unknown>;
    // No conversation_id — the server get-or-creates it in one transaction…
    expect(body).not.toHaveProperty("conversation_id");
    // …keyed by a stable idempotency key so a double-tap collapses to one row.
    expect(typeof body.conversation_idempotency_key).toBe("string");
    expect(
      (body.conversation_idempotency_key as string).length,
    ).toBeGreaterThan(0);
    // The goal still rides along on the shared run body.
    expect(body.user_input).toBe("Watch this wallet");

    // The created conversation id is handed back so the host navigates to it.
    await waitFor(() => {
      expect(onConversationCreated).toHaveBeenCalledWith("conv-created");
    });
  });

  it("does NOT create a conversation on mount (the racy self-create is gone)", async () => {
    const recorder: Recorder = { calls: [] };
    renderRunBinder(runTransport(recorder, { run_id: "run-x" }), {
      conversationId: null,
    });
    // Let the mount effects settle.
    await waitFor(() => {
      expect(
        recorder.calls.some((c) =>
          c.path.includes("/v1/settings/provider-keys"),
        ),
      ).toBe(true);
    });
    // Nothing ever POSTs to create a conversation, and no run is started before
    // the user sends.
    expect(
      recorder.calls.some(
        (c) => c.method === "POST" && c.path === "/v1/agent/conversations",
      ),
    ).toBe(false);
    expect(
      recorder.calls.some(
        (c) => c.method === "POST" && c.path === "/v1/agent/runs",
      ),
    ).toBe(false);
  });
});

describe("RunBinder — existing conversation", () => {
  it("posts a run WITH the threaded conversation_id and no idempotency key; does not re-create", async () => {
    const recorder: Recorder = { calls: [] };
    const onConversationCreated = vi.fn();
    const container = renderRunBinder(
      runTransport(recorder, { run_id: "run-2" }),
      {
        conversationId: "conv-existing" as ConversationId,
        onConversationCreated,
      },
    );

    await typeAndSend(container, "Follow up on that");

    await waitFor(() => {
      expect(lastRunPost(recorder)).not.toBeNull();
    });
    const body = lastRunPost(recorder) as Record<string, unknown>;
    expect(body.conversation_id).toBe("conv-existing");
    expect(body).not.toHaveProperty("conversation_idempotency_key");
    // An existing conversation is never re-created.
    expect(onConversationCreated).not.toHaveBeenCalled();
  });
});

describe("ChatsBinder — reopen threads the real conversation id", () => {
  it("invokes onOpenConversation with the row's conversation id on reopen", async () => {
    const recorder: Recorder = { calls: [] };
    const onOpenConversation = vi.fn();
    const conversation = {
      conversation_id: "conv-42",
      title: "Watchlist digest",
      status: "active",
      updated_at: "2026-07-22T00:00:00Z",
      metadata: {},
      latest_run_status: "completed",
    } as unknown as Conversation;

    const ui: ReactElement = (
      <TransportProvider transport={chatsTransport(recorder, [conversation])}>
        <RouterProvider router={fakeRouter()}>
          <ChatsBinder onOpenConversation={onOpenConversation} />
        </RouterProvider>
      </TransportProvider>
    );
    const { container } = render(ui);

    const row = await waitFor(() => {
      const el = container.querySelector("[data-testid='chat-archive-row']");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    fireEvent.click(row);

    expect(onOpenConversation).toHaveBeenCalledTimes(1);
    expect(onOpenConversation).toHaveBeenCalledWith("conv-42");
  });
});
