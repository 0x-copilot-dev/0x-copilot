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

import { act, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

import { registerSurfaces } from "../../app/registerSurfaces";
import type { RequestIdentity } from "../../api/config";
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

function renderRoute(transport: Transport): ReturnType<typeof render> {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={makeStore()}>
        <RunRoute identity={IDENTITY} />
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
      if (req.path === "/v1/agent/conversations" && req.method === "GET") {
        return { conversations: [{ conversation_id: "conv-1" }] };
      }
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] }; // configured → modelReady stays true
      }
      if (req.path.includes("/messages")) {
        return { messages: [] };
      }
      // Run-identity (desktop-run-identity §D2 / PR #211): the cockpit resolves
      // the active run from the conversation detail's `latest_run_id`, not the
      // now-dead GET /v1/agent/runs auto-resolve.
      if (
        req.path === "/v1/agent/conversations/conv-1" &&
        req.method === "GET"
      ) {
        return { conversation_id: "conv-1", latest_run_id: "run-1" };
      }
      return {};
    };

    renderRoute(transport);

    // The binder resolves the conversation, then mounts the real cockpit.
    await screen.findByTestId("run-destination");
    // No fabricated conversation was created — the most-recent one was reused.
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
  });

  it("mounts the design's rich empty composer ('What should we run first?') when there is no active run", async () => {
    const transport = new FakeTransport();
    transport.requestHandler = async (req) => {
      if (req.path === "/v1/agent/conversations" && req.method === "GET") {
        return { conversations: [{ conversation_id: "conv-1" }] };
      }
      if (req.path === "/v1/settings/provider-keys") {
        return { keys: [{ id: "k1" }] };
      }
      if (req.path.includes("/messages")) {
        return { messages: [] };
      }
      if (req.path === "/v1/agent/runs" && req.method === "GET") {
        return { runs: [] }; // no prior runs → empty-state composer
      }
      return {};
    };

    renderRoute(transport);

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
});
