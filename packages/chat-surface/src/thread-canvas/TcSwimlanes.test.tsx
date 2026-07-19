import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import type {
  SseSubscribeOptions,
  SseSubscription,
  Transport,
} from "@0x-copilot/chat-transport";

import { KeyValueStoreProvider } from "../providers/KeyValueStoreProvider";
import { TransportProvider } from "../providers/TransportProvider";
import type { KeyValueStore } from "../storage/key-value-store";

import { TcSwimlanes, type Playhead } from "./TcSwimlanes";

interface FakeTransport extends Transport {
  emit(raw: string): void;
  readonly requests: Array<{ method: string; path: string; query?: unknown }>;
  readonly subscribePath: string | null;
  readonly closed: boolean;
}

function makeTransport(): FakeTransport {
  let onMessage: ((raw: string) => void) | null = null;
  let subscribePath: string | null = null;
  let closed = false;
  const requests: Array<{ method: string; path: string; query?: unknown }> = [];

  const transport: FakeTransport = {
    request: <TRes,>(req: {
      method: string;
      path: string;
      query?: unknown;
    }): Promise<TRes> => {
      requests.push({ method: req.method, path: req.path, query: req.query });
      return Promise.resolve({} as TRes);
    },
    subscribeServerSentEvents: (opts: SseSubscribeOptions): SseSubscription => {
      onMessage = opts.onMessage;
      subscribePath = opts.path;
      return {
        close: () => {
          closed = true;
          onMessage = null;
        },
      };
    },
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
    emit: (raw: string) => {
      if (onMessage !== null) {
        onMessage(raw);
      }
    },
    requests,
    get subscribePath() {
      return subscribePath;
    },
    get closed() {
      return closed;
    },
  };
  return transport;
}

function makeKvStore(initial: Record<string, string> = {}): KeyValueStore & {
  readonly snapshot: () => Record<string, string | null>;
} {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    get: (key) => store.get(key) ?? null,
    set: (key, value) => {
      if (value === null) {
        store.delete(key);
      } else {
        store.set(key, value);
      }
    },
    keys: (prefix) => {
      const out: string[] = [];
      for (const key of store.keys()) {
        if (prefix === undefined || key.startsWith(prefix)) {
          out.push(key);
        }
      }
      return out;
    },
    snapshot: () => {
      const out: Record<string, string | null> = {};
      for (const [k, v] of store) {
        out[k] = v;
      }
      return out;
    },
  };
}

function envelope(
  overrides: Partial<RuntimeEventEnvelope> & {
    readonly created_at: string;
    readonly event_id: string;
  },
): RuntimeEventEnvelope {
  return {
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: 1,
    event_type: "run_started",
    activity_kind: "run",
    payload: {},
    ...overrides,
  } as RuntimeEventEnvelope;
}

function renderWith(
  transport: FakeTransport,
  kvStore: KeyValueStore,
  ui: ReactNode,
): ReturnType<typeof render> {
  return render(
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={kvStore}>{ui}</KeyValueStoreProvider>
    </TransportProvider>,
  );
}

