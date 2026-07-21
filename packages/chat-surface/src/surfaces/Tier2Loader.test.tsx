import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Tier2Loader,
  type Tier2WorkerLike,
  type Tier2WorkerRequest,
  type Tier2WorkerResponse,
} from "./Tier2Loader";
import { executeAdapterRender } from "./tier2Worker";

class StubWorker implements Tier2WorkerLike {
  private listeners: {
    message: Set<(event: { data: unknown }) => void>;
    error: Set<(event: { data: unknown }) => void>;
  } = { message: new Set(), error: new Set() };
  public posted: Tier2WorkerRequest[] = [];
  public terminated = false;
  private behavior: (self: StubWorker, request: Tier2WorkerRequest) => void;

  constructor(
    behavior: (self: StubWorker, request: Tier2WorkerRequest) => void,
  ) {
    this.behavior = behavior;
  }

  postMessage(value: unknown): void {
    this.posted.push(value as Tier2WorkerRequest);
    this.behavior(this, value as Tier2WorkerRequest);
  }

  terminate(): void {
    this.terminated = true;
  }

  addEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void {
    this.listeners[type].add(listener);
  }

  removeEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void {
    this.listeners[type].delete(listener);
  }

  emitMessage(response: Tier2WorkerResponse): void {
    for (const listener of this.listeners.message) {
      listener({ data: response });
    }
  }

  emitError(detail: unknown): void {
    for (const listener of this.listeners.error) {
      listener({ data: detail });
    }
  }
}

const NOOP_SOURCE = "/* tier2 adapter */";

interface StubHolder {
  current: StubWorker | null;
}

describe("Tier2Loader — happy path", () => {
  it("reconciles a rendered JSON tree into the host DOM", async () => {
    const stub: StubHolder = { current: null };
    const factory = () => {
      const worker = new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "div",
              props: { "data-testid": "tier2-output" },
              children: ["hello tier2"],
            },
          });
        });
      });
      stub.current = worker;
      return worker;
    };

    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        state={{}}
        workerFactory={factory}
      />,
    );

    const node = await screen.findByTestId("tier2-output");
    expect(node).toHaveTextContent("hello tier2");
    expect(stub.current?.posted[0]?.kind).toBe("render");
    expect(stub.current?.posted[0]?.mode).toBe("current");
  });

  it("passes the diff payload when pendingDiff is set", async () => {
    const stub: StubHolder = { current: null };
    const factory = () => {
      const worker = new StubWorker((self, request) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "div",
              props: { "data-testid": "diff-mode" },
              children: [String(request.mode)],
            },
          });
        });
      });
      stub.current = worker;
      return worker;
    };

    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        pendingDiff={{ diff: { id: "d1" } }}
        workerFactory={factory}
      />,
    );

    const node = await screen.findByTestId("diff-mode");
    expect(node).toHaveTextContent("diff");
    expect(stub.current?.posted[0]?.payload).toEqual({ id: "d1" });
  });
});

describe("Tier2Loader — reconciliation safety", () => {
  it("strips on* handlers when reconciling", async () => {
    const factory = () => {
      return new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "div",
              props: {
                "data-testid": "no-handlers",
                onClick: "alert(1)",
              },
              children: ["safe"],
            },
          });
        });
      });
    };
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
      />,
    );
    const node = await screen.findByTestId("no-handlers");
    expect(node).not.toHaveAttribute("onClick");
    expect(node).not.toHaveAttribute("onclick");
  });

  it("marks unknown tags with data-tier2-unsafe-tag and renders as span", async () => {
    const factory = () => {
      return new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "iframe",
              props: { "data-testid": "unsafe", src: "x" },
              children: [],
            },
          });
        });
      });
    };
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
      />,
    );
    const node = await screen.findByTestId("unsafe");
    expect(node.tagName.toLowerCase()).toBe("span");
    expect(node).toHaveAttribute("data-tier2-unsafe-tag", "iframe");
  });

  it("maps ds:Button to the design-system Button", async () => {
    const factory = () => {
      return new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "ds:Button",
              props: { "data-testid": "ds-btn", variant: "primary" },
              children: ["click"],
            },
          });
        });
      });
    };
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
      />,
    );
    const btn = await screen.findByTestId("ds-btn");
    expect(btn.tagName.toLowerCase()).toBe("button");
  });
});

describe("Tier2Loader — failure modes", () => {
  it("renders null when the worker reports a throw (adapter exception)", async () => {
    const failures: Array<{ reason: string; detail?: string }> = [];
    const factory = () => {
      return new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "failed",
            reason: "throw",
            detail: "boom",
          });
        });
      });
    };
    const { container } = render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
        onFailure={(reason, detail) => failures.push({ reason, detail })}
      />,
    );
    await waitFor(() => expect(failures).toHaveLength(1));
    expect(failures[0].reason).toBe("throw");
    expect(
      container.querySelector("[data-testid='tier2-loader-pending']"),
    ).toBeNull();
  });

  it("renders null and reports oom when the worker reports oom", async () => {
    const failures: Array<{ reason: string }> = [];
    const factory = () => {
      return new StubWorker((self) => {
        queueMicrotask(() => {
          self.emitMessage({
            kind: "failed",
            reason: "oom",
            detail: "1GB string rejected",
          });
        });
      });
    };
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
        onFailure={(reason) => failures.push({ reason })}
      />,
    );
    await waitFor(() => expect(failures).toHaveLength(1));
    expect(failures[0].reason).toBe("oom");
  });
});

