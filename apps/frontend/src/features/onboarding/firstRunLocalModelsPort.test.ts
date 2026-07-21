// Web FirstRunLocalModelsPort — thin adapter over api/localModelsApi, plus the
// callback-SSE → AsyncIterable bridge in `pull`.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { QWEN3_4B_PRESET } from "@0x-copilot/chat-surface";
import type {
  LocalModelPullEvent,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

vi.mock("../../api/localModelsApi", () => ({
  getLocalModelsStatus: vi.fn(),
  listLocalModels: vi.fn(),
  streamLocalModelPull: vi.fn(),
}));

import {
  getLocalModelsStatus,
  listLocalModels,
  streamLocalModelPull,
} from "../../api/localModelsApi";
import { createFirstRunLocalModelsPort } from "./firstRunLocalModelsPort";

const flush = (): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, 0));

function frame(overrides: Partial<LocalModelPullEvent>): LocalModelPullEvent {
  return {
    sequence_no: 1,
    status: "downloading",
    bytes_total: 100,
    bytes_completed: 50,
    speed_bps: null,
    eta_seconds: null,
    done: false,
    error: null,
    ...overrides,
  };
}

describe("createFirstRunLocalModelsPort", () => {
  beforeEach(() => {
    vi.mocked(getLocalModelsStatus).mockReset();
    vi.mocked(listLocalModels).mockReset();
    vi.mocked(streamLocalModelPull).mockReset();
  });

  it("status() delegates to getLocalModelsStatus", async () => {
    const status = {
      enabled: true,
      ollama_running: true,
    } as LocalModelsStatus;
    vi.mocked(getLocalModelsStatus).mockResolvedValue(status);
    await expect(createFirstRunLocalModelsPort().status()).resolves.toBe(
      status,
    );
  });

  it("list() returns the models array from the list response", async () => {
    vi.mocked(listLocalModels).mockResolvedValue({
      models: [{ name: "qwen3:4b" }],
    } as never);
    await expect(createFirstRunLocalModelsPort().list()).resolves.toEqual([
      { name: "qwen3:4b" },
    ]);
  });

  it("pull() bridges SSE frames into an async iterable and stops on done", async () => {
    let onEvent!: (event: LocalModelPullEvent) => void;
    const close = vi.fn();
    vi.mocked(streamLocalModelPull).mockImplementation((opts) => {
      onEvent = opts.onEvent;
      return { close };
    });

    const port = createFirstRunLocalModelsPort();
    const collected: LocalModelPullEvent[] = [];
    const consuming = (async () => {
      for await (const f of port.pull(QWEN3_4B_PRESET)) collected.push(f);
    })();

    await flush();
    expect(streamLocalModelPull).toHaveBeenCalledWith(
      expect.objectContaining({
        repo: QWEN3_4B_PRESET.repo,
        quant: QWEN3_4B_PRESET.quant,
      }),
    );

    onEvent(frame({ sequence_no: 1, done: false }));
    onEvent(frame({ sequence_no: 2, status: "success", done: true }));
    await consuming;

    expect(collected.map((f) => f.sequence_no)).toEqual([1, 2]);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("pull() rejects and closes the subscription on a stream error", async () => {
    let onError!: (err: Error) => void;
    const close = vi.fn();
    vi.mocked(streamLocalModelPull).mockImplementation((opts) => {
      // The port always supplies onError.
      onError = opts.onError as (err: Error) => void;
      return { close };
    });

    const port = createFirstRunLocalModelsPort();
    const consuming = (async () => {
      for await (const _f of port.pull(QWEN3_4B_PRESET)) {
        /* drain */
      }
    })();

    await flush();
    onError(new Error("stream boom"));

    await expect(consuming).rejects.toThrow("stream boom");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("pull() stops (and closes) when the signal aborts", async () => {
    const close = vi.fn();
    vi.mocked(streamLocalModelPull).mockImplementation(() => ({ close }));

    const controller = new AbortController();
    const port = createFirstRunLocalModelsPort();
    const collected: LocalModelPullEvent[] = [];
    const consuming = (async () => {
      for await (const f of port.pull(QWEN3_4B_PRESET, controller.signal)) {
        collected.push(f);
      }
    })();

    await flush();
    controller.abort();
    await consuming;

    expect(collected).toHaveLength(0);
    expect(close).toHaveBeenCalledTimes(1);
  });
});
