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
  ProjectId,
  ProjectSummary,
  RunHistoryEntry,
  RunHistoryResponse,
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

import {
  ActivityBinder,
  ChatsBinder,
  ConnectorsBinder,
  ProjectsBinder,
  RunBinder,
  createDesktopProjectDataPort,
} from "./destinationBinders";

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
  // PRD-09 — `useChatsArchive` fetches each bucket server-side, so the fake
  // classifies conversations into the bucket the request asks for (archived
  // wins, then pinned, else recent), matching the real query scoping.
  const bucketOf = (c: Conversation): string => {
    if (c.status === "archived" || c.archived_at != null) return "archived";
    if (c.pinned === true) return "pinned";
    return "recent";
  };
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      const bucket = req.query?.bucket as string | undefined;
      const scoped =
        bucket === undefined
          ? [...conversations]
          : conversations.filter((c) => bucketOf(c) === bucket);
      const body: ConversationListResponse = {
        conversations: scoped,
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
    /** Pass one in to assert on what the cockpit persisted. */
    store?: KeyValueStore;
  },
): HTMLElement {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={props.store ?? fakeKeyValueStore()}>
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

// ===========================================================================
// createDesktopProjectDataPort — PRD-07 DoD 15.
//
// The desktop `ProjectDataPort.listProjectChats("p1")` must issue exactly ONE
// Transport request whose PATH carries `filter[project_id]=p1` (so the facade's
// alias survives → ai-backend's `project_id`), and the returned row must carry
// `model` and `status` — proving it is mapped by the SHARED `toChatArchiveRow`
// (PRD-03), not a local projection that a `ProjectActivityRecord` could never
// feed. Desktop REACHABILITY of the detail view (focusedProjectId + renderDetail)
// is PRD-10 DoD 9, not this PRD — this test exercises the port directly.
// ===========================================================================

// ===========================================================================
// Studio rail fold on DESKTOP.
//
// The fold lives in `RunDestination`/`ThreadCanvas`, which both hosts mount —
// but "the shared package has it" is not evidence that the desktop binder,
// with the desktop's own providers and its own KeyValueStore port, actually
// renders and persists it. This drives the real desktop mount path.
// ===========================================================================

describe("RunBinder — Studio rail fold (desktop mount path)", () => {
  async function mountBoundCockpit(store: KeyValueStore): Promise<HTMLElement> {
    const recorder: Recorder = { calls: [] };
    const container = renderRunBinder(
      runTransport(recorder, { run_id: "run-fold" }),
      { conversationId: "conv-fold" as ConversationId, store },
    );
    // The canvas (and therefore the rail) mounts once a run is bound.
    await typeAndSend(container, "Draft the renewal email");
    await waitFor(() => {
      expect(
        container.querySelector("[data-testid='run-workspace-rail']"),
      ).not.toBeNull();
    });
    return container;
  }

  function chevron(container: HTMLElement): HTMLButtonElement {
    const el = container.querySelector<HTMLButtonElement>(
      "[data-testid='run-rail-collapse']",
    );
    if (el === null) throw new Error("collapse chevron not mounted on desktop");
    return el;
  }

  it("folds the rail to its icon strip and hands the width to the surface", async () => {
    const store = fakeKeyValueStore();
    const container = await mountBoundCockpit(store);

    const canvas = container.querySelector<HTMLElement>(
      "[data-testid='thread-canvas']",
    );
    if (canvas === null) throw new Error("canvas not mounted");
    expect(canvas.style.gridTemplateColumns).not.toContain("46px");

    fireEvent.click(chevron(container));

    expect(canvas).toHaveAttribute("data-rail-collapsed", "true");
    expect(canvas.style.gridTemplateColumns).toContain("46px");
    expect(
      container.querySelector("[data-testid='run-rail-strip']"),
    ).not.toBeNull();
    // Folded, there is nothing to drag.
    expect(
      container.querySelector("[data-testid='tc-rail-resizer']"),
    ).toBeNull();
  });

  it("persists the fold through the desktop KeyValueStore port", async () => {
    const store = fakeKeyValueStore();
    const container = await mountBoundCockpit(store);
    fireEvent.click(chevron(container));

    const key = store
      .keys("chats.thread.")
      .find((k) => k.endsWith(".run_studio_rail_collapsed"));
    expect(key).toBeDefined();
    expect(store.get(key as string)).toBe("1");
  });

  // Folding must not cost the composer its draft — the chat panel is hidden,
  // never unmounted.
  it("keeps the composer alive (with its draft) across a fold", async () => {
    const store = fakeKeyValueStore();
    const container = await mountBoundCockpit(store);
    const ta = textarea(container) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "half-written thought" } });

    fireEvent.click(chevron(container));
    expect(textarea(container)).toBe(ta);
    expect((textarea(container) as HTMLTextAreaElement).value).toBe(
      "half-written thought",
    );

    const expand = container.querySelector<HTMLButtonElement>(
      "[data-testid='run-rail-expand']",
    );
    if (expand === null) throw new Error("expand chevron not mounted");
    fireEvent.click(expand);
    expect((textarea(container) as HTMLTextAreaElement).value).toBe(
      "half-written thought",
    );
  });
});

