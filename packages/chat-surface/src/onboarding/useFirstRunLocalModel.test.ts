// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  LocalModelPullEvent,
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import type { FirstRunLocalModelsPort } from "./localModelsPort";
import { useFirstRunLocalModel } from "./useFirstRunLocalModel";

const PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  sizeBytes: 4_280_404_704,
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

/** A hand-driven pull stream so the test controls each frame's timing. */
function makeStream() {
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
          if (f.done || f.error !== null) return;
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
    push: (f: LocalModelPullEvent) => {
      queue.push(f);
      notify();
    },
    fail: (e: Error) => {
      failure = e;
      notify();
    },
  };
}

interface FakePort extends FirstRunLocalModelsPort {
  readonly pullCalls: number;
}

function fakePort(cfg: {
  status: () => LocalModelsStatus;
  models?: readonly LocalModelSummary[];
  stream?: AsyncIterable<LocalModelPullEvent>;
}): FakePort {
  let pullCalls = 0;
  const port = {
    status: () => Promise.resolve(cfg.status()),
    list: () => Promise.resolve(cfg.models ?? []),
    pull: () => {
      pullCalls += 1;
      return cfg.stream ?? makeStream().iterable;
    },
    get pullCalls() {
      return pullCalls;
    },
  };
  return port as unknown as FakePort;
}

describe("useFirstRunLocalModel", () => {
  it("probes on mount and enables the Start path when Ollama is running", async () => {
    const port = fakePort({
      status: () => ({
        enabled: true,
        ollama_running: true,
        ollama_version: "1",
      }),
    });
    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET }),
    );
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.enabled).toBe(true);
    expect(result.current.ollamaRunning).toBe(true);
    expect(result.current.disabled).toBe(false);
    expect(result.current.localModelPct).toBeNull();
  });

  it("keeps Start inert when the feature is disabled (web/cloud)", async () => {
    const port = fakePort({
      status: () => ({
        enabled: false,
        ollama_running: false,
        ollama_version: null,
      }),
    });
    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET }),
    );
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.disabled).toBe(true);
    act(() => result.current.start());
    expect(port.pullCalls).toBe(0);
    expect(result.current.status).toBe("idle");
  });

  it("reports Ollama-not-running and keeps Start inert", async () => {
    const port = fakePort({
      status: () => ({
        enabled: true,
        ollama_running: false,
        ollama_version: null,
      }),
    });
    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET }),
    );
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.ollamaRunning).toBe(false);
    expect(result.current.disabled).toBe(true);
    act(() => result.current.start());
    expect(port.pullCalls).toBe(0);
  });

  it("drives pct from frames and flips to ready + onReady(tag) on done", async () => {
    const stream = makeStream();
    const onReady = vi.fn();
    const port = fakePort({
      status: () => ({
        enabled: true,
        ollama_running: true,
        ollama_version: "1",
      }),
      models: [
        {
          name: "hf.co/Qwen/Qwen3-4B-GGUF:Q8_0",
          size_bytes: 1,
          quantization: null,
          parameter_size: null,
          run_placement: null,
        },
      ],
      stream: stream.iterable,
    });
    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET, onReady }),
    );
    await waitFor(() => expect(result.current.disabled).toBe(false));

    act(() => result.current.start());
    expect(result.current.status).toBe("downloading");
    expect(result.current.localModelPct).toBe(2); // SPEC seed

    await act(async () => {
      stream.push(
        frame({ sequence_no: 1, bytes_completed: 50, bytes_total: 200 }),
      );
    });
    await waitFor(() => expect(result.current.localModelPct).toBe(25));

    await act(async () => {
      stream.push(frame({ sequence_no: 2, done: true, status: "success" }));
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.localModelPct).toBe(100);
    expect(result.current.modelName).toBe("hf.co/Qwen/Qwen3-4B-GGUF:Q8_0");
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(onReady).toHaveBeenCalledWith("hf.co/Qwen/Qwen3-4B-GGUF:Q8_0");
  });

  it("surfaces an error frame and retries the pull", async () => {
    const first = makeStream();
    const second = makeStream();
    const streams = [first.iterable, second.iterable];
    let idx = 0;
    // A bespoke port that hands out a fresh stream on each pull.
    const port: FirstRunLocalModelsPort = {
      status: () =>
        Promise.resolve({
          enabled: true,
          ollama_running: true,
          ollama_version: "1",
        }),
      list: () => Promise.resolve([]),
      pull: () => streams[idx++] ?? makeStream().iterable,
    };

    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET }),
    );
    await waitFor(() => expect(result.current.disabled).toBe(false));

    act(() => result.current.start());
    await act(async () => {
      first.push(frame({ sequence_no: 1, error: "disk full", done: true }));
    });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("disk full");

    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe("downloading"));
    expect(idx).toBe(2); // a second pull was opened
  });

  it("does not fire onReady after unmount", async () => {
    const stream = makeStream();
    const onReady = vi.fn();
    const port = fakePort({
      status: () => ({
        enabled: true,
        ollama_running: true,
        ollama_version: "1",
      }),
      stream: stream.iterable,
    });
    const { result, unmount } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET, onReady }),
    );
    await waitFor(() => expect(result.current.disabled).toBe(false));
    act(() => result.current.start());

    unmount();
    await act(async () => {
      stream.push(frame({ sequence_no: 1, done: true }));
    });
    expect(onReady).not.toHaveBeenCalled();
  });

  it("re-probes on recheck() and flips disabled off when Ollama comes up", async () => {
    let running = false;
    const port = fakePort({
      status: () => ({
        enabled: true,
        ollama_running: running,
        ollama_version: running ? "1" : null,
      }),
    });
    const { result } = renderHook(() =>
      useFirstRunLocalModel({ port, preset: PRESET }),
    );
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.disabled).toBe(true);

    running = true;
    act(() => result.current.recheck());
    await waitFor(() => expect(result.current.disabled).toBe(false));
    expect(result.current.ollamaRunning).toBe(true);
  });
});
