// @vitest-environment jsdom
//
// useFirstRunLocalModel — PRD-P8 §6. The hook exposes two orthogonal axes
// (`runtime` = server-derived runtime state, `phase` = download lifecycle), so
// these tests are written against those axes and against OBSERVABLE behaviour
// (what the port was asked to do, what the card would render) — never against
// internal refs or effect wiring.
//
// The load-bearing property, and the reason PRD-P8 exists at all: after ANY
// failure the hook must be either visibly progressing or blocked with a message
// the user can act on. A frozen bar with no signal and no way forward is the
// bug (`the permanent "Queued — starts when the model lands" hang`).
//
// Timing is deterministic: fake timers + explicit flushes, never `waitFor`.

import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  LocalModelPullEvent,
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

import type { PresenceSignal, PresenceState } from "../ports/PresenceSignal";
import { PresenceSignalProvider } from "../providers/PresenceSignalProvider";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { FIRST_RUN_COPY } from "./firstRun";
import type { FirstRunLocalModelsPort } from "./localModelsPort";
import {
  useFirstRunLocalModel,
  type UseFirstRunLocalModelResult,
} from "./useFirstRunLocalModel";

// --- PRD-P8 §6 cadence + backoff, restated here so a silent drift fails ------
const FAST_POLL_MS = 3_000;
const FAST_POLL_WINDOW_MS = 120_000;
const SLOW_POLL_MS = 15_000;
const FIRST_BACKOFF_MS = 1_000;

const PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  sizeBytes: 4_280_404_704,
};

/** What Ollama stores an `hf.co` GGUF pull as. */
const INSTALLED_TAG = "hf.co/Qwen/Qwen3-4B-GGUF:Q8_0";

function installedPreset(name = INSTALLED_TAG): LocalModelSummary {
  return {
    name,
    size_bytes: 4_280_404_704,
    quantization: "Q8_0",
    parameter_size: "4B",
    run_placement: null,
  };
}

// --- statuses the facade can answer with ------------------------------------

const RUNNING: LocalModelsStatus = {
  enabled: true,
  ollama_running: true,
  ollama_version: "0.5.4",
  runtime_state: "running",
  runtime_managed: true,
};

const STOPPED: LocalModelsStatus = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
  runtime_state: "stopped",
  runtime_managed: true,
};

const NOT_INSTALLED: LocalModelsStatus = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
  runtime_state: "not_installed",
  runtime_managed: true,
};

const FEATURE_OFF: LocalModelsStatus = {
  enabled: false,
  ollama_running: false,
  ollama_version: null,
  runtime_state: "unknown",
  runtime_managed: false,
};

/** An older server (PRD-P8 D3): neither optional field is present. */
const LEGACY_DOWN: LocalModelsStatus = {
  enabled: true,
  ollama_running: false,
  ollama_version: null,
};

const LEGACY_UP: LocalModelsStatus = {
  enabled: true,
  ollama_running: true,
  ollama_version: "0.5.4",
};

function frame(over: Partial<LocalModelPullEvent>): LocalModelPullEvent {
  return {
    sequence_no: 0,
    status: "pulling",
    bytes_total: null,
    bytes_completed: null,
    speed_bps: null,
    eta_seconds: null,
    done: false,
    error: null,
    ...over,
  };
}

// --- a hand-driven pull stream so each frame's timing is the test's ---------

interface PullStream {
  readonly iterable: AsyncIterable<LocalModelPullEvent>;
  /** Emit one SSE frame. */
  push(f: LocalModelPullEvent): void;
  /** End the stream WITHOUT a terminal frame (a torn SSE connection). */
  end(): void;
  /** Blow the stream up (a transport throw). */
  fail(e: Error): void;
}