describe("TcSwimlanes", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.useFakeTimers();
  });

  afterEach(() => {
    warnSpy.mockRestore();
    vi.useRealTimers();
  });

  it("shows only the empty state (no transport controls) until beads arrive", () => {
    const transport = makeTransport();
    const kv = makeKvStore();
    renderWith(transport, kv, <TcSwimlanes runId="run-1" />);
    expect(screen.getByTestId("tc-swimlanes-empty")).toBeInTheDocument();
    // Progressive disclosure: the toolbar is withheld, not disabled — dead
    // `<`/`Play`/`>` chrome over an empty timeline reads as broken.
    expect(screen.queryByTestId("tc-swimlanes-back")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tc-swimlanes-play")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-swimlanes-forward"),
    ).not.toBeInTheDocument();
    // Subscription is still live despite the collapsed toolbar, so the first
    // bead will progressively reveal the controls.
    expect(transport.subscribePath).toBe("/v1/agent/runs/run-1/stream");
  });

  it("renders beads from the SSE stream in their surface lanes", () => {
    const transport = makeTransport();
    const kv = makeKvStore();
    renderWith(transport, kv, <TcSwimlanes runId="run-1" />);

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e2",
            created_at: "2026-05-17T10:00:05.000Z",
            payload: { surface_uri: "sheet-row://row-2" },
          }),
        ),
      );
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e3",
            created_at: "2026-05-17T10:00:10.000Z",
            payload: {},
          }),
        ),
      );
    });

    expect(screen.getByTestId("tc-swimlanes-lane-email")).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-swimlanes-lane-sheet-row"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-lane-system")).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-bead-e1")).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-bead-e2")).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-bead-e3")).toBeInTheDocument();
  });

  it("silently drops malformed messages and non-envelope JSON", () => {
    const transport = makeTransport();
    renderWith(transport, makeKvStore(), <TcSwimlanes runId="run-1" />);

    act(() => {
      transport.emit("not json");
      transport.emit(JSON.stringify({ event_id: "no-run-id" }));
    });

    expect(screen.getByTestId("tc-swimlanes-empty")).toBeInTheDocument();
  });

  it("clicking a bead moves the playhead off-now and calls onScrubChange", () => {
    const transport = makeTransport();
    const onScrubChange = vi.fn<(p: Playhead) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onScrubChange={onScrubChange} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e2",
            created_at: "2026-05-17T10:00:05.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    fireEvent.click(screen.getByTestId("tc-swimlanes-bead-select-e1"));

    expect(onScrubChange).toHaveBeenLastCalledWith({
      at: Date.parse("2026-05-17T10:00:00.000Z"),
    });
    expect(screen.getByTestId("tc-swimlanes")).toHaveAttribute(
      "data-playhead",
      "scrubbed",
    );
  });

  it("ArrowLeft / ArrowRight step beads; Escape snaps to now", () => {
    const transport = makeTransport();
    const onScrubChange = vi.fn<(p: Playhead) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onScrubChange={onScrubChange} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e2",
            created_at: "2026-05-17T10:00:05.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    const container = screen.getByTestId("tc-swimlanes");
    fireEvent.keyDown(container, { key: "ArrowLeft" });
    expect(onScrubChange).toHaveBeenLastCalledWith({
      at: Date.parse("2026-05-17T10:00:00.000Z"),
    });

    fireEvent.keyDown(container, { key: "ArrowRight" });
    expect(onScrubChange).toHaveBeenLastCalledWith({
      at: Date.parse("2026-05-17T10:00:05.000Z"),
    });

    fireEvent.keyDown(container, { key: "Escape" });
    expect(onScrubChange).toHaveBeenLastCalledWith("now");
    expect(container).toHaveAttribute("data-playhead", "now");
  });

  it("⌘← / ⌘→ step beads and ⌘L snaps to now (PR-3.7 / FR-3.14)", () => {
    const transport = makeTransport();
    const onScrubChange = vi.fn<(p: Playhead) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onScrubChange={onScrubChange} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e2",
            created_at: "2026-05-17T10:00:05.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    const container = screen.getByTestId("tc-swimlanes");

    // ⌘← steps back from now to the previous bead…
    fireEvent.keyDown(container, { key: "ArrowLeft", metaKey: true });
    expect(onScrubChange).toHaveBeenLastCalledWith({
      at: Date.parse("2026-05-17T10:00:00.000Z"),
    });

    // …⌘→ steps forward…
    fireEvent.keyDown(container, { key: "ArrowRight", metaKey: true });
    expect(onScrubChange).toHaveBeenLastCalledWith({
      at: Date.parse("2026-05-17T10:00:05.000Z"),
    });

    // …and ⌘L snaps back to live.
    fireEvent.keyDown(container, { key: "l", metaKey: true });
    expect(onScrubChange).toHaveBeenLastCalledWith("now");
    expect(container).toHaveAttribute("data-playhead", "now");
  });

  it("Snap-to-now, Branch and Restore are visible only when scrubbed off-now", () => {
    const transport = makeTransport();
    const onBranch = vi.fn<(at: number) => void>();
    const onRestore = vi.fn<(at: number) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onBranch={onBranch} onRestore={onRestore} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    expect(
      screen.queryByTestId("tc-swimlanes-snap-now"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("tc-swimlanes-branch")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-swimlanes-restore"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("tc-swimlanes-bead-select-e1"));

    expect(screen.getByTestId("tc-swimlanes-snap-now")).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-branch")).toBeInTheDocument();
    expect(screen.getByTestId("tc-swimlanes-restore")).toBeInTheDocument();
  });

  it("Branch and Restore call Transport.request with the placeholder paths", async () => {
    const transport = makeTransport();
    const onBranch = vi.fn<(at: number) => void>();
    const onRestore = vi.fn<(at: number) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onBranch={onBranch} onRestore={onRestore} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    fireEvent.click(screen.getByTestId("tc-swimlanes-bead-select-e1"));
    fireEvent.click(screen.getByTestId("tc-swimlanes-branch"));
    fireEvent.click(screen.getByTestId("tc-swimlanes-restore"));

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const branchReq = transport.requests.find(
      (r) => r.path === "/v1/agent/runs/run-1/branch",
    );
    const restoreReq = transport.requests.find(
      (r) => r.path === "/v1/agent/runs/run-1/restore",
    );
    expect(branchReq).toBeDefined();
    expect(branchReq?.method).toBe("POST");
    expect((branchReq?.query as Record<string, unknown>)?.at).toBe(
      Date.parse("2026-05-17T10:00:00.000Z"),
    );
    expect(restoreReq).toBeDefined();
    expect(restoreReq?.method).toBe("POST");

    expect(onBranch).toHaveBeenCalledWith(
      Date.parse("2026-05-17T10:00:00.000Z"),
    );
    expect(onRestore).toHaveBeenCalledWith(
      Date.parse("2026-05-17T10:00:00.000Z"),
    );
  });

  it("Snap-to-now button returns the playhead to now", () => {
    const transport = makeTransport();
    const onScrubChange = vi.fn<(p: Playhead) => void>();
    renderWith(
      transport,
      makeKvStore(),
      <TcSwimlanes runId="run-1" onScrubChange={onScrubChange} />,
    );

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    fireEvent.click(screen.getByTestId("tc-swimlanes-bead-select-e1"));
    expect(screen.getByTestId("tc-swimlanes-snap-now")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("tc-swimlanes-snap-now"));
    expect(onScrubChange).toHaveBeenLastCalledWith("now");
  });

  it("pins persist to the key-value store keyed by run id", () => {
    const transport = makeTransport();
    const kv = makeKvStore();
    renderWith(transport, kv, <TcSwimlanes runId="run-1" />);

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    const pinButton = screen.getByTestId("tc-swimlanes-bead-pin-e1");
    fireEvent.click(pinButton);
    expect(kv.get("swimlanes:pinned:run-1")).toBe(JSON.stringify(["e1"]));
    expect(
      screen.getByTestId("tc-swimlanes-bead-e1").getAttribute("data-pinned"),
    ).toBe("true");

    fireEvent.click(pinButton);
    expect(kv.get("swimlanes:pinned:run-1")).toBeNull();
  });

  it("re-mounting restores pinned beads from the key-value store", () => {
    const transport = makeTransport();
    const kv = makeKvStore({
      "swimlanes:pinned:run-1": JSON.stringify(["e1"]),
    });
    renderWith(transport, kv, <TcSwimlanes runId="run-1" />);

    act(() => {
      transport.emit(
        JSON.stringify(
          envelope({
            event_id: "e1",
            created_at: "2026-05-17T10:00:00.000Z",
            payload: { surface_uri: "email://draft-1" },
          }),
        ),
      );
    });

    expect(
      screen.getByTestId("tc-swimlanes-bead-e1").getAttribute("data-pinned"),
    ).toBe("true");
  });

  it("closes the subscription on unmount and re-subscribes when runId changes", () => {
    const transport = makeTransport();
    const { rerender, unmount } = render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeKvStore()}>
          <TcSwimlanes runId="run-1" />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );
    expect(transport.subscribePath).toBe("/v1/agent/runs/run-1/stream");

    rerender(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeKvStore()}>
          <TcSwimlanes runId="run-2" />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );
    expect(transport.subscribePath).toBe("/v1/agent/runs/run-2/stream");

    unmount();
    expect(transport.closed).toBe(true);
  });
});