describe("createDesktopProjectDataPort — project-scoped chats (PRD-07 DoD 15)", () => {
  it("listProjectChats issues one filter[project_id]=<id> request and maps rows via toChatArchiveRow", async () => {
    const recorder: Recorder = { calls: [] };
    const conversation: Conversation = {
      conversation_id: "conv-p1",
      org_id: "org-1",
      user_id: "user-1",
      assistant_id: "asst-1",
      title: "Filed chat",
      status: "active",
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
      archived_at: null,
      metadata: {},
      schema_version: 1,
      latest_run_status: "running",
      model: "claude-sonnet-4.5",
      project_id: "p1",
    };
    const port = createDesktopProjectDataPort(
      chatsTransport(recorder, [conversation]),
    );

    const result = await port.listProjectChats("p1" as ProjectId);

    // Exactly one Transport request, whose PATH carries the project filter axis
    // (the facade reads `filter[project_id]` and translates to `project_id`).
    expect(recorder.calls).toHaveLength(1);
    expect(recorder.calls[0]!.method).toBe("GET");
    expect(recorder.calls[0]!.path).toContain("filter[project_id]=p1");
    expect(recorder.calls[0]!.path).toContain("include_archived=true");

    // The row is mapped by the SHARED `toChatArchiveRow`: it carries `model`
    // (mono tag) and `status` (the archive chip taxonomy) — the two fields a
    // local activity-record projection could not supply.
    expect(result.status).toBe("ok");
    const rows = result.data ?? [];
    expect(rows).toHaveLength(1);
    expect(rows[0]!.model).toBe("claude-sonnet-4.5");
    expect(rows[0]!.status).toBe("running");
  });
});

// ===========================================================================
// ProjectsBinder — desktop project-detail REACHABILITY (PRD-10 DoD 9).
//
// This is the regression guard for the desktop-detail bug: before PRD-10 the
// binder passed `detail={{ mode: "disabled" }}`, so clicking a project card did
// NOTHING and the whole `ProjectDetailView` (plus PRD-07's ProjectDataPort data)
// was dead code on desktop. The binder now owns the focus state and mounts the
// shared detail through `renderDetail`; clicking a card must reveal the detail.
//
// NOTE: run from `apps/desktop`, `@0x-copilot/chat-surface` resolves to the ROOT
// checkout (which lacks PRD-10's `onOpenProject`-on-whole-card + `onBack`), so
// this test is proven against the worktree package via the aliased run recorded
// in the PR notes; the orchestrator's post-merge repoint re-verifies it here.
// ===========================================================================

function projectSummary(over: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: "p1" as ProjectId,
    tenant_id: "t1" as unknown as ProjectSummary["tenant_id"],
    name: "Renewal Q4",
    description: "Push the renewal across the line.",
    icon_emoji: "📁" as unknown as ProjectSummary["icon_emoji"],
    color_hue: 210 as unknown as ProjectSummary["color_hue"],
    status: "active",
    owner_user_id: "u1" as unknown as ProjectSummary["owner_user_id"],
    viewer_role: null,
    viewer_starred: false,
    counts: {
      chats: 0,
      files: 0,
      todos_open: 0,
      todos_done: 0,
      inbox_items: 0,
      library_items: 0,
      routines_active: 0,
      members: 1,
    },
    last_activity_at: null,
    updated_at: "2026-07-22T00:00:00Z",
    ...over,
  };
}

