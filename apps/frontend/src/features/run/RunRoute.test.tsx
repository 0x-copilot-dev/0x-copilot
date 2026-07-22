// RunRoute — web Run-cockpit binder tests (PRD-05, AC3).
//
// Renders the real binder against a Transport fake that resolves a conversation,
// reports a configured model, and streams run events. Asserts the flag-ON path:
//   1. RunRoute resolves a conversation and mounts the real `RunDestination`.
//   2. A seeded event array carrying a PRD-01 `payload.surface` envelope renders
//      the Record archetype in the center pane (registerSurfaces() wired it).
//   3. The binder's `onStartRun` glue POSTs a run and binds it live.
//
// Boundary note: this is a test file (`src/features/**/*.test.tsx`), so the
// substrate-boundary eslint rules are off — importing the chat-transport port
// types + registry helpers for setup is intentional and sanctioned.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  KeyValueStoreProvider,
  TransportProvider,
  clearRegistry,
} from "@0x-copilot/chat-surface";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import type { ConversationId } from "@0x-copilot/api-types";

import { registerSurfaces } from "../../app/registerSurfaces";
import type { RequestIdentity } from "../../api/config";
import type { CompletedMcpAuthAction } from "../chat/mcpAuthAction";
import { RunRoute } from "./RunRoute";

// jsdom ships no IntersectionObserver; the rich empty composer's AssistantComposer
// caret path wants one. A no-op keeps the tree renderable.
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

// PRD-01 golden `record` fixture (linear_get_issue), inlined so the test does
// not deep-import package internals (the fixture is not on a public barrel).
const LINEAR_RECORD_SPEC = {
  spec_version: 1,
  archetype: "record",
  source: { server: "seed:linear", tool: "get_issue" },
  title_path: "issue.title",
  subtitle_path: "issue.identifier",
  fields: [
    { label: "State", path: "issue.state.name", format: "badge" },
    { label: "Assignee", path: "issue.assignee.displayName", format: "user" },
    { label: "Priority", path: "issue.priorityLabel" },
  ],
  link: { label: "Open in Linear", url_path: "issue.url" },
} as const;

const LINEAR_RECORD_DATA = {
  issue: {
    title: "Fix login redirect loop",
    identifier: "ENG-1421",
    state: { name: "In Progress" },
    assignee: { displayName: "Sarah Chen" },
    priorityLabel: "High",
    url: "https://linear.app/acme/issue/ENG-1421",
  },
} as const;

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSub {
  readonly path: string;
  readonly eventName?: string;
  readonly onMessage?: (raw: string) => void;
  readonly onError?: (err: Error) => void;
  closed: boolean;
}

class FakeTransport implements Transport {
  requestHandler: (req: TypedRequest) => Promise<unknown> = async () => ({});
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    this.requests.push(req);
    return (await this.requestHandler(req)) as TRes;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = {
      path: opts.path,
      eventName: opts.eventName,
      onMessage: opts.onMessage,
      onError: opts.onError,
      closed: false,
    };
    this.subs.push(sub);
    return {
      close: () => {
        sub.closed = true;
      },
    };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  /** The `useRunSession` tail — the only `runtime_event`-named subscription. */
  get sessionSub(): CapturedSub | undefined {
    return [...this.subs]
      .reverse()
      .find((sub) => !sub.closed && sub.eventName === "runtime_event");
  }
}