describe("Tier2Loader — preemptive timeout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("terminates the worker and reports timeout when it never responds", async () => {
    const failures: Array<{ reason: string; detail?: string }> = [];
    const stub: StubHolder = { current: null };
    const factory = () => {
      const worker = new StubWorker(() => {
        // Never respond — simulates while(true){}.
      });
      stub.current = worker;
      return worker;
    };

    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
        budgetMs={100}
        onFailure={(reason, detail) => failures.push({ reason, detail })}
      />,
    );

    expect(stub.current?.terminated).toBe(false);
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(stub.current?.terminated).toBe(true);
    expect(failures).toHaveLength(1);
    expect(failures[0].reason).toBe("timeout");
  });

  it("does not terminate when the worker responds before the budget", async () => {
    const stub: StubHolder = { current: null };
    const factory = () => {
      const worker = new StubWorker((self) => {
        setTimeout(() => {
          self.emitMessage({
            kind: "rendered",
            tree: {
              tag: "div",
              props: { "data-testid": "fast-render" },
              children: ["under budget"],
            },
          });
        }, 50);
      });
      stub.current = worker;
      return worker;
    };
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={factory}
        budgetMs={100}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(50);
    });
    expect(stub.current?.terminated).toBe(false);
    const node = screen.getByTestId("fast-render");
    expect(node).toHaveTextContent("under budget");
    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(stub.current?.terminated).toBe(false);
  });
});

// A worker that runs the REAL render core (`executeAdapterRender` — the exact
// code the production worker executes) synchronously, delivering the result on
// a microtask. jsdom cannot run a real Web Worker, so this drives the real
// render path end-to-end through the loader without one.
class RealCoreWorker implements Tier2WorkerLike {
  private listeners = new Set<(event: { data: unknown }) => void>();
  public terminated = false;
  postMessage(value: unknown): void {
    const response = executeAdapterRender(value as Tier2WorkerRequest);
    queueMicrotask(() => {
      for (const listener of this.listeners) listener({ data: response });
    });
  }
  terminate(): void {
    this.terminated = true;
  }
  addEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void {
    if (type === "message") this.listeners.add(listener);
  }
  removeEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void {
    if (type === "message") this.listeners.delete(listener);
  }
}

const GENERATED_ADAPTER_SOURCE = [
  'import * as React from "react";',
  'import { tokens } from "@0x-copilot/design-system";',
  "void tokens;",
  "export const renderCurrent = (state) =>",
  '  React.createElement("div", { "data-testid": "real-out" },',
  '    React.createElement("strong", null, "Title:"),',
  "    String(state && state.title));",
  'export const renderDiff = (d) => React.createElement("div", null, "diff");',
  'export const adapter = { scheme: "record", matches: () => true,',
  "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
  '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
].join("\n");

describe("Tier2Loader — real render core (AC1)", () => {
  it("renders a known-good adapter source through the real worker core", async () => {
    const worker = new RealCoreWorker();
    render(
      <Tier2Loader
        adapterSource={GENERATED_ADAPTER_SOURCE}
        scheme="record"
        version={1}
        state={{ title: "Hello" }}
        workerFactory={() => worker}
      />,
    );
    const node = await screen.findByTestId("real-out");
    expect(node.tagName.toLowerCase()).toBe("div");
    expect(node).toHaveTextContent("Title:");
    expect(node).toHaveTextContent("Hello");
    expect(node.querySelector("strong")).not.toBeNull();
  });

  it("reports onFailure when the real core rejects a source touching fetch", async () => {
    const failures: Array<{ reason: string }> = [];
    const badSource = [
      "export const renderCurrent = (s) => { fetch('http://x'); return null; };",
      "export const renderDiff = (d) => null;",
      'export const adapter = { scheme: "x", matches: () => true,',
      "  renderCurrent: renderCurrent, renderDiff: renderDiff,",
      '  metadata: { origin: "agent-generated", schemaVersion: 1 } };',
    ].join("\n");
    render(
      <Tier2Loader
        adapterSource={badSource}
        scheme="x"
        version={1}
        state={{}}
        workerFactory={() => new RealCoreWorker()}
        onFailure={(reason) => failures.push({ reason })}
      />,
    );
    await waitFor(() => expect(failures).toHaveLength(1));
    expect(failures[0].reason).toBe("throw");
  });
});

describe("Tier2Loader — looping source preemption (AC1)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("terminates a worker whose source loops forever and reports timeout", async () => {
    // A real infinite loop cannot be run on the test thread; the loader's
    // preemptive boundary is `worker.terminate()` on the wall-clock budget,
    // so a never-responding worker (the observable shape of `while(true){}`
    // inside a real Worker) is the correct stand-in.
    const failures: Array<{ reason: string }> = [];
    const worker = new (class implements Tier2WorkerLike {
      public terminated = false;
      postMessage(): void {
        /* never responds — models a source stuck in a loop */
      }
      terminate(): void {
        this.terminated = true;
      }
      addEventListener(): void {}
      removeEventListener(): void {}
    })();
    render(
      <Tier2Loader
        adapterSource={"while (true) {}"}
        scheme="loop"
        version={1}
        state={{}}
        budgetMs={100}
        workerFactory={() => worker}
        onFailure={(reason) => failures.push({ reason })}
      />,
    );
    expect(worker.terminated).toBe(false);
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(worker.terminated).toBe(true);
    expect(failures).toHaveLength(1);
    expect(failures[0].reason).toBe("timeout");
  });
});

describe("Tier2Loader — worker factory failure", () => {
  it("surfaces a throw failure when the factory itself throws", async () => {
    const failures: Array<{ reason: string }> = [];
    render(
      <Tier2Loader
        adapterSource={NOOP_SOURCE}
        scheme="demo"
        version={1}
        workerFactory={() => {
          throw new Error("factory exploded");
        }}
        onFailure={(reason) => failures.push({ reason })}
      />,
    );
    await waitFor(() => expect(failures).toHaveLength(1));
    expect(failures[0].reason).toBe("throw");
  });
});