function projectsTransport(
  recorder: Recorder,
  projects: readonly ProjectSummary[],
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      if (req.path.startsWith("/v1/projects") && req.method === "GET") {
        return Promise.resolve({ items: projects } as unknown as TRes);
      }
      if (req.path.includes("/v1/agent/conversations")) {
        return Promise.resolve({ conversations: [] } as unknown as TRes);
      }
      if (req.path.includes("/v1/library")) {
        return Promise.resolve({ items: [] } as unknown as TRes);
      }
      return Promise.resolve({} as unknown as TRes);
    },
    subscribeServerSentEvents: (): SseSubscription => ({
      close: () => undefined,
    }),
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

describe("ProjectsBinder — desktop project-detail reachability (PRD-10 DoD 9)", () => {
  it("clicking a project card reveals the detail view with the project name", async () => {
    const recorder: Recorder = { calls: [] };
    const ui: ReactElement = (
      <TransportProvider
        transport={projectsTransport(recorder, [projectSummary()])}
      >
        <RouterProvider router={fakeRouter()}>
          <ProjectsBinder />
        </RouterProvider>
      </TransportProvider>
    );
    const { container } = render(ui);

    const card = await waitFor(() => {
      const el = container.querySelector("[data-testid='project-card']");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    // Clicking the card focuses the project → the binder mounts the detail.
    fireEvent.click(card);

    await waitFor(() => {
      const name = container.querySelector(
        "[data-testid='project-detail-name']",
      );
      expect(name).not.toBeNull();
      expect(name?.textContent).toBe("Renewal Q4");
    });
  });
});

describe("ChatsBinder — reopen threads the real conversation id", () => {
  it("invokes onOpenConversation with the row's conversation id on reopen", async () => {
    const recorder: Recorder = { calls: [] };
    const onOpenConversation = vi.fn();
    // A fully-typed Conversation (no `as unknown as` escape hatch — that double
    // assertion silently defeated the PRD-05 `latest_run_status` narrowing).
    // `latest_run_status` carries an emittable non-terminal status.
    const conversation: Conversation = {
      conversation_id: "conv-42",
      org_id: "org-1",
      user_id: "user-1",
      assistant_id: "asst-1",
      title: "Watchlist digest",
      status: "active",
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
      archived_at: null,
      metadata: {},
      schema_version: 1,
      latest_run_status: "running",
    };

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

describe("ChatsBinder — reads first-class preview/model/pinned (PRD-03 Move 1)", () => {
  it("renders the first-class fields (not metadata) and buckets a pinned row", async () => {
    const recorder: Recorder = { calls: [] };
    // First-class fields carry the real values; a stale `metadata` blob carries
    // contradictory ones. Nothing writes `metadata.*`, so the first-class
    // fields must win — the exact drift the old local `toArchiveRow` shipped.
    const conversation: Conversation = {
      conversation_id: "conv-pin",
      org_id: "org-1",
      user_id: "user-1",
      assistant_id: "asst-1",
      title: "Pinned digest",
      status: "active",
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
      archived_at: null,
      metadata: { preview: "WRONG", model: "WRONG", pinned: false },
      schema_version: 1,
      pinned: true,
      preview: "hello",
      model: "claude-sonnet-4.5",
    };

    const ui: ReactElement = (
      <TransportProvider transport={chatsTransport(recorder, [conversation])}>
        <RouterProvider router={fakeRouter()}>
          <ChatsBinder />
        </RouterProvider>
      </TransportProvider>
    );
    const { container } = render(ui);

    const preview = await waitFor(() => {
      const el = container.querySelector(
        "[data-testid='chat-archive-row-preview']",
      );
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(preview.textContent).toBe("hello");
    expect(
      container.querySelector("[data-testid='chat-archive-row-model']")
        ?.textContent,
    ).toBe("claude-sonnet-4.5");
    // The contradictory metadata values never surface.
    expect(container.textContent ?? "").not.toContain("WRONG");
    // `pinned: true` (first-class) buckets the row into the Pinned section —
    // on `main` desktop read the stale metadata blob, so Pinned was always empty.
    const pinnedList = container.querySelector(
      "[data-testid='chats-section-pinned-list']",
    );
    expect(
      pinnedList?.querySelector("[data-testid='chat-archive-row']"),
    ).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// PRD-06 DoD 15 — regression guard for the "Off everywhere" bug on desktop.
// The binder must (a) render each connector's REAL access mode (not a blanket
// "off"), and (b) persist a change via PATCH /v1/connectors/{id}/access-mode
// through the shared `ConnectorAccessPort` seam.
// ---------------------------------------------------------------------------

function connectorsTransport(recorder: Recorder): Transport {
  const connectors = [
    {
      id: "conn_gmail",
      tenant_id: "tnt_1",
      slug: "gmail",
      display_name: "Gmail",
      description: "",
      status: "connected",
      access_mode: "read",
      owner_user_id: "user_1",
      scopes: [],
      last_sync_at: null,
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
    },
    {
      id: "conn_slack",
      tenant_id: "tnt_1",
      slug: "slack",
      display_name: "Slack",
      description: "",
      status: "connected",
      access_mode: "read_act",
      owner_user_id: "user_1",
      scopes: [],
      last_sync_at: null,
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
    },
  ];
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      recorder.calls.push(req);
      if (req.method === "GET" && req.path === "/v1/connectors") {
        return Promise.resolve({
          connectors,
          available: [],
        } as unknown as TRes);
      }
      if (
        req.method === "PATCH" &&
        req.path.startsWith("/v1/connectors/") &&
        req.path.endsWith("/access-mode")
      ) {
        const body = req.body as { access_mode: string };
        return Promise.resolve({
          connector: { ...connectors[0], access_mode: body.access_mode },
        } as unknown as TRes);
      }
      return Promise.resolve({} as TRes);
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

describe("ConnectorsBinder — access mode reflects real authority + persists", () => {
  it("renders each connector's real mode (not all off) and PATCHes on change", async () => {
    const recorder: Recorder = { calls: [] };
    const { container, getAllByTestId, getByRole } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    // Both segments render their REAL mode — the bug painted every row "off".
    const segments = await waitFor(() => {
      const found = getAllByTestId("access-mode-segment");
      expect(found).toHaveLength(2);
      return found;
    });
    const values = segments.map((s) => s.getAttribute("data-value")).sort();
    expect(values).toEqual(["read", "read_act"]);
    expect(values).not.toEqual(["off", "off"]);

    // Click a third option on the Gmail (read) segment → issues the PATCH.
    const gmail = getByRole("radiogroup", { name: "Access mode for Gmail" });
    fireEvent.click(
      gmail.querySelector(
        "[data-testid='access-mode-option-read_act']",
      ) as HTMLElement,
    );

    await waitFor(() => {
      const patch = recorder.calls.find(
        (c) =>
          c.method === "PATCH" &&
          c.path === "/v1/connectors/conn_gmail/access-mode",
      );
      expect(patch).toBeDefined();
      expect(patch?.body).toEqual({ access_mode: "read_act" });
    });
    expect(container).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// PRD-11 DoD 8 — desktop mounts the SAME ConnectModal. This is the regression
// guard that fails on `main`: the old binder mounted no modal (its CTA flipped
// a filter tab). (a) the CTA opens the modal; (b) the pinned escape-hatch row
// opens the MCP config editor and round-trips the document.
// ---------------------------------------------------------------------------

describe("ConnectorsBinder — connect flow (PRD-11 D4)", () => {
  it("opens the MCP config editor from the 'Manage MCP' CTA", async () => {
    const recorder: Recorder = { calls: [] };
    const { getByTestId, queryByTestId } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("connectors-connect-cta")).toBeInTheDocument();
    });
    // No modal until the CTA is pressed.
    expect(queryByTestId("settings-modal")).toBeNull();

    fireEvent.click(getByTestId("connectors-connect-cta"));

    // Straight to the document — no catalog step in between. The catalog is
    // still on the surface itself ("Add a connector" rows), so this CTA is
    // free to be the one thing that surface could not otherwise do.
    await waitFor(() => {
      expect(getByTestId("manage-mcp-editor")).toBeInTheDocument();
    });
    expect(getByTestId("settings-modal").querySelector("h2")?.textContent).toBe(
      "Manage MCP",
    );
  });

  // "Manage MCP" is the whole configuration as one editable document, which
  // replaced the single Server-URL form: that form could express exactly one
  // kind of server (remote, no credential, no headers) while the control
  // opening it advertised "paste a JSON config — stdio or remote", so a user
  // following its own description had nowhere to put a header or a command.
  it("the CTA loads the config document from the server", async () => {
    const recorder: Recorder = { calls: [] };
    const { getByTestId } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("connectors-connect-cta")).toBeInTheDocument();
    });
    fireEvent.click(getByTestId("connectors-connect-cta"));

    await waitFor(() => {
      expect(getByTestId("manage-mcp-editor")).toBeInTheDocument();
    });
    // The editor is seeded from the server, not from local state — otherwise
    // it would happily save a document that omitted every server it failed to
    // load, deleting them.
    const reads = recorder.calls.filter(
      (c) => c.method === "GET" && c.path === "/v1/mcp/config",
    );
    expect(reads).toHaveLength(1);
  });

  it("saving the editor PUTs the document exactly once", async () => {
    const recorder: Recorder = { calls: [] };
    const { getByTestId, getByRole } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("connectors-connect-cta")).toBeInTheDocument();
    });
    fireEvent.click(getByTestId("connectors-connect-cta"));
    await waitFor(() => {
      expect(getByTestId("manage-mcp-editor")).toBeInTheDocument();
    });

    fireEvent.change(getByTestId("manage-mcp-editor"), {
      target: {
        value: JSON.stringify({
          servers: {
            github: {
              type: "http",
              url: "https://api.githubcopilot.com/mcp/",
            },
          },
        }),
      },
    });
    fireEvent.click(getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const writes = recorder.calls.filter(
        (c) => c.method === "PUT" && c.path === "/v1/mcp/config",
      );
      expect(writes).toHaveLength(1);
    });
  });
});

// ---------------------------------------------------------------------------
// Remove — the row's Remove asks first, then removes over the ONE verb
// `DELETE /v1/connectors/{id}`. The binder used to list /v1/mcp/servers and
// guess which server backed the row; that guess failed outright once the
// server was gone, and deleting the server never removed the row anyway.
// ---------------------------------------------------------------------------

describe("ConnectorsBinder — remove", () => {
  it("confirms first, then DELETEs the connector (never /v1/mcp/servers)", async () => {
    const recorder: Recorder = { calls: [] };
    const { getAllByTestId, getByTestId } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    await waitFor(() => {
      expect(getAllByTestId("connector-remove")).toHaveLength(2);
    });

    fireEvent.click(getAllByTestId("connector-remove")[0]);
    // The first click asks; nothing has been deleted.
    expect(recorder.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    expect(getByTestId("settings-modal")).toBeInTheDocument();

    fireEvent.click(getByTestId("connector-remove-confirm"));

    await waitFor(() => {
      const deletes = recorder.calls.filter((c) => c.method === "DELETE");
      expect(deletes).toHaveLength(1);
      expect(deletes[0].path).toBe("/v1/connectors/conn_gmail");
    });
    expect(
      recorder.calls.some((c) => c.path.startsWith("/v1/mcp/servers")),
    ).toBe(false);
  });

  it("cancelling removes nothing", async () => {
    const recorder: Recorder = { calls: [] };
    const { getAllByTestId, getByTestId, queryByTestId } = render(
      <TransportProvider transport={connectorsTransport(recorder)}>
        <ConnectorsBinder />
      </TransportProvider>,
    );

    await waitFor(() => {
      expect(getAllByTestId("connector-remove")).toHaveLength(2);
    });

    fireEvent.click(getAllByTestId("connector-remove")[1]);
    fireEvent.click(getByTestId("connector-remove-cancel"));

    expect(queryByTestId("settings-modal")).toBeNull();
    expect(recorder.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });
});

// ===========================================================================
// ActivityBinder — PRD-08 D1/D1c: reads GET /v1/agent/runs (never /v1/audit),
// renders the counter meta line, and (PRD-04 Seam C) forwards the row's
// { conversationId, runId } to `onOpenRun`.
// ===========================================================================

function runEntry(over: Partial<RunHistoryEntry> = {}): RunHistoryEntry {
  return {
    run_id: "run_row",
    conversation_id: "conv_row",
    conversation_title: "Weekly treasury reconciliation",
    status: "running",
    model_name: "gpt-4o",
    created_at: "2026-07-18T08:00:00Z",
    started_at: "2026-07-18T09:00:00Z",
    completed_at: null,
    cancelled_at: null,
    connector_count: null,
    step_count: null,
    pending_approval_count: 0,
    ...over,
  };
}

// Records every request path so the test can assert the audit stream is never
// touched (DoD 12 — the swallowed-403 regression guard).
function activityTransport(
  entries: readonly RunHistoryEntry[],
  seenPaths: string[],
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> => {
      seenPaths.push(req.path);
      if (req.path.includes("/v1/agent/runs")) {
        const body: RunHistoryResponse = {
          runs: [...entries],
          next_cursor: null,
          has_more: false,
        };
        return Promise.resolve(body as unknown as TRes);
      }
      return Promise.resolve({} as unknown as TRes);
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

describe("ActivityBinder — reads the run-history spine (PRD-08 D1/D1c)", () => {
  it("activating a row forwards { conversationId, runId } and NEVER calls /v1/audit (DoD 12)", async () => {
    const onOpenRun = vi.fn();
    const seenPaths: string[] = [];
    const { container } = render(
      <TransportProvider transport={activityTransport([runEntry()], seenPaths)}>
        <RouterProvider router={fakeRouter()}>
          <ActivityBinder onOpenRun={onOpenRun} />
        </RouterProvider>
      </TransportProvider>,
    );

    let row: HTMLElement | null = null;
    await waitFor(() => {
      row = container.querySelector<HTMLElement>(
        "[data-testid='activity-row']",
      );
      expect(row).not.toBeNull();
    });

    fireEvent.click(row as unknown as HTMLElement);
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun).toHaveBeenCalledWith({
      conversationId: "conv_row",
      runId: "run_row",
    });

    // One request, to the run-history spine; the audit stream is never read.
    expect(seenPaths.some((p) => p.includes("/v1/agent/runs"))).toBe(true);
    expect(seenPaths.some((p) => p.includes("/v1/audit"))).toBe(false);
  });

  // DoD 13 — one composer, two hosts: identical fixture → identical meta line.
  it("renders the meta sub-line '4 apps · 7 steps · awaiting 1 approval' from the counters (DoD 13)", async () => {
    const seenPaths: string[] = [];
    const { container } = render(
      <TransportProvider
        transport={activityTransport(
          [
            runEntry({
              run_id: "run_meta",
              conversation_id: "conv_meta",
              conversation_title: "Launch Week ops",
              status: "running",
              connector_count: 4,
              step_count: 7,
              pending_approval_count: 1,
            }),
          ],
          seenPaths,
        )}
      >
        <RouterProvider router={fakeRouter()}>
          <ActivityBinder onOpenRun={vi.fn()} />
        </RouterProvider>
      </TransportProvider>,
    );

    let metaEl: HTMLElement | null = null;
    await waitFor(() => {
      metaEl = container.querySelector<HTMLElement>(
        "[data-testid='activity-row-meta']",
      );
      expect(metaEl).not.toBeNull();
    });
    expect((metaEl as unknown as HTMLElement).textContent).toBe(
      "4 apps · 7 steps · awaiting 1 approval",
    );
  });
});