function makeStream(): PullStream {
  const queue: LocalModelPullEvent[] = [];
  let failure: Error | null = null;
  let ended = false;
  let wake: (() => void) | null = null;
  const notify = (): void => {
    if (wake) {
      const resume = wake;
      wake = null;
      resume();
    }
  };
  const iterable: AsyncIterable<LocalModelPullEvent> = {
    async *[Symbol.asyncIterator]() {
      while (true) {
        while (queue.length > 0) {
          const f = queue.shift() as LocalModelPullEvent;
          yield f;
          // Mirrors `createFirstRunLocalModelsPort`'s terminator EXACTLY,
          // including its nullish check — a frame with no `error` key is not a
          // terminal frame and must not close the stream.
          if (f.done || (f.error !== null && f.error !== undefined)) return;
        }
        if (failure) throw failure;
        if (ended) return;
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    },
  };
  return {
    iterable,
    push: (f) => {
      queue.push(f);
      notify();
    },
    end: () => {
      ended = true;
      notify();
    },
    fail: (e) => {
      failure = e;
      notify();
    },
  };
}

// --- the fake host port ------------------------------------------------------

interface Harness {
  readonly port: FirstRunLocalModelsPort;
  readonly calls: {
    status: number;
    list: number;
    pull: number;
    startRuntime: number;
  };
  /** Every stream handed out, in `pull()` order. */
  readonly streams: readonly PullStream[];
  stream(index: number): PullStream;
  setStatus(next: LocalModelsStatus): void;
  setModels(next: readonly LocalModelSummary[]): void;
  setStatusThrows(next: boolean): void;
}

function harness(initial: LocalModelsStatus): Harness {
  let status = initial;
  let models: readonly LocalModelSummary[] = [];
  let statusThrows = false;
  const streams: PullStream[] = [];
  const calls = { status: 0, list: 0, pull: 0, startRuntime: 0 };

  const port: FirstRunLocalModelsPort = {
    status: () => {
      calls.status += 1;
      return statusThrows
        ? Promise.reject(new Error("probe blip"))
        : Promise.resolve(status);
    },
    list: () => {
      calls.list += 1;
      return Promise.resolve(models);
    },
    pull: () => {
      calls.pull += 1;
      const stream = makeStream();
      streams.push(stream);
      return stream.iterable;
    },
    startRuntime: () => {
      calls.startRuntime += 1;
      return Promise.resolve(status);
    },
  };

  return {
    port,
    calls,
    streams,
    stream: (index) => {
      const stream = streams[index];
      if (!stream) throw new Error(`no pull #${index + 1} was ever opened`);
      return stream;
    },
    setStatus: (next) => {
      status = next;
    },
    setModels: (next) => {
      models = next;
    },
    setStatusThrows: (next) => {
      statusThrows = next;
    },
  };
}

function fakePresence(initial: PresenceState): {
  readonly signal: PresenceSignal;
  set(next: PresenceState): void;
} {
  let state = initial;
  const subscribers = new Set<(next: PresenceState) => void>();
  return {
    signal: {
      current: () => state,
      subscribe: (fn) => {
        subscribers.add(fn);
        return () => {
          subscribers.delete(fn);
        };
      },
    },
    set: (next) => {
      state = next;
      for (const fn of subscribers) fn(next);
    },
  };
}

// --- deterministic flushing --------------------------------------------------

/**
 * Advance the fake clock by `ms` and let every promise chain the hook opened
 * settle, inside `act` so React commits. The trailing zero-advance is a second
 * macrotask turn — the probe chains `status()` → `list()`, and a failure chains
 * `fail()` → `probe()`.
 */
async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    await vi.advanceTimersByTimeAsync(0);
  });
}

function mount(
  port: FirstRunLocalModelsPort,
  options: {
    readonly onReady?: (modelName: string) => void;
    readonly presence?: PresenceSignal;
  } = {},
) {
  const { presence } = options;
  return renderHook(
    () =>
      useFirstRunLocalModel({ port, preset: PRESET, onReady: options.onReady }),
    presence
      ? {
          wrapper: ({ children }: { readonly children: ReactNode }) =>
            createElement(PresenceSignalProvider, {
              signal: presence,
              children,
            }),
        }
      : undefined,
  );
}

/**
 * PRD-P8 D1's whole point, as a predicate: something is happening, or the user
 * has been told what went wrong, or the runtime is visibly down (state ④, which
 * carries `Restart Ollama` + "download resumes on its own"). Anything else is
 * the silent freeze this PRD exists to kill.
 */
function hasAWayForward(r: UseFirstRunLocalModelResult): boolean {
  if (
    r.phase === "downloading" ||
    r.phase === "reconnecting" ||
    r.phase === "ready"
  ) {
    return true;
  }
  if (r.blocked !== null && r.blocked.message.trim().length > 0) return true;
  return r.runtime !== "running";
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useFirstRunLocalModel — runtime derivation (PRD-P8 §4.2)", () => {
  it("reports the server's runtime_state verbatim when it is running", async () => {
    const h = harness(RUNNING);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.enabled).toBe(true);
    expect(result.current.runtime).toBe("running");
    expect(result.current.runtimeManaged).toBe(true);
    expect(result.current.phase).toBe("idle");
    expect(result.current.disabled).toBe(false);
    expect(result.current.localModelPct).toBeNull();
    expect(result.current.blocked).toBeNull();
  });

  it("distinguishes not_installed (state ①) from stopped (state ④)", async () => {
    const notInstalled = mount(harness(NOT_INSTALLED).port);
    await settle();
    expect(notInstalled.result.current.runtime).toBe("not_installed");
    notInstalled.unmount();

    const stopped = mount(harness(STOPPED).port);
    await settle();
    expect(stopped.result.current.runtime).toBe("stopped");
  });

  it("falls back to ollama_running when runtime_state is absent, and NEVER says not_installed", async () => {
    const h = harness(LEGACY_DOWN);
    const { result } = mount(h.port);
    await settle();

    // A client cannot see the host filesystem — guessing "not_installed" here
    // would render `Get Ollama ↗` to someone who already has Ollama.
    expect(result.current.runtime).toBe("unknown");
    expect(result.current.runtime).not.toBe("not_installed");
    // An absent `runtime_managed` must not produce a Restart button.
    expect(result.current.runtimeManaged).toBe(false);
    expect(result.current.disabled).toBe(true);

    h.setStatus(LEGACY_UP);
    await settle(FAST_POLL_MS);
    expect(result.current.runtime).toBe("running");
  });

  it("treats the feature being off as unknown, not as a missing runtime", async () => {
    const { result } = mount(harness(FEATURE_OFF).port);
    await settle();

    expect(result.current.enabled).toBe(false);
    expect(result.current.runtime).toBe("unknown");
    expect(result.current.disabled).toBe(true);
  });
});

