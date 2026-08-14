// The revert affordance, driven end to end through the mounted cockpit.
//
// WHAT THIS TEST IS FOR. `GET /v1/agent/runs/{run_id}/host-writes` and
// `POST …/host-writes/revert` were backend-complete, facade-proxied, and called
// by NOTHING — the failure mode this program keeps hitting is a feature that is
// landed but unreachable, which no unit test of the projection can detect. So
// this drives the real `RunDestination` against a real `Transport`, finds the
// affordance the way a person would (a rail tab, then a row, then a control),
// and asserts the POST that leaves the client.
//
// Every assertion below fails without the wiring: no Changes tab exists, so the
// first `findByRole("tab", { name: /Changes/ })` times out.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ConversationId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { TransportHttpError } from "@0x-copilot/chat-transport";
import { describe, expect, it } from "vitest";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";

import { RunDestination } from "./RunDestination";

const CONV = "conv-1" as ConversationId;
const RUN = "run-1";
const LIST_PATH = `/v1/agent/runs/${RUN}/host-writes`;
const REVERT_PATH = `/v1/agent/runs/${RUN}/host-writes/revert`;

const CAPABILITIES: TransportCapabilities = {
  substrate: "desktop-webview",
  nativeSecretStorage: true,
  fileSystemAccess: true,
  clipboardWrite: true,
  openExternal: true,
};

const ENTRIES = [
  {
    entry_id: "e1",
    tool_call_id: "call_write_plan",
    sequence: 1,
    path: "/Users/x/Documents/quarterly-plan.md",
    kind: "modified",
    prior_size: 812,
    revertible: true,
    captured_at: "2026-01-01T00:00:00Z",
  },
  {
    entry_id: "e2",
    tool_call_id: "call_write_plan",
    sequence: 2,
    path: "/Users/x/Documents/appendix.md",
    kind: "created",
    prior_size: 0,
    revertible: true,
    captured_at: "2026-01-01T00:00:01Z",
  },
  // A second tool call, so "undo one thing" is a real choice rather than the
  // only button on screen.
  {
    entry_id: "e3",
    tool_call_id: "call_write_log",
    sequence: 3,
    path: "/Users/x/Documents/log.txt",
    kind: "created",
    prior_size: 0,
    revertible: true,
    captured_at: "2026-01-01T00:00:02Z",
  },
];

class HostWritesTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  private onEvent: ((raw: string) => void) | undefined;

  constructor(private readonly listing: unknown) {}

  async request<TRes>(request: TypedRequest): Promise<TRes> {
    this.requests.push(request);
    if (request.path === LIST_PATH) {
      if (this.listing instanceof Error) throw this.listing;
      return this.listing as TRes;
    }
    if (request.path === REVERT_PATH) {
      const toolCallId = (request.body as { tool_call_id?: string })
        .tool_call_id;
      return {
        run_id: RUN,
        tool_call_id: toolCallId,
        outcomes: [
          {
            path: "/Users/x/Documents/quarterly-plan.md",
            kind: "modified",
            status: "restored",
          },
          {
            path: "/Users/x/Documents/appendix.md",
            kind: "created",
            status: "refused",
            detail: "target is a symlink",
          },
        ],
      } as TRes;
    }
    if (request.path.includes("/messages")) return { messages: [] } as TRes;
    return {
      latest_run_id: RUN,
      latest_run_id_any_status: RUN,
      runs: [],
    } as TRes;
  }

  subscribeServerSentEvents(options: SseSubscribeOptions): SseSubscription {
    if (options.eventName === "runtime_event") this.onEvent = options.onMessage;
    return { close: () => {} };
  }

  hasEventSubscriber(): boolean {
    return this.onEvent !== undefined;
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }
}

function store(): KeyValueStore {
  return { get: () => null, set: () => {}, keys: () => [] };
}

function mount(transport: Transport): void {
  render(
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store()}>
        <RunDestination conversationId={CONV} />
      </KeyValueStoreProvider>
    </TransportProvider>,
  );
}

async function openChanges(transport: HostWritesTransport): Promise<void> {
  await screen.findByTestId("thread-canvas");
  await waitFor(() => expect(transport.hasEventSubscriber()).toBe(true));
  const tab = await screen.findByRole("tab", { name: /Changes/ });
  await act(async () => {
    fireEvent.click(tab);
  });
}

