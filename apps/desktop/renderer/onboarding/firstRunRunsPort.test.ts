// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "@0x-copilot/chat-transport";
import type {
  ModelSelectionRequest,
  RunAttachmentRequest,
} from "@0x-copilot/api-types";

import { createFirstRunRunsPort } from "./firstRunRunsPort";

interface Recorded {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

/**
 * Fake Transport routing `request` by path — the port only uses `request`, so
 * the SSE/session/capabilities members are unused stubs.
 */
function fakeTransport(byPath: Record<string, unknown>): {
  readonly transport: Transport;
  readonly calls: Recorded[];
} {
  const calls: Recorded[] = [];
  const request = vi.fn(async (req: TypedRequest) => {
    calls.push({ method: req.method, path: req.path, body: req.body });
    if (!(req.path in byPath)) {
      throw new Error(`unexpected path ${req.path}`);
    }
    return byPath[req.path];
  });
  const transport = { request } as unknown as Transport;
  return { transport, calls };
}

describe("createFirstRunRunsPort", () => {
  it("issues the two POSTs in order and returns {conversationId, runId}", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": { conversation_id: "conv_1" },
      "/v1/agent/runs": { run_id: "run_1" },
    });
    const port = createFirstRunRunsPort(transport);

    const result = await port.createFirstRun({
      userInput: "watch my wallet",
      model: null,
      webSearchEnabled: true,
    });

    expect(calls).toHaveLength(2);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].path).toBe("/v1/agent/conversations");
    expect(calls[0].body).toEqual({ title: "watch my wallet" });
    expect(calls[1].method).toBe("POST");
    expect(calls[1].path).toBe("/v1/agent/runs");
    expect(calls[1].body).toEqual({
      conversation_id: "conv_1",
      user_input: "watch my wallet",
      web_search_enabled: true,
    });
    expect(result).toEqual({ conversationId: "conv_1", runId: "run_1" });
  });

  it("includes model + attachments in the run body only when present", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": { conversation_id: "conv_2" },
      "/v1/agent/runs": { run_id: "run_2" },
    });
    const model: ModelSelectionRequest = {
      provider: "ollama",
      model_name: "qwen3:4b",
    };
    const attachments: RunAttachmentRequest[] = [
      {
        id: "a1",
        type: "text/csv",
        name: "airdrop-claims.csv",
        content_type: "text/csv",
        size: 10,
        content: [{ type: "text", text: "address,token" }],
      },
    ];

    await createFirstRunRunsPort(transport).createFirstRun({
      userInput: "explain this csv",
      model,
      attachments,
      webSearchEnabled: true,
    });

    expect(calls[1].body).toEqual({
      conversation_id: "conv_2",
      user_input: "explain this csv",
      model,
      attachments,
      web_search_enabled: true,
    });
  });

  it("threads web_search_enabled=false + request_context.connector_scopes (P4)", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": { conversation_id: "conv_p4" },
      "/v1/agent/runs": { run_id: "run_p4" },
    });

    await createFirstRunRunsPort(transport).createFirstRun({
      userInput: "no web, sheets on",
      model: null,
      webSearchEnabled: false,
      connectorScopes: { "seed:sheets": [] },
    });

    expect(calls[1].body).toEqual({
      conversation_id: "conv_p4",
      user_input: "no web, sheets on",
      web_search_enabled: false,
      request_context: { connector_scopes: { "seed:sheets": [] } },
    });
  });

  it("omits request_context when no connectors are active (P4)", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": { conversation_id: "conv_empty" },
      "/v1/agent/runs": { run_id: "run_empty" },
    });

    await createFirstRunRunsPort(transport).createFirstRun({
      userInput: "just web",
      model: null,
      webSearchEnabled: true,
      connectorScopes: {},
    });

    expect(calls[1].body).toEqual({
      conversation_id: "conv_empty",
      user_input: "just web",
      web_search_enabled: true,
    });
  });

  it("falls back to a neutral title for an attachment-only (empty text) send", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": { conversation_id: "conv_3" },
      "/v1/agent/runs": { run_id: "run_3" },
    });

    await createFirstRunRunsPort(transport).createFirstRun({
      userInput: "   ",
      model: null,
      webSearchEnabled: true,
    });

    expect(calls[0].body).toEqual({ title: "First run" });
  });

  it("throws (no run POST) when the conversation create returns no id", async () => {
    const { transport, calls } = fakeTransport({
      "/v1/agent/conversations": {},
      "/v1/agent/runs": { run_id: "unused" },
    });

    await expect(
      createFirstRunRunsPort(transport).createFirstRun({
        userInput: "hi",
        model: null,
        webSearchEnabled: true,
      }),
    ).rejects.toThrow(/conversation create returned no conversation_id/);
    // Only the conversation POST was attempted — the run POST never fired.
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/v1/agent/conversations");
  });
});