describe("useFirstRunLocalModel — the Start path gate", () => {
  it("keeps Start inert when the feature is disabled (web/cloud)", async () => {
    const h = harness(FEATURE_OFF);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.disabled).toBe(true);
    act(() => result.current.start());
    await settle();

    expect(h.calls.pull).toBe(0);
    expect(result.current.phase).toBe("idle");
  });

  it("keeps Start inert while the runtime is not running", async () => {
    const h = harness(STOPPED);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.runtime).toBe("stopped");
    expect(result.current.disabled).toBe(true);
    act(() => result.current.start());
    await settle();

    expect(h.calls.pull).toBe(0);
  });

  it("re-probes on recheck() and opens the Start path once Ollama comes up", async () => {
    const h = harness(STOPPED);
    const { result } = mount(h.port);
    await settle();
    expect(result.current.disabled).toBe(true);

    h.setStatus(RUNNING);
    act(() => result.current.recheck());
    await settle();

    expect(result.current.runtime).toBe("running");
    expect(result.current.disabled).toBe(false);
  });
});

describe("useFirstRunLocalModel — polling (PRD-P8 §6)", () => {
  it("re-probes on the fast cadence while the runtime is not running, then slows down", async () => {
    const h = harness(STOPPED);
    mount(h.port);
    await settle();
    const afterMount = h.calls.status;
    expect(afterMount).toBe(1);

    // Fast window: every 3s.
    await settle(FAST_POLL_MS);
    expect(h.calls.status).toBe(afterMount + 1);
    await settle(FAST_POLL_MS);
    expect(h.calls.status).toBe(afterMount + 2);

    // Cross the 2-minute fast window…
    await settle(FAST_POLL_WINDOW_MS + SLOW_POLL_MS);
    const afterWindow = h.calls.status;

    // …a fast-cadence wait no longer produces a probe…
    await settle(FAST_POLL_MS);
    expect(h.calls.status).toBe(afterWindow);
    // …but the slow cadence does.
    await settle(SLOW_POLL_MS);
    expect(h.calls.status).toBe(afterWindow + 1);
  });

  it("stops probing once the runtime is running", async () => {
    const h = harness(RUNNING);
    mount(h.port);
    await settle();
    const afterMount = h.calls.status;

    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.status).toBe(afterMount);
  });

  it("stops polling entirely on unmount", async () => {
    const h = harness(STOPPED);
    const { unmount } = mount(h.port);
    await settle();
    const afterMount = h.calls.status;

    unmount();
    await settle(FAST_POLL_WINDOW_MS + SLOW_POLL_MS);
    expect(h.calls.status).toBe(afterMount);
  });

  it("never polls a hidden window, and resumes when it is shown again", async () => {
    const h = harness(STOPPED);
    const presence = fakePresence("hidden");
    mount(h.port, { presence: presence.signal });
    await settle();
    const afterMount = h.calls.status;

    await settle(FAST_POLL_MS * 4);
    expect(h.calls.status).toBe(afterMount);

    await act(async () => {
      presence.set("visible");
      await vi.advanceTimersByTimeAsync(0);
    });
    await settle(FAST_POLL_MS);
    expect(h.calls.status).toBeGreaterThan(afterMount);
  });

  it("keeps retrying a probe that never answers — a transport blip is not a dead end", async () => {
    const h = harness(STOPPED);
    h.setStatusThrows(true);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.runtime).toBe("unknown");
    expect(result.current.phase).toBe("idle");
    const afterMount = h.calls.status;

    await settle(FAST_POLL_MS);
    expect(h.calls.status).toBeGreaterThan(afterMount);

    h.setStatusThrows(false);
    h.setStatus(RUNNING);
    await settle(FAST_POLL_MS);
    expect(result.current.runtime).toBe("running");
  });
});

describe("useFirstRunLocalModel — auto-start on the runtime edge (PRD-P8 §6)", () => {
  it("auto-starts the download when a non-running runtime becomes running", async () => {
    const h = harness(STOPPED);
    const { result } = mount(h.port);
    await settle();
    expect(result.current.runtime).toBe("stopped");
    expect(h.calls.pull).toBe(0);

    // Ollama comes up between polls — design state ① "download starts once
    // it's detected".
    h.setStatus(RUNNING);
    await settle(FAST_POLL_MS);

    // REGRESSION GUARD: if the pull is ever kicked off from the probe's `.then`
    // instead of from an effect keyed on the runtime edge, `start()` reads the
    // pre-commit closure, no-ops, and this stays 0 forever.
    expect(h.calls.pull).toBe(1);
    expect(result.current.runtime).toBe("running");
    expect(result.current.phase).toBe("downloading");
    // It happened on the very probe that first reported "running" — no extra
    // round-trip was needed to notice.
    expect(h.calls.status).toBe(2);

    // And exactly once — the edge does not re-fire.
    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.pull).toBe(1);
    expect(result.current.phase).toBe("downloading");
  });

  it("does NOT auto-start when the runtime was already running at the first probe (state ② keeps its Start button)", async () => {
    const h = harness(RUNNING);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.runtime).toBe("running");
    expect(result.current.phase).toBe("idle");
    expect(result.current.disabled).toBe(false);
    expect(h.calls.pull).toBe(0);

    // Waiting changes nothing — the user has to ask.
    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.pull).toBe(0);
    expect(result.current.phase).toBe("idle");

    act(() => result.current.start());
    expect(h.calls.pull).toBe(1);
    expect(result.current.phase).toBe("downloading");
  });
});

