import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Tier2Loader,
  type Tier2WorkerLike,
  type Tier2WorkerRequest,
  type Tier2WorkerResponse,
} from "./Tier2Loader";

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
