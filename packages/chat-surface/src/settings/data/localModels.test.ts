// The local-models data seam builds the expected TypedRequest / SSE options and
// calls the injected Transport — proving there is no bare fetch / EventSource
// (chat-surface substrate boundary). Plus the SSE frame parsing + drop-malformed
// behaviour and the path-encoded delete (Ollama tags carry `/` and `:`).

import { describe, expect, it, vi } from "vitest";

import type { LocalModelPullEvent } from "@0x-copilot/api-types";

import type {
  SseSubscribeOptions,
  Transport,
  TypedRequest,
} from "../../ports/Transport";
import type { LocalModelPullHandlers } from "../DownloadLocalModelModal";
import {
  LOCAL_MODEL_CATALOG,
  LOCAL_MODEL_PULL_EVENT,
  QWEN3_4B_PRESET,
  createLocalModelsPort,
} from "./localModels";

function fakeTransport(handler: (req: TypedRequest) => unknown): {
  readonly transport: Transport;
  readonly calls: TypedRequest[];
  readonly sse: SseSubscribeOptions[];
  readonly sseClose: () => void;
} {
  const calls: TypedRequest[] = [];
  const sse: SseSubscribeOptions[] = [];
  const sseClose = vi.fn();
  const request = (async (req: TypedRequest) => {
    calls.push(req);
    return handler(req);
  }) as Transport["request"];
  const transport: Transport = {
    request,
    subscribeServerSentEvents: (opts: SseSubscribeOptions) => {
      sse.push(opts);
      return { close: sseClose };
    },
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, calls, sse, sseClose };
}

const frame = (
  over: Partial<LocalModelPullEvent> = {},
): LocalModelPullEvent => ({
  sequence_no: 1,
  status: "downloading",
  bytes_total: 800,
  bytes_completed: 400,
  speed_bps: 10,
  eta_seconds: 40,
  done: false,
  error: null,
  ...over,
});

describe("createLocalModelsPort", () => {
  it("status GETs /v1/local-models/status", async () => {
    const { transport, calls } = fakeTransport(() => ({
      enabled: true,
      ollama_running: false,
      ollama_version: null,
    }));
    const status = await createLocalModelsPort(transport).status();
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/local-models/status",
    });
    expect(status.ollama_running).toBe(false);
  });

  it("list GETs /v1/local-models and unwraps .models", async () => {
    const { transport, calls } = fakeTransport(() => ({
      models: [
        {
          name: "hf.co/bartowski/phi-4-GGUF:Q4_K_M",
          size_bytes: 9_050_000_000,
          quantization: "Q4_K_M",
          parameter_size: "14B",
          run_placement: null,
        },
      ],
    }));
    const models = await createLocalModelsPort(transport).list();
    expect(calls[0]).toMatchObject({ method: "GET", path: "/v1/local-models" });
    expect(models).toHaveLength(1);
    expect(models[0].name).toContain("phi-4");
  });

  it("size GETs /v1/local-models/size with repo+quant query", async () => {
    const { transport, calls } = fakeTransport(() => ({
      repo: "bartowski/phi-4-GGUF",
      quant: "Q4_K_M",
      filename: "phi-4-Q4_K_M.gguf",
      size_bytes: 9_050_000_000,
    }));
    const size = await createLocalModelsPort(transport).size(
      "bartowski/phi-4-GGUF",
      "Q4_K_M",
    );
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/local-models/size",
      query: { repo: "bartowski/phi-4-GGUF", quant: "Q4_K_M" },
    });
    expect(size.size_bytes).toBe(9_050_000_000);
  });

  it("remove DELETEs and path-encodes the Ollama tag (/ and :)", async () => {
    const { transport, calls } = fakeTransport(() => undefined);
    await createLocalModelsPort(transport).remove(
      "hf.co/bartowski/phi-4-GGUF:Q4_K_M",
    );
    expect(calls[0]).toMatchObject({
      method: "DELETE",
      path: "/v1/local-models/hf.co%2Fbartowski%2Fphi-4-GGUF%3AQ4_K_M",
    });
  });

  it("pull subscribes to the local_model_pull SSE with repo+quant and parses frames", () => {
    const { transport, sse } = fakeTransport(() => undefined);
    const onEvent = vi.fn();
    const onError = vi.fn();
    const handlers: LocalModelPullHandlers = { onEvent, onError };
    const handle = createLocalModelsPort(transport).pull(
      "bartowski/phi-4-GGUF",
      "Q4_K_M",
      handlers,
    );
    expect(sse[0]).toMatchObject({
      path: "/v1/local-models/pull",
      query: { repo: "bartowski/phi-4-GGUF", quant: "Q4_K_M" },
      eventName: LOCAL_MODEL_PULL_EVENT,
    });

    // A well-formed frame is forwarded.
    sse[0].onMessage(JSON.stringify(frame({ bytes_completed: 500 })));
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].bytes_completed).toBe(500);

    // A malformed frame is dropped, not thrown, and keeps the stream alive.
    sse[0].onMessage("not json");
    sse[0].onMessage(JSON.stringify({ nope: true }));
    expect(onEvent).toHaveBeenCalledTimes(1);

    // The handle closes the underlying subscription.
    expect(typeof handle.close).toBe("function");
    handle.close();
  });

  it("pull forwards a transport SSE error to onError", () => {
    const { transport, sse } = fakeTransport(() => undefined);
    const onError = vi.fn();
    createLocalModelsPort(transport).pull("r", "q", {
      onEvent: vi.fn(),
      onError,
    });
    sse[0].onError?.(new Error("socket closed"));
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
  });
});

