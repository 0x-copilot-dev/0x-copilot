import { describe, expect, it, vi } from "vitest";

import type {
  LocalModelPullEvent,
  LocalModelsListResponse,
  LocalModelsStatus,
} from "@0x-copilot/api-types";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "../ports/Transport";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { createFirstRunLocalModelsPort } from "./localModelsPort";

const PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  sizeBytes: 4_280_404_704,
};

function pullFrame(over: Partial<LocalModelPullEvent>): LocalModelPullEvent {
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

interface FakeSse {
  readonly opts: SseSubscribeOptions;
  readonly close: ReturnType<typeof vi.fn>;
}

function fakeTransport(over: {
  request?: <T>(req: TypedRequest) => Promise<T>;
  sinks?: FakeSse[];
}): Transport {
  return {
    request: (over.request ??
      (() => Promise.reject(new Error("no request")))) as <T>(
      req: TypedRequest,
    ) => Promise<T>,
    subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
      const close = vi.fn();
      over.sinks?.push({ opts, close });
      return { close };
    },
    getSession(): Session {
      return { bearer: null };
    },
    capabilities(): TransportCapabilities {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
  };
}

describe("createFirstRunLocalModelsPort", () => {
  it("status() delegates to GET /v1/local-models/status", async () => {
    const request = vi.fn(async () => ({
      enabled: true,
      ollama_running: true,
      ollama_version: "0.1",
    })) as unknown as <T>(req: TypedRequest) => Promise<T>;
    const port = createFirstRunLocalModelsPort(fakeTransport({ request }));
    const status = (await port.status()) as LocalModelsStatus;
    expect(status.enabled).toBe(true);
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        path: "/v1/local-models/status",
      }),
    );
  });

  it("list() unwraps the models array from GET /v1/local-models", async () => {
    const body: LocalModelsListResponse = {
      models: [
        {
          name: "hf.co/Qwen/Qwen3-4B-GGUF:Q8_0",
          size_bytes: 1,
          quantization: null,
          parameter_size: null,
          run_placement: null,
        },
      ],
    };
    const request = vi.fn(async () => body) as unknown as <T>(
      req: TypedRequest,
    ) => Promise<T>;
    const port = createFirstRunLocalModelsPort(fakeTransport({ request }));
    const models = await port.list();
    expect(models).toHaveLength(1);
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "GET", path: "/v1/local-models" }),
    );
  });

  it("startRuntime() POSTs /v1/local-models/runtime/start and returns the status", async () => {
    const request = vi.fn(async () => ({
      enabled: true,
      ollama_running: true,
      ollama_version: "0.6.2",
      runtime_state: "running",
      runtime_managed: true,
    })) as unknown as <T>(req: TypedRequest) => Promise<T>;
    const port = createFirstRunLocalModelsPort(fakeTransport({ request }));

    const status = await port.startRuntime();

    expect(status.runtime_state).toBe("running");
    expect(status.runtime_managed).toBe(true);
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/v1/local-models/runtime/start",
      }),
    );
  });

  it("startRuntime() forwards the abort signal and propagates a 404 gate", async () => {
    const controller = new AbortController();
    const request = vi.fn(() =>
      Promise.reject(new Error("404 CONFIGURATION_ERROR")),
    ) as unknown as <T>(req: TypedRequest) => Promise<T>;
    const port = createFirstRunLocalModelsPort(fakeTransport({ request }));

    await expect(port.startRuntime(controller.signal)).rejects.toThrow(
      "CONFIGURATION_ERROR",
    );
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("pull() opens the SSE lane with repo/quant and yields parsed frames until done", async () => {
    const sinks: FakeSse[] = [];
    const port = createFirstRunLocalModelsPort(fakeTransport({ sinks }));

    const received: LocalModelPullEvent[] = [];
    const consume = (async () => {
      for await (const frame of port.pull(PRESET)) received.push(frame);
    })();

    // The generator subscribes lazily on first `next()`; wait a tick.
    await Promise.resolve();
    await Promise.resolve();
    expect(sinks).toHaveLength(1);
    expect(sinks[0].opts.path).toBe("/v1/local-models/pull");
    expect(sinks[0].opts.query).toEqual({
      repo: PRESET.repo,
      quant: PRESET.quant,
    });

    const { onMessage } = sinks[0].opts;
    onMessage(
      JSON.stringify(pullFrame({ sequence_no: 1, bytes_completed: 10 })),
    );
    onMessage("not json"); // dropped, does not tear the stream down
    onMessage(
      JSON.stringify(
        pullFrame({ sequence_no: 2, done: true, status: "success" }),
      ),
    );

    await consume;
    expect(received.map((f) => f.sequence_no)).toEqual([1, 2]);
    expect(sinks[0].close).toHaveBeenCalledTimes(1);
  });

  it("pull() does not treat a frame with NO `error` key as a terminal error", async () => {
    const sinks: FakeSse[] = [];
    const port = createFirstRunLocalModelsPort(fakeTransport({ sinks }));

    const received: LocalModelPullEvent[] = [];
    const consume = (async () => {
      for await (const frame of port.pull(PRESET)) received.push(frame);
    })();

    await Promise.resolve();
    await Promise.resolve();
    const { onMessage } = sinks[0].opts;
    // `isLocalModelPullEvent` admits this (only sequence_no/status/done are
    // required), so a truncated or legacy frame reaches the consumer with
    // `error === undefined`. Closing the stream on it would end a healthy pull.
    onMessage(
      JSON.stringify({ sequence_no: 1, status: "pulling", done: false }),
    );
    onMessage(
      JSON.stringify(
        pullFrame({ sequence_no: 2, done: true, status: "success" }),
      ),
    );

    await consume;
    expect(received.map((f) => f.sequence_no)).toEqual([1, 2]);
  });

  it("pull() throws when the transport reports an SSE error", async () => {
    const sinks: FakeSse[] = [];
    const port = createFirstRunLocalModelsPort(fakeTransport({ sinks }));

    const consume = (async () => {
      for await (const _ of port.pull(PRESET)) void _;
    })();

    await Promise.resolve();
    await Promise.resolve();
    sinks[0].opts.onError?.(new Error("stream dropped"));

    await expect(consume).rejects.toThrow("stream dropped");
    expect(sinks[0].close).toHaveBeenCalledTimes(1);
  });

  it("pull() ends and closes the subscription when the signal aborts", async () => {
    const sinks: FakeSse[] = [];
    const port = createFirstRunLocalModelsPort(fakeTransport({ sinks }));
    const controller = new AbortController();

    const consume = (async () => {
      for await (const _ of port.pull(PRESET, controller.signal)) void _;
    })();

    await Promise.resolve();
    await Promise.resolve();
    controller.abort();

    await consume; // resolves (no throw) on abort
    expect(sinks[0].close).toHaveBeenCalledTimes(1);
  });
});
