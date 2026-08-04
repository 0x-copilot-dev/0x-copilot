// @vitest-environment jsdom
//
// The desktop host's half of project filing — the slice that makes the whole
// feature observable. Every `project_id` in this renderer used to be a READ
// filter; nothing wrote one, which is why a project could only ever read
// "0 chats". These assertions are therefore about the WRITES, and about the two
// paths having to exist separately:
//
//   * an existing conversation → `PATCH /v1/agent/conversations/{id}`;
//   * a chat that does not exist yet → the pick is HELD and sent as
//     `project_id` on `POST /v1/agent/conversations`, because neither the run
//     body nor the server's ensure-conversation helper can carry a project.
//     Shipping only the first would fail silently on exactly the flow the
//     design is built around: a fresh chat started inside a project.
//
// Plus the coupling that justifies putting the scope under "New run": the scope
// survives the binder's remount (the outlet keys it by conversation id) and is
// what the created conversation is filed under.
import {
  KeyValueStoreProvider,
  RouterProvider,
  TransportProvider,
  type ArtifactRoute,
  type ConversationId,
  type KeyValueStore,
  type ProjectSummary,
  type Router,
} from "@0x-copilot/chat-surface";
import type { Conversation, ProjectId } from "@0x-copilot/api-types";
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
import {
  currentThreadScope,
  resetProjectFilingState,
  setThreadScope,
} from "./projects/useProjectFiling";

const PROJECT_ID = "proj-acme" as ProjectId;