describe("useFirstRunLocalModel — already-installed short-circuit (PRD-P8 §6)", () => {
  it("reports ready + onReady(tag) and issues NO pull when the preset is present", async () => {
    const h = harness(RUNNING);
    h.setModels([installedPreset()]);
    const onReady = vi.fn();
    const { result } = mount(h.port, { onReady });
    await settle();

    expect(result.current.modelInstalled).toBe(true);
    expect(result.current.phase).toBe("ready");
    expect(result.current.modelName).toBe(INSTALLED_TAG);
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(onReady).toHaveBeenCalledWith(INSTALLED_TAG);
    expect(h.calls.pull).toBe(0);

    // Still no redundant pull later, and onReady stays one-shot.
    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.pull).toBe(0);
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it("short-circuits instead of auto-starting when a stopped runtime comes back with the preset present", async () => {
    const h = harness(STOPPED);
    const onReady = vi.fn();
    const { result } = mount(h.port, { onReady });
    await settle();
    expect(result.current.runtime).toBe("stopped");

    h.setStatus(RUNNING);
    h.setModels([installedPreset()]);
    await settle(FAST_POLL_MS);

    expect(result.current.phase).toBe("ready");
    expect(result.current.modelInstalled).toBe(true);
    expect(h.calls.pull).toBe(0);
    expect(onReady).toHaveBeenCalledWith(INSTALLED_TAG);
  });

  it("never opens a pull for an already-installed preset, even for one render", async () => {
    // Regression: the probe used to commit `runtime = "running"` BEFORE the
    // awaited list() resolved, so one render had a running runtime with
    // `modelInstalled` still false — enough for the auto-start effect to open
    // a pull for a model already on disk (a wasted stream, and a ③ flash on a
    // machine that needs no download). `pull` must never be called at all.
    const h = harness(NOT_INSTALLED);
    const { result } = mount(h.port);
    await settle();
    expect(result.current.runtime).toBe("not_installed"); // auto-start armed

    h.setStatus(RUNNING);
    h.setModels([installedPreset()]);
    await settle(FAST_POLL_MS);
    await settle();

    expect(result.current.modelInstalled).toBe(true);
    expect(result.current.phase).toBe("ready");
    expect(h.calls.pull).toBe(0);
  });

  it("does not short-circuit when the running runtime has some OTHER model installed", async () => {
    const h = harness(RUNNING);
    h.setModels([installedPreset("llama3.2:3b")]);
    const { result } = mount(h.port);
    await settle();

    expect(result.current.modelInstalled).toBe(false);
    expect(result.current.phase).toBe("idle");
    expect(result.current.disabled).toBe(false);
  });
});

describe("useFirstRunLocalModel — download progress", () => {
  it("drives pct from frames and flips to ready + onReady(tag) on done", async () => {
    const h = harness(RUNNING);
    const onReady = vi.fn();
    const { result } = mount(h.port, { onReady });
    await settle();

    act(() => result.current.start());
    expect(result.current.phase).toBe("downloading");
    expect(result.current.localModelPct).toBe(2); // SPEC seed

    await act(async () => {
      h.stream(0).push(
        frame({ sequence_no: 1, bytes_completed: 50, bytes_total: 200 }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.localModelPct).toBe(25);

    // The tag is resolved from a fresh list() after the pull lands.
    h.setModels([installedPreset()]);
    await act(async () => {
      h.stream(0).push(
        frame({ sequence_no: 2, done: true, status: "success" }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.phase).toBe("ready");
    expect(result.current.localModelPct).toBe(100);
    expect(result.current.modelName).toBe(INSTALLED_TAG);
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(onReady).toHaveBeenCalledWith(INSTALLED_TAG);
  });

  it("exposes bytesCompleted / bytesTotal from live frames and carries them across status-only lines", async () => {
    const h = harness(RUNNING);
    const { result } = mount(h.port);
    await settle();
    act(() => result.current.start());

    expect(result.current.bytesCompleted).toBeNull();
    expect(result.current.bytesTotal).toBeNull();

    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 1,
          bytes_completed: 1_000_000,
          bytes_total: 4_000_000,
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.bytesCompleted).toBe(1_000_000);
    expect(result.current.bytesTotal).toBe(4_000_000);
    expect(result.current.localModelPct).toBe(25);

    // Ollama interleaves byte-less status lines ("verifying sha256"); the bar
    // must not snap back to 0.
    await act(async () => {
      h.stream(0).push(frame({ sequence_no: 2, status: "verifying sha256" }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.localModelPct).toBe(25);
    expect(result.current.bytesTotal).toBe(4_000_000);
  });

  it("does not fire onReady after unmount", async () => {
    const h = harness(RUNNING);
    const onReady = vi.fn();
    const { result, unmount } = mount(h.port, { onReady });
    await settle();
    act(() => result.current.start());

    unmount();
    await act(async () => {
      h.stream(0).push(frame({ sequence_no: 1, done: true }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(onReady).not.toHaveBeenCalled();
  });
});

describe("useFirstRunLocalModel — failure kinds (PRD-P8 D1)", () => {
  /** Drive a fresh hook to "downloading, 25% proven". */
  async function downloadingAt25(h: Harness) {
    const view = mount(h.port);
    await settle();
    act(() => view.result.current.start());
    await act(async () => {
      h.stream(0).push(
        frame({ sequence_no: 1, bytes_completed: 50, bytes_total: 200 }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(view.result.current.localModelPct).toBe(25);
    return view;
  }

  it("runtime_unreachable keeps progress, shows state ④, and resumes by itself when the daemon returns", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    // The daemon really is gone, so the re-probe behind the failure agrees.
    h.setStatus(STOPPED);
    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 2,
          status: "error",
          error: "connection refused",
          error_kind: "runtime_unreachable",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.runtime).toBe("stopped");
    expect(result.current.localModelPct).toBe(25); // progress is NOT discarded
    expect(result.current.blocked).toBeNull(); // no red terminal state
    expect(result.current.phase).not.toBe("downloading");
    expect(hasAWayForward(result.current)).toBe(true);

    // Nothing is retried while the daemon is down…
    await settle(FAST_POLL_MS * 3);
    expect(h.calls.pull).toBe(1);

    // …and it resumes on its own once Ollama answers again.
    h.setStatus(RUNNING);
    await settle(FAST_POLL_MS);
    expect(result.current.runtime).toBe("running");
    expect(result.current.phase).toBe("downloading");
    expect(h.calls.pull).toBe(2);
    expect(result.current.localModelPct).toBe(25);
  });

  it("a daemon that answers /api/version but refuses /api/pull is rate-limited, not hammered", async () => {
    // The pathological `runtime_unreachable` shape, and the one the happy-path
    // test above cannot see: the STATUS probe keeps answering "running" (the
    // version endpoint is up) while every pull ConnectErrors (a wedged daemon,
    // a restart in flight, an exhausted connection pool).
    //
    // `fail` re-probes itself, so arming the auto-start effect in the same tick
    // meant that probe answered "running" in the same microtask turn, the
    // effect re-opened the pull with no delay, and the new pull broke the same
    // way. Measured before the fix: 400+ pulls opened with ZERO wall-clock
    // elapsed, `blocked` still null, a spinner on screen. The transient lane
    // had a bounded delay; this one had none at all.
    let pulls = 0;
    const port: FirstRunLocalModelsPort = {
      status: () => Promise.resolve(RUNNING),
      list: () => Promise.resolve([]),
      pull: () => {
        pulls += 1;
        // A runaway guard, so a regression fails the assertion below instead
        // of hanging the suite (which is what it did when first reproduced).
        if (pulls > 500) throw new Error(`runaway: ${pulls} pulls`);
        return (async function* () {
          yield frame({
            sequence_no: 1,
            status: "error",
            error: "Ollama request failed: /api/pull",
            error_kind: "runtime_unreachable",
          });
        })();
      },
      startRuntime: () => Promise.resolve(RUNNING),
    };

    const { result } = mount(port);
    await settle();
    act(() => result.current.start());
    await settle(0);
    expect(pulls).toBe(1); // no free retry inside the failure's own turn

    // Re-armed on the same schedule the transient lane uses: 1s, then 2s…
    await settle(1_000);
    expect(pulls).toBe(2);
    await settle(2_000);
    expect(pulls).toBe(3);

    // …so a full minute of wall clock buys a handful of attempts, not
    // hundreds of thousands. This is the assertion the old code could not pass.
    await settle(60_000);
    expect(pulls).toBeGreaterThan(3); // still resuming on its own (§6)
    expect(pulls).toBeLessThanOrEqual(10);

    // And it is never hard-blocked: §6 requires a daemon that comes back to
    // resume the download by itself, however long it was down.
    expect(result.current.blocked).toBeNull();
    // Nor is the wait silent. Between attempts the card must not fall back to
    // state ②'s "Start download" — that would quietly un-start a download the
    // user did start. `reconnecting` says what is true and keeps the bar.
    expect(["downloading", "reconnecting"]).toContain(result.current.phase);
    expect(result.current.localModelPct).not.toBeNull();
  });

  it("a transient break reconnects with capped exponential backoff", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 2,
          status: "error",
          error: "stream ended early",
          error_kind: "transient",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.phase).toBe("reconnecting");
    expect(result.current.blocked).toBeNull();
    expect(result.current.localModelPct).toBe(25);

    // First retry after 1s, not before.
    await settle(FIRST_BACKOFF_MS - 1);
    expect(h.calls.pull).toBe(1);
    await settle(1);
    expect(h.calls.pull).toBe(2);
    expect(result.current.phase).toBe("downloading");

    // Second break backs off further (1s → 2s).
    await act(async () => {
      h.stream(1).push(
        frame({
          sequence_no: 3,
          status: "error",
          error: "stream ended early",
          error_kind: "transient",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.phase).toBe("reconnecting");
    await settle(FIRST_BACKOFF_MS);
    expect(h.calls.pull).toBe(2); // 1s is no longer enough
    await settle(FIRST_BACKOFF_MS);
    expect(h.calls.pull).toBe(3);
  });

  // --- the retry BUDGET (PRD-P8 §6) ---------------------------------------
  //
  // The backoff bounds the DELAY between retries; these bound the NUMBER of
  // them. Unbounded retry is the same dead end as a frozen bar — it just
  // animates: a permanently broken proxy (one that accepts the connection and
  // tears it down every time) reconnected forever and the user was never told.

  /** The delays `backoffDelayMs` produces, one per retry the budget allows. */
  const BACKOFF_SCHEDULE_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
  const MAX_TRANSIENT_RETRIES = BACKOFF_SCHEDULE_MS.length;

  /** Break pull #`index` the way a flaky link does: transient, no new bytes. */
  async function breakTransiently(h: Harness, index: number): Promise<void> {
    await act(async () => {
      h.stream(index).push(
        frame({
          sequence_no: 100 + index,
          status: "error",
          error: "stream ended early",
          error_kind: "transient",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  /** Spend the entire allowance without ever moving a byte forward. */
  async function burnTheBudget(h: Harness): Promise<void> {
    for (let attempt = 0; attempt < MAX_TRANSIENT_RETRIES; attempt += 1) {
      await breakTransiently(h, attempt);
      await settle(BACKOFF_SCHEDULE_MS[attempt]);
    }
  }

  it("spends the whole retry budget before involving the user — a real blip stays invisible", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    for (let attempt = 0; attempt < MAX_TRANSIENT_RETRIES; attempt += 1) {
      await breakTransiently(h, attempt);
      // Below the cap the hook absorbs the break: reconnecting, never blocked.
      expect(result.current.phase).toBe("reconnecting");
      expect(result.current.blocked).toBeNull();

      await settle(BACKOFF_SCHEDULE_MS[attempt]);
      expect(h.calls.pull).toBe(attempt + 2);
      expect(result.current.phase).toBe("downloading");
      expect(result.current.localModelPct).toBe(25); // nothing thrown away
    }
  });

  it("stops reconnecting AT the cap and says so — never an unbounded silent spin", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);
    await burnTheBudget(h);

    const pullsAtCap = h.calls.pull;
    expect(pullsAtCap).toBe(MAX_TRANSIENT_RETRIES + 1);

    // One more break, with nothing left to spend.
    await breakTransiently(h, MAX_TRANSIENT_RETRIES);

    expect(result.current.phase).not.toBe("reconnecting");
    expect(result.current.blocked).not.toBeNull();
    expect(result.current.blocked?.message).toBe(
      FIRST_RUN_COPY.local.retriesExhausted,
    );
    // The honesty requirement: whatever it says, it must be renderable and it
    // must not promise a reconnect that is no longer coming.
    expect(result.current.blocked?.message.trim().length).toBeGreaterThan(0);
    expect(result.current.blocked?.message).not.toMatch(/reconnect/i);
    expect(hasAWayForward(result.current)).toBe(true);
    expect(result.current.localModelPct).toBe(25);

    // And it really has stopped: no retry ever fires again, however long we
    // wait. This is the assertion the unbounded loop could not pass.
    await settle(FAST_POLL_WINDOW_MS + SLOW_POLL_MS);
    expect(h.calls.pull).toBe(pullsAtCap);

    // The manual escape (design state ④'s "Resume download") still works and
    // re-arms the allowance.
    act(() => result.current.resume());
    expect(result.current.blocked).toBeNull();
    expect(result.current.phase).toBe("downloading");
    expect(h.calls.pull).toBe(pullsAtCap + 1);
  });

  it("forward progress refunds the budget — a long flaky download that IS working is never hard-blocked", async () => {
    // A 4.3 GB pull over a bad link legitimately breaks far more than six
    // times while still finishing. Capping the download rather than the
    // consecutive-failure streak would be its own dishonest dead end.
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);
    await burnTheBudget(h);

    // The retry that finally downloads something NEW resets the allowance…
    await act(async () => {
      h.stream(MAX_TRANSIENT_RETRIES).push(
        frame({ sequence_no: 200, bytes_completed: 150, bytes_total: 200 }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.localModelPct).toBe(75);

    // …so the next break reconnects instead of blocking, and does so from the
    // FIRST backoff step rather than the ceiling.
    await breakTransiently(h, MAX_TRANSIENT_RETRIES);
    expect(result.current.blocked).toBeNull();
    expect(result.current.phase).toBe("reconnecting");
    await settle(BACKOFF_SCHEDULE_MS[0]);
    expect(result.current.phase).toBe("downloading");
    expect(h.calls.pull).toBe(MAX_TRANSIENT_RETRIES + 2);
  });

  it("re-delivering bytes it already had buys a broken stream no extra retries", async () => {
    // The refund is keyed on a high-water mark, not on "a frame arrived":
    // a server that replays the same prefix and dies every time is not making
    // progress, and must still hit the cap.
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h); // proves 50 bytes

    for (let attempt = 0; attempt < MAX_TRANSIENT_RETRIES; attempt += 1) {
      await act(async () => {
        h.stream(attempt).push(
          frame({
            sequence_no: 300 + attempt,
            bytes_completed: 50, // exactly what was already proved
            bytes_total: 200,
          }),
        );
        await vi.advanceTimersByTimeAsync(0);
      });
      await breakTransiently(h, attempt);
      await settle(BACKOFF_SCHEDULE_MS[attempt]);
    }

    await breakTransiently(h, MAX_TRANSIENT_RETRIES);
    expect(result.current.blocked).not.toBeNull();
    expect(result.current.phase).not.toBe("reconnecting");
  });

  it("a terminal failure blocks with the server's message, never auto-retries, and resume() is the way out", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 2,
          status: "error",
          error: "no space left on device",
          error_kind: "terminal",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.blocked).toEqual({
      kind: "terminal",
      message: "no space left on device",
    });
    expect(result.current.phase).not.toBe("downloading");
    expect(result.current.localModelPct).toBe(25);

    // No auto-retry, however long we wait.
    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.pull).toBe(1);

    // "Resume download" is the escape.
    act(() => result.current.resume());
    expect(result.current.blocked).toBeNull();
    expect(result.current.phase).toBe("downloading");
    expect(h.calls.pull).toBe(2);
  });

  it("resume() while the runtime is down stays armed and downloads once it returns — never a dead card", async () => {
    // Regression for the frozen dead end PRD-P8 exists to kill. The path:
    // a terminal failure blocks; its follow-up probe ALSO fails, so the
    // runtime degrades to "unknown"; the user presses "Resume download".
    // `resume()` used to clear `blocked` and disarm auto-start before a
    // `beginPull()` that early-returns on a non-running runtime — leaving no
    // download, no armed retry, and no button. Forever.
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    // The daemon goes down too; `fail()` re-probes, so the card lands on a
    // terminal block WITH a non-running runtime.
    h.setStatus(STOPPED);
    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 2,
          status: "error",
          error: "no space left on device",
          error_kind: "terminal",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    await settle();

    expect(result.current.blocked).not.toBeNull();
    expect(result.current.runtime).not.toBe("running");

    // The user acts on the only affordance the card offers.
    act(() => result.current.resume());
    await settle();

    // No new pull could start yet — but the request must NOT be forgotten.
    expect(h.calls.pull).toBe(1);

    // The daemon comes back. The download must begin by itself.
    h.setStatus(RUNNING);
    await settle(FAST_POLL_MS);
    await settle();

    expect(result.current.runtime).toBe("running");
    expect(h.calls.pull).toBe(2);
    expect(result.current.phase).toBe("downloading");
    expect(result.current.localModelPct).toBe(25); // resumed, nothing lost
  });

  it("an unclassified failure degrades to blocked rather than guessing a retry is safe", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    await act(async () => {
      // Older server: no `error_kind` at all.
      h.stream(0).push(
        frame({ sequence_no: 2, status: "error", error: "disk full" }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.blocked).toEqual({
      kind: "terminal",
      message: "disk full",
    });
    await settle(FAST_POLL_WINDOW_MS);
    expect(h.calls.pull).toBe(1);
  });

  it("a stream that ends with no terminal frame is treated as a break, not as success", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    await act(async () => {
      h.stream(0).end();
      await vi.advanceTimersByTimeAsync(0);
    });

    // Silently landing back on "idle" at 25% IS the frozen-progress bug.
    expect(result.current.phase).toBe("reconnecting");
    expect(result.current.localModelPct).toBe(25);
    await settle(FIRST_BACKOFF_MS);
    expect(h.calls.pull).toBe(2);
  });

  it("a frame that omits `error` entirely is progress, not an endless retry loop", async () => {
    const h = harness(RUNNING);
    const { result } = await downloadingAt25(h);

    await act(async () => {
      // The port's runtime guard only requires sequence_no/status/done, so a
      // truncated or legacy frame reaches the hook with NO `error` field. Read
      // as an error it would carry an `undefined` message, throw, and re-enter
      // the retry loop on the same frame forever.
      const partial = {
        sequence_no: 2,
        status: "pulling",
        bytes_completed: 150,
        bytes_total: 200,
        done: false,
      } as unknown as LocalModelPullEvent;
      h.stream(0).push(partial);
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.phase).toBe("downloading");
    expect(result.current.blocked).toBeNull();
    expect(result.current.localModelPct).toBe(75);
    await settle(FIRST_BACKOFF_MS * 4);
    expect(h.calls.pull).toBe(1); // no retry storm
  });

  it("unmounting mid-backoff schedules nothing further", async () => {
    const h = harness(RUNNING);
    const { unmount } = await downloadingAt25(h);

    await act(async () => {
      h.stream(0).push(
        frame({
          sequence_no: 2,
          status: "error",
          error: "stream ended early",
          error_kind: "transient",
        }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });

    const statusCalls = h.calls.status;
    unmount();
    await settle(FAST_POLL_WINDOW_MS + SLOW_POLL_MS);

    expect(h.calls.pull).toBe(1);
    expect(h.calls.status).toBe(statusCalls);
  });
});

// The invariant the whole PRD exists to enforce, asserted for every way a pull
// can break. Progress may pause, but the user must always be able to SEE that
// and DO something — never a frozen bar with no signal and no path forward.
describe("useFirstRunLocalModel — the no-silent-freeze invariant (PRD-P8 D1)", () => {
  const BREAKS: readonly {
    readonly name: string;
    readonly trigger: (stream: PullStream) => void;
  }[] = [
    {
      name: "runtime_unreachable",
      trigger: (s) =>
        s.push(
          frame({
            sequence_no: 2,
            status: "error",
            error: "connection refused",
            error_kind: "runtime_unreachable",
          }),
        ),
    },
    {
      name: "transient",
      trigger: (s) =>
        s.push(
          frame({
            sequence_no: 2,
            status: "error",
            error: "stream ended early",
            error_kind: "transient",
          }),
        ),
    },
    {
      name: "terminal",
      trigger: (s) =>
        s.push(
          frame({
            sequence_no: 2,
            status: "error",
            error: "no space left on device",
            error_kind: "terminal",
          }),
        ),
    },
    {
      name: "an error frame with no error_kind",
      trigger: (s) =>
        s.push(frame({ sequence_no: 2, status: "error", error: "boom" })),
    },
    {
      name: "an error frame with an empty message",
      trigger: (s) =>
        s.push(frame({ sequence_no: 2, status: "error", error: "   " })),
    },
    {
      name: "a stream that ends with no terminal frame",
      trigger: (s) => s.end(),
    },
    {
      name: "a stream that throws",
      trigger: (s) => s.fail(new Error("socket hang up")),
    },
  ];

  for (const brk of BREAKS) {
    it(`is progressing or blocked-with-a-message after ${brk.name} — never silently frozen`, async () => {
      const h = harness(RUNNING);
      const { result } = mount(h.port);
      await settle();
      act(() => result.current.start());
      await act(async () => {
        h.stream(0).push(
          frame({ sequence_no: 1, bytes_completed: 50, bytes_total: 200 }),
        );
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.localModelPct).toBe(25);

      await act(async () => {
        brk.trigger(h.stream(0));
        await vi.advanceTimersByTimeAsync(0);
      });

      const r = result.current;
      // The explicit negation of the bug: idle + nothing wrong + runtime fine.
      const silentlyFrozen =
        r.phase === "idle" && r.blocked === null && r.runtime === "running";
      expect(silentlyFrozen).toBe(false);
      expect(hasAWayForward(r)).toBe(true);
      // A block must always carry something renderable.
      if (r.blocked !== null) {
        expect(r.blocked.message.trim().length).toBeGreaterThan(0);
      }
      // And whatever had already downloaded is never thrown away.
      expect(r.localModelPct).not.toBeNull();
    });
  }
});

describe("useFirstRunLocalModel — runtime control (PRD-P8 §4.3)", () => {
  it("restartRuntime() is inert when this server cannot manage the runtime", async () => {
    const h = harness({ ...STOPPED, runtime_managed: false });
    const { result } = mount(h.port);
    await settle();

    expect(result.current.runtimeManaged).toBe(false);
    act(() => result.current.restartRuntime());
    await settle();

    expect(h.calls.startRuntime).toBe(0);
  });

  it("restartRuntime() starts the runtime and the download picks up from there", async () => {
    const h = harness(STOPPED);
    const { result } = mount(h.port);
    await settle();
    expect(result.current.runtimeManaged).toBe(true);
    expect(h.calls.pull).toBe(0);

    // The host process comes up as a result of the call.
    h.setStatus(RUNNING);
    await act(async () => {
      result.current.restartRuntime();
      await vi.advanceTimersByTimeAsync(0);
    });
    await settle();

    expect(h.calls.startRuntime).toBe(1);
    expect(result.current.runtime).toBe("running");
    expect(result.current.phase).toBe("downloading");
    expect(h.calls.pull).toBe(1);
  });

  it("a restart that fails to bring the runtime up leaves the card actionable, not frozen", async () => {
    const h = harness(STOPPED);
    const { result } = mount(h.port);
    await settle();

    await act(async () => {
      result.current.restartRuntime();
      await vi.advanceTimersByTimeAsync(0);
    });
    await settle();

    expect(h.calls.startRuntime).toBe(1);
    expect(result.current.runtime).toBe("stopped");
    expect(hasAWayForward(result.current)).toBe(true);
  });

  it("a blipped probe does not repaint a live download as runtime-down", async () => {
    const h = harness(RUNNING);
    const { result } = mount(h.port);
    await settle();
    act(() => result.current.start());
    expect(result.current.phase).toBe("downloading");

    h.setStatusThrows(true);
    act(() => result.current.recheck());
    await settle();

    expect(result.current.runtime).toBe("running");
    expect(result.current.phase).toBe("downloading");
  });
});