describe("LOCAL_MODEL_CATALOG", () => {
  it("offers a curated set of HF GGUF repos with a default quant", () => {
    expect(LOCAL_MODEL_CATALOG.length).toBeGreaterThanOrEqual(5);
    for (const model of LOCAL_MODEL_CATALOG) {
      expect(model.repo).toMatch(/GGUF$/i);
      expect(model.quant).toBeTruthy();
      expect(model.name).toBeTruthy();
    }
    // Includes the flagship large model called out in the spec.
    expect(
      LOCAL_MODEL_CATALOG.some((m) => m.name.includes("Llama 3.3 70B")),
    ).toBe(true);
  });

  it("is deduped by Ollama install tag (repo + quant)", () => {
    const tags = LOCAL_MODEL_CATALOG.map((m) => `${m.repo}:${m.quant}`);
    expect(new Set(tags).size).toBe(tags.length);
  });

  it("includes the FTUE first-run default as a member (no drift)", () => {
    // The onboarding gate and the Settings modal read ONE curated list; the
    // first-run default must be a member so neither surface loses it.
    expect(LOCAL_MODEL_CATALOG).toContain(QWEN3_4B_PRESET);
  });
});

describe("QWEN3_4B_PRESET", () => {
  it("pins the real Qwen3-4B GGUF repo, quant, and verified byte size", () => {
    expect(QWEN3_4B_PRESET.repo).toBe("Qwen/Qwen3-4B-GGUF");
    expect(QWEN3_4B_PRESET.quant).toBe("Q8_0");
    expect(QWEN3_4B_PRESET.name).toBe("Qwen 3 4B");
    expect(QWEN3_4B_PRESET.parameterSize).toBe("4B");
    // Verified Hugging Face Q8_0 byte count (Qwen3-4B-Q8_0.gguf).
    expect(QWEN3_4B_PRESET.sizeBytes).toBe(4_280_404_704);
  });

  it("resolves to a valid Ollama HF pull tag", () => {
    expect(`hf.co/${QWEN3_4B_PRESET.repo}:${QWEN3_4B_PRESET.quant}`).toBe(
      "hf.co/Qwen/Qwen3-4B-GGUF:Q8_0",
    );
  });
});