function makeStore() {
  const map = new Map<string, string>();
  return {
    get: (key: string) => map.get(key) ?? null,
    set: (key: string, value: string | null) => {
      if (value === null) map.delete(key);
      else map.set(key, value);
    },
    keys: (prefix?: string) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

function renderRoute(
  transport: Transport,
  conversationId?: string,
  onConversationCreated?: (id: ConversationId) => void,
  completedMcpAuthAction?: CompletedMcpAuthAction | null,
): ReturnType<typeof render> {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={makeStore()}>
        <RunRoute
          identity={IDENTITY}
          conversationId={
            conversationId === undefined
              ? null
              : (conversationId as ConversationId)
          }
          onConversationCreated={onConversationCreated}
          completedMcpAuthAction={completedMcpAuthAction}
        />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
}

/** A `tool_result` carrying the PRD-01 `payload.surface` record envelope. */
function recordSurfaceEvent(uri: string): Record<string, unknown> {
  return {
    event_id: "surf-1",
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: 1,
    event_type: "tool_result",
    activity_kind: "tool",
    payload: {
      surface: {
        surface_uri: uri,
        archetype: "record",
        state: { spec: LINEAR_RECORD_SPEC, data: LINEAR_RECORD_DATA },
      },
    },
    created_at: "2026-07-20T10:00:00.000Z",
  };
}

beforeEach(() => {
  registerSurfaces();
});

afterEach(() => {
  clearRegistry();
});

describe("RunRoute (PRD-05)", () => {
  it("mounts RunDestination and renders a PRD-01 record envelope as the Record archetype", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] }; // configured → modelReady stays true
      }
      if (req.path.includes("/messages")) {
        return { messages: [] };
      }
      // Runs-list (multi-run selector): conv-1 has the running run.
      if (req.path.endsWith("/conversations/conv-1/runs")) {
        return {
          runs: [
            {
              run_id: "run-1",
              status: "running",
              model_name: "gpt-5.4",
              created_at: "2026-07-20T10:00:00.000Z",
            },
          ],
        };
      }
      // The conversation head resolves the active run → the cockpit binds it.
      if (req.path.endsWith("/conversations/conv-1")) {
        return { latest_run_id: "run-1" };
      }
      return {};
    };

    renderRoute(transport, "conv-1");

    // The binder mounts the real cockpit for the reopened conversation.
    await screen.findByTestId("run-destination");
    // No conversation was fabricated on mount (the racy self-create is gone).
    expect(
      transport.requests.some(
        (r) => r.method === "POST" && r.path === "/v1/agent/conversations",
      ),
    ).toBe(false);

    // The resolved run binds the session's SSE tail; stream a surface envelope.
    await waitFor(() => expect(transport.sessionSub).toBeDefined());
    act(() => {
      transport.sessionSub?.onMessage?.(
        JSON.stringify(recordSurfaceEvent("record://seed/linear/get_issue/1")),
      );
    });

    // The center pane resolves the `record` archetype adapter (registerSurfaces
    // wired it) and renders the spec-driven record, not the tier-3 fallback.
    const record = await screen.findByTestId("record-renderer");
    expect(record.getAttribute("data-spec")).toBe("present");
    // Scope to the surface: the title also appears in the surface-tab strip.
    expect(
      within(record).getByText("Fix login redirect loop"),
    ).toBeInTheDocument();
    expect(within(record).getByText("In Progress")).toBeInTheDocument();

    // WC-P1 keystone: the reopened cockpit now fills the `renderComposer` slot,
    // so a SECOND (turn-N) in-chat message has a live composer + send path — the
    // web bug where a 2nd message was inert is closed. Before P1 the cockpit
    // mounted only the empty composer and this element did not exist.
    expect(await screen.findByTestId("run-composer")).toBeInTheDocument();
  });

  it("mounts the design's rich empty composer ('What should we run first?') when there is no active run", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] };
      }
      if (req.path.includes("/messages")) {
        return { messages: [] };
      }
      if (req.path.endsWith("/runs")) {
        return { runs: [] }; // no prior runs → empty-state composer
      }
      return {}; // head GET → no latest_run_id → no active run
    };

    renderRoute(transport, "conv-1");

    // No active run → the design's rich composer (hero + starter chips), NOT the
    // plain "Give it a goal" card and NOT a blank canvas.
    const hero = await screen.findByTestId("first-run-composer-h1");
    expect(hero.textContent).toBe("What should we run first?");
    expect(screen.getByTestId("first-run-chip-watch-wallet")).not.toBeNull();
    expect(screen.queryByTestId("run-empty-goal-input")).toBeNull();
    expect(screen.queryByTestId("thread-canvas")).toBeNull();

    // The most-recent conversation was reused — no fabricated one was created.
    expect(
      transport.requests.some(
        (r) => r.method === "POST" && r.path === "/v1/agent/conversations",
      ),
    ).toBe(false);
  });

  // WC-P2 (AD-10): a new chat has no conversation until the first send mints one
  // (ensure-conversation-on-run). The host must be told the created id so it can
  // promote the URL from `/` to `/run/<id>` (reopen / refresh / share target it).
  it("calls onConversationCreated when a new chat's first send mints a conversation", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] }; // configured → modelReady stays true
      }
      if (req.path === "/v1/agent/models") {
        return {
          models: [
            {
              id: "gpt-5.4",
              provider: "openai",
              model_name: "gpt-5.4",
              name: "GPT-5.4",
              configured: true,
              supports_streaming: true,
            },
          ],
        };
      }
      if (req.path.includes("/messages")) return { messages: [] };
      // Ensure-conversation-on-run: the first send POSTs a run with NO
      // conversation_id + an idempotency key; the server mints + returns both.
      // (Checked BEFORE the runs-list GET — both paths end with "/runs".)
      if (req.method === "POST" && req.path === "/v1/agent/runs") {
        return { run_id: "run-new", conversation_id: "conv-new" };
      }
      if (req.path.endsWith("/runs")) return { runs: [] };
      return {}; // head GET → no active run → empty composer
    };
    const onConversationCreated = vi.fn();

    // New chat — no conversationId prop (the `/` entry, not `/run/<id>`).
    const { container } = renderRoute(
      transport,
      undefined,
      onConversationCreated,
    );

    await screen.findByTestId("first-run-composer-h1");
    const input = container.querySelector<HTMLTextAreaElement>(
      "[data-testid='composer-textarea']",
    );
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLTextAreaElement, {
      target: { value: "Ship the renewal batch" },
    });
    fireEvent.click(
      container.querySelector(
        "button[aria-label='Send message']",
      ) as HTMLButtonElement,
    );

    // The first send POSTed a run with no conversation_id + an idempotency key…
    await waitFor(() => {
      const post = transport.requests.find(
        (r) => r.method === "POST" && r.path === "/v1/agent/runs",
      );
      expect(post).toBeDefined();
      const body = post?.body as Record<string, unknown> | undefined;
      expect(body?.conversation_id).toBeUndefined();
      expect(body?.conversation_idempotency_key).toBeDefined();
    });
    // …and the host was notified with the created id so App can promote the URL.
    await waitFor(() =>
      expect(onConversationCreated).toHaveBeenCalledWith("conv-new"),
    );
  });

  // WC-P5b (AD-8): mid-run MCP-OAuth resume. App navigates back to the run root
  // (dropping the conversation from the URL) and mints a `completedMcpAuthAction`
  // carrying the run id ONLY. The binder maps run→conversation via
  // `GET /v1/agent/runs/{run_id}` and re-opens that conversation so the cockpit
  // rebinds; `useRunSession` then self-resumes the stream from its cursor.
  it("resolves run→conversation from a completedMcpAuthAction and opens that conversation", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] };
      }
      // The resume GET: run id → its conversation. (`/runs/run-oauth` does not
      // end with `/runs`, so it never collides with the runs-list branch.)
      if (req.method === "GET" && req.path === "/v1/agent/runs/run-oauth") {
        return { conversation_id: "conv-resumed", status: "running" };
      }
      if (req.path.includes("/messages")) return { messages: [] };
      // POST-run guard BEFORE any endsWith("/runs") GET branch (both end "/runs").
      if (req.method === "POST" && req.path === "/v1/agent/runs") return {};
      if (req.path.endsWith("/runs")) return { runs: [] };
      return {}; // head GET → no active run for the "new" mount
    };
    const onConversationCreated = vi.fn();
    const completed: CompletedMcpAuthAction = {
      approvalId: "mcp_auth:run-oauth:seed:linear",
      serverId: "seed:linear",
      runId: "run-oauth",
      createdAt: "2026-07-22T10:00:00.000Z",
      completedAt: "2026-07-22T10:01:00.000Z",
    };

    // Mount at the run root (no conversationId), exactly as App does on OAuth return.
    renderRoute(transport, undefined, onConversationCreated, completed);

    // The binder resolved run→conversation and re-opened the thread so the
    // cockpit rebinds (URL promotes to /run/conv-resumed).
    await waitFor(() =>
      expect(onConversationCreated).toHaveBeenCalledWith("conv-resumed"),
    );
    expect(
      transport.requests.some(
        (r) => r.method === "GET" && r.path === "/v1/agent/runs/run-oauth",
      ),
    ).toBe(true);
    // Resume NEVER POSTs a `/decision` for an `mcp_auth` gate (AD-7).
    expect(transport.requests.some((r) => r.path.includes("/approvals/"))).toBe(
      false,
    );
  });

  // R2 degrade: the run terminated during the redirect (or its row was lost to a
  // backend restart). Resolving still yields a conversation → land on it (the
  // completed transcript), never a hung stream.
  it("degrades to landing on the conversation when the resumed run has terminated", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] };
      }
      if (req.method === "GET" && req.path === "/v1/agent/runs/run-done") {
        // Terminal status, but still a resolvable conversation.
        return { conversation_id: "conv-done", status: "completed" };
      }
      if (req.path.includes("/messages")) return { messages: [] };
      if (req.method === "POST" && req.path === "/v1/agent/runs") return {};
      if (req.path.endsWith("/runs")) return { runs: [] };
      return {};
    };
    const onConversationCreated = vi.fn();
    const completed: CompletedMcpAuthAction = {
      approvalId: "mcp_auth:run-done:seed:linear",
      serverId: "seed:linear",
      runId: "run-done",
      createdAt: "2026-07-22T10:00:00.000Z",
      completedAt: "2026-07-22T10:01:00.000Z",
    };

    renderRoute(transport, undefined, onConversationCreated, completed);

    // Still lands on the conversation (transcript), no throw.
    await waitFor(() =>
      expect(onConversationCreated).toHaveBeenCalledWith("conv-done"),
    );
    expect(await screen.findByTestId("run-route")).toBeInTheDocument();
  });

  // R2 degrade: the run/approval row was fully lost (GET 404s). No conversation
  // to resolve → the resume swallows the error, opens nothing, and the cockpit
  // stays rendered — never a throw, never a hung stream.
  it("degrades without throwing when the resumed run cannot be resolved", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] };
      }
      if (req.method === "GET" && req.path === "/v1/agent/runs/run-lost") {
        throw new Error("404 run not found");
      }
      if (req.path.includes("/messages")) return { messages: [] };
      if (req.method === "POST" && req.path === "/v1/agent/runs") return {};
      if (req.path.endsWith("/runs")) return { runs: [] };
      return {};
    };
    const onConversationCreated = vi.fn();
    const completed: CompletedMcpAuthAction = {
      approvalId: "mcp_auth:run-lost:seed:linear",
      serverId: "seed:linear",
      runId: "run-lost",
      createdAt: "2026-07-22T10:00:00.000Z",
      completedAt: "2026-07-22T10:01:00.000Z",
    };

    renderRoute(transport, undefined, onConversationCreated, completed);

    // The failing resolve was attempted…
    await waitFor(() =>
      expect(
        transport.requests.some(
          (r) => r.method === "GET" && r.path === "/v1/agent/runs/run-lost",
        ),
      ).toBe(true),
    );
    // …but no conversation was opened, and the cockpit is still rendered.
    expect(onConversationCreated).not.toHaveBeenCalled();
    expect(screen.getByTestId("run-route")).toBeInTheDocument();
  });
});