describe("RunDestination — the revert affordance is reachable", () => {
  it("reads the run's host-write journal through the facade route", async () => {
    const transport = new HostWritesTransport({
      run_id: RUN,
      entries: ENTRIES,
    });
    mount(transport);
    await waitFor(() =>
      expect(
        transport.requests.some(
          (r) => r.method === "GET" && r.path === LIST_PATH,
        ),
      ).toBe(true),
    );
  });

  it("draws a Changes tab counting the FILES the run altered, not the records", async () => {
    const transport = new HostWritesTransport({
      run_id: RUN,
      entries: ENTRIES,
    });
    mount(transport);
    await openChanges(transport);
    // Three records over three distinct paths — the badge counts paths, which
    // is what an undo restores after the server's per-path collapse.
    expect(screen.getByTestId("run-rail-changes-badge")).toHaveTextContent("3");
    expect(screen.getByTestId("run-rail-panel-changes")).toBeInTheDocument();
  });

  it("undoes ONE tool call, and never the whole run", async () => {
    const transport = new HostWritesTransport({
      run_id: RUN,
      entries: ENTRIES,
    });
    mount(transport);
    await openChanges(transport);

    // Both tool calls are on screen, each with its own control — that is what
    // makes "undo the one bad thing" a choice rather than a hammer.
    expect(
      screen.getByTestId("host-writes-group-call_write_plan"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("host-writes-group-call_write_log"),
    ).toBeInTheDocument();

    // Nothing posts from the collapsed row.
    fireEvent.click(screen.getByTestId("host-writes-undo-call_write_plan"));
    expect(transport.requests.some((r) => r.path === REVERT_PATH)).toBe(false);

    await act(async () => {
      fireEvent.click(
        screen.getByTestId("host-writes-confirm-call_write_plan"),
      );
    });

    await waitFor(() =>
      expect(transport.requests).toContainEqual(
        expect.objectContaining({
          method: "POST",
          path: REVERT_PATH,
          body: { tool_call_id: "call_write_plan" },
        }),
      ),
    );
    // The whole-run revert is `tool_call_id` omitted. No control on this
    // surface performs it, so no request may ever leave without the id.
    for (const request of transport.requests.filter(
      (r) => r.path === REVERT_PATH,
    )) {
      expect((request.body as { tool_call_id?: unknown }).tool_call_id).toBe(
        "call_write_plan",
      );
    }
    // And the OTHER tool call is untouched — its control is still offered.
    expect(
      screen.getByTestId("host-writes-undo-call_write_log"),
    ).toBeInTheDocument();
  });

  it("renders the per-path receipt rather than mutating silently", async () => {
    const transport = new HostWritesTransport({
      run_id: RUN,
      entries: ENTRIES,
    });
    mount(transport);
    await openChanges(transport);
    fireEvent.click(screen.getByTestId("host-writes-undo-call_write_plan"));
    await act(async () => {
      fireEvent.click(
        screen.getByTestId("host-writes-confirm-call_write_plan"),
      );
    });

    const receipt = await screen.findByTestId(
      "host-writes-receipt-call_write_plan",
    );
    // One row restored, one refused — so the card must NOT read as a success.
    expect(receipt).toHaveAttribute("data-complete", "false");
    expect(receipt.textContent).toContain("restored");
    expect(receipt.textContent).toContain("refused");
    expect(receipt.textContent).toContain("target is a symlink");
    expect(receipt.textContent).toContain("Partly undone");
  });
});

describe("RunDestination — the tab appears only when it has something to say", () => {
  it("draws no Changes tab when the run wrote nothing", async () => {
    const transport = new HostWritesTransport({ run_id: RUN, entries: [] });
    mount(transport);
    await screen.findByTestId("thread-canvas");
    await waitFor(() =>
      expect(transport.requests.some((r) => r.path === LIST_PATH)).toBe(true),
    );
    expect(screen.queryByRole("tab", { name: /Changes/ })).toBeNull();
  });

  // Every non-desktop image composes no object store, so the routes answer 503
  // by design. That is the capability speaking, not a fault — a red error strip
  // over a deployment behaving exactly as intended is worse than no tab.
  it("draws no Changes tab and no error when the deployment captures no writes", async () => {
    const transport = new HostWritesTransport(
      new TransportHttpError(
        503,
        "Agent-write undo is not available on this deployment.",
      ),
    );
    mount(transport);
    await screen.findByTestId("thread-canvas");
    await waitFor(() =>
      expect(transport.requests.some((r) => r.path === LIST_PATH)).toBe(true),
    );
    expect(screen.queryByRole("tab", { name: /Changes/ })).toBeNull();
    expect(screen.queryByTestId("host-writes-tab-error")).toBeNull();
  });

  // A failure to READ the journal is different: silence there is
  // indistinguishable from "this run changed nothing on your disk".
  it("draws the tab to report a read failure", async () => {
    const transport = new HostWritesTransport(
      new TransportHttpError(500, "boom"),
    );
    mount(transport);
    await screen.findByTestId("thread-canvas");
    await waitFor(() => expect(transport.hasEventSubscriber()).toBe(true));
    const tab = await screen.findByRole("tab", { name: /Changes/ });
    await act(async () => {
      fireEvent.click(tab);
    });
    expect(screen.getByTestId("host-writes-tab-error")).toBeInTheDocument();
  });
});