afterEach(() => {
  cleanup();
  // The scope and the project cache are MODULE state (deliberately — see the
  // module header), so a test that skipped this would seed the next one.
  resetProjectFilingState();
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

function projectSummary(): ProjectSummary {
  return {
    id: PROJECT_ID,
    tenant_id: "t1" as unknown as ProjectSummary["tenant_id"],
    name: "Acme renewal",
    description: "",
    icon_emoji: "📁" as unknown as ProjectSummary["icon_emoji"],
    color_hue: 210 as unknown as ProjectSummary["color_hue"],
    status: "active",
    owner_user_id: "u1" as unknown as ProjectSummary["owner_user_id"],
    viewer_role: null,
    viewer_starred: false,
    counts: {
      chats: 2,
      files: 0,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: 0,
      routines_active: 0,
      members: 1,
    },
    last_activity_at: null,
    updated_at: "2026-08-04T00:00:00Z",
  };
}

// The mount GETs the cockpit + composer make. A provider key keeps the
// readiness gate open (composer enabled → sendable); every list resolves empty
// so the session lands on its empty-state composer.
function payloadFor(path: string): Record<string, unknown> {
  if (path.startsWith("/v1/projects")) return { items: [projectSummary()] };
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

function filingTransport(
  recorder: Recorder,
  overrides: {
    readonly conversationProjectId?: string | null;
    readonly createdConversationId?: string;
    readonly failPatch?: boolean;
  } = {},
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      if (
        req.method === "PATCH" &&
        /\/v1\/agent\/conversations\/.+/.test(req.path)
      ) {
        return overrides.failPatch === true
          ? Promise.reject(new Error("nope"))
          : Promise.resolve({} as unknown as TRes);
      }
      if (req.method === "POST" && req.path === "/v1/agent/conversations") {
        return Promise.resolve({
          conversation_id: overrides.createdConversationId ?? "conv-created",
        } as unknown as TRes);
      }
      if (req.method === "POST" && req.path === "/v1/agent/runs") {
        return Promise.resolve({ run_id: "run-1" } as unknown as TRes);
      }
      // The cockpit's read of the bound conversation — the chip's seed.
      if (
        req.method === "GET" &&
        /\/v1\/agent\/conversations\/[^/?]+$/.test(req.path)
      ) {
        return Promise.resolve({
          conversation_id: "conv-existing",
          project_id: overrides.conversationProjectId ?? null,
        } as unknown as TRes);
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

function chatsListTransport(
  recorder: Recorder,
  conversations: readonly Conversation[],
): Transport {
  const base = filingTransport(recorder);
  return {
    ...base,
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      if (req.method === "GET" && req.path === "/v1/agent/conversations") {
        recorder.calls.push(req);
        const bucket = req.query?.bucket as string | undefined;
        const rows = bucket === "recent" ? conversations : [];
        return Promise.resolve({
          conversations: rows,
          has_more: false,
        } as unknown as TRes);
      }
      return base.request<TRes>(req);
    },
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
    readonly conversationId: ConversationId | null;
    readonly onConversationCreated?: (id: ConversationId) => void;
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

/** The chip's trigger, once the project list has landed. */
async function filingTrigger(container: HTMLElement): Promise<HTMLElement> {
  return await waitFor(() => {
    const el = container.querySelector<HTMLElement>(
      "[data-testid='composer-project-filing-trigger']",
    );
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
}

/** The menu is PORTALED by the desktop anchored popover, so it is not inside
 *  the render container — query the document. */
async function filingOption(projectId: string): Promise<HTMLElement> {
  return await waitFor(() => {
    const el = document.querySelector<HTMLElement>(
      `[data-testid='composer-project-filing-option'][data-project-id='${projectId}']`,
    );
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
}

function patchCalls(recorder: Recorder): TypedRequest[] {
  return recorder.calls.filter((c) => c.method === "PATCH");
}

function conversationPosts(recorder: Recorder): TypedRequest[] {
  return recorder.calls.filter(
    (c) => c.method === "POST" && c.path === "/v1/agent/conversations",
  );
}

function lastRunPost(recorder: Recorder): Record<string, unknown> | null {
  for (let i = recorder.calls.length - 1; i >= 0; i--) {
    const call = recorder.calls[i];
    if (
      call !== undefined &&
      call.method === "POST" &&
      call.path === "/v1/agent/runs"
    ) {
      return (call.body ?? {}) as Record<string, unknown>;
    }
  }
  return null;
}

describe("RunBinder — filing an EXISTING conversation (the PATCH path)", () => {
  it("PATCHes /v1/agent/conversations/{id} with the picked project_id", async () => {
    const recorder: Recorder = { calls: [] };
    const container = renderRunBinder(filingTransport(recorder), {
      conversationId: "conv-existing" as ConversationId,
    });

    fireEvent.click(await filingTrigger(container));
    fireEvent.click(await filingOption(PROJECT_ID));

    await waitFor(() => {
      expect(patchCalls(recorder)).toHaveLength(1);
    });
    const patch = patchCalls(recorder)[0] as TypedRequest;
    expect(patch.path).toBe("/v1/agent/conversations/conv-existing");
    expect(patch.body).toEqual({ project_id: PROJECT_ID });
    // No conversation is created for a chat that already exists.
    expect(conversationPosts(recorder)).toHaveLength(0);
  });

  it("seeds the pill from the conversation's own project_id", async () => {
    const recorder: Recorder = { calls: [] };
    const container = renderRunBinder(
      filingTransport(recorder, { conversationProjectId: PROJECT_ID }),
      { conversationId: "conv-existing" as ConversationId },
    );

    const trigger = await filingTrigger(container);
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "Filed under: Acme renewal",
      );
    });
  });

  it("reverts the pill when the write fails (no silent lie)", async () => {
    const recorder: Recorder = { calls: [] };
    const container = renderRunBinder(
      filingTransport(recorder, { failPatch: true }),
      { conversationId: "conv-existing" as ConversationId },
    );

    const trigger = await filingTrigger(container);
    fireEvent.click(trigger);
    fireEvent.click(await filingOption(PROJECT_ID));

    await waitFor(() => {
      expect(patchCalls(recorder)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "Filed under: no project",
      );
    });
  });
});

describe("RunBinder — filing a NEW chat (the create path)", () => {
  it("creates the conversation WITH project_id, then runs against it", async () => {
    const recorder: Recorder = { calls: [] };
    const onConversationCreated = vi.fn();
    const container = renderRunBinder(filingTransport(recorder), {
      conversationId: null,
      onConversationCreated,
    });

    fireEvent.click(await filingTrigger(container));
    fireEvent.click(await filingOption(PROJECT_ID));

    // Nothing is written before there is a chat to write to.
    expect(patchCalls(recorder)).toHaveLength(0);
    expect(conversationPosts(recorder)).toHaveLength(0);

    const textarea = await waitFor(() => {
      const el = container.querySelector<HTMLTextAreaElement>(
        "[data-testid='composer-textarea']",
      );
      expect(el).not.toBeNull();
      return el as HTMLTextAreaElement;
    });
    fireEvent.change(textarea, { target: { value: "Draft the renewal" } });
    const send = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Send message']",
    );
    if (send === null) throw new Error("composer send button not mounted");
    fireEvent.click(send);

    await waitFor(() => {
      expect(conversationPosts(recorder)).toHaveLength(1);
    });
    const create = conversationPosts(recorder)[0] as TypedRequest;
    const body = (create.body ?? {}) as Record<string, unknown>;
    expect(body.project_id).toBe(PROJECT_ID);
    // The new-chat idempotency key rides the create, so a double-tap still
    // collapses to ONE conversation.
    expect(typeof body.idempotency_key).toBe("string");
    expect((body.idempotency_key as string).length).toBeGreaterThan(0);

    // …and the run binds the conversation that was just created.
    await waitFor(() => {
      expect(lastRunPost(recorder)).not.toBeNull();
    });
    const run = lastRunPost(recorder) as Record<string, unknown>;
    expect(run.conversation_id).toBe("conv-created");
    expect(run).not.toHaveProperty("conversation_idempotency_key");
    await waitFor(() => {
      expect(onConversationCreated).toHaveBeenCalledWith("conv-created");
    });
  });

  it("inherits the thread scope, which survives the binder's remount", async () => {
    const recorder: Recorder = { calls: [] };
    // "New run" remounts this binder (the outlet keys it by conversation id),
    // so the scope has to outlive the component that set it.
    setThreadScope(PROJECT_ID);
    expect(currentThreadScope()).toBe(PROJECT_ID);

    const container = renderRunBinder(filingTransport(recorder), {
      conversationId: null,
    });

    const trigger = await filingTrigger(container);
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "Filed under: Acme renewal",
      );
    });
  });
});

describe("ChatsBinder — Move to project…", () => {
  const conversation: Conversation = {
    conversation_id: "conv-42",
    org_id: "org-1",
    user_id: "user-1",
    assistant_id: "asst-1",
    title: "Watchlist digest",
    status: "active",
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
  };

  it("PATCHes the row's conversation with the picked project and refetches", async () => {
    const recorder: Recorder = { calls: [] };
    const ui: ReactElement = (
      <TransportProvider
        transport={chatsListTransport(recorder, [conversation])}
      >
        <RouterProvider router={fakeRouter()}>
          <ChatsBinder />
        </RouterProvider>
      </TransportProvider>
    );
    const { container } = render(ui);

    const overflow = await waitFor(() => {
      const el = container.querySelector<HTMLButtonElement>(
        "[data-testid='chat-archive-row-overflow-trigger']",
      );
      expect(el).not.toBeNull();
      return el as HTMLButtonElement;
    });
    fireEvent.click(overflow);

    const move = await waitFor(() => {
      const el = container.querySelector<HTMLButtonElement>(
        "[data-testid='chat-archive-row-move-to-project']",
      );
      expect(el).not.toBeNull();
      return el as HTMLButtonElement;
    });
    fireEvent.click(move);

    // The sheet is the host's answer to the row's intent (which names no
    // project) — and it reuses the composer's chip rather than a second list.
    const sheetTrigger = await waitFor(() => {
      const el = document.querySelector<HTMLElement>(
        "[data-testid='desktop-project-filing-sheet'] [data-testid='composer-project-filing-trigger']",
      );
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    fireEvent.click(sheetTrigger);
    fireEvent.click(await filingOption(PROJECT_ID));

    await waitFor(() => {
      expect(patchCalls(recorder)).toHaveLength(1);
    });
    const patch = patchCalls(recorder)[0] as TypedRequest;
    expect(patch.path).toBe("/v1/agent/conversations/conv-42");
    expect(patch.body).toEqual({ project_id: PROJECT_ID });

    // The sheet closes, and the list is refetched so the row cannot keep
    // showing where it used to be filed.
    await waitFor(() => {
      expect(
        document.querySelector("[data-testid='desktop-project-filing-sheet']"),
      ).toBeNull();
    });
    const bucketGets = recorder.calls.filter(
      (c) => c.method === "GET" && c.path === "/v1/agent/conversations",
    );
    // 3 buckets on mount, 3 again after the write.
    expect(bucketGets.length).toBeGreaterThanOrEqual(6);
  });
});
