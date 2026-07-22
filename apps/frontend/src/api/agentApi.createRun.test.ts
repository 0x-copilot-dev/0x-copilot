// Wire-contract test for `createRun` — Phase 1 P1-C (chats-canvas-prd
// §16). Confirms that `reasoning_depth` rides as a top-level field on
// `CreateRunRequest`, NOT folded into the model selection's
// `reasoning.effort` via the legacy `applyDepth(model, depth)` hack.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createRun } from "./agentApi";

let lastBody: Record<string, unknown> | null = null;

function stubOkFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      lastBody = typeof init?.body === "string" ? JSON.parse(init.body) : null;
      return new Response(
        JSON.stringify({
          run_id: "run_001",
          conversation_id: "conv_001",
          user_message_id: "msg_001",
          trace_id: "trace_001",
          status: "queued",
          stream_url: "/v1/agent/runs/run_001/stream",
          events_url: "/v1/agent/runs/run_001/events",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }),
  );
}

beforeEach(() => {
  lastBody = null;
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createRun — reasoning_depth wire field (P1-C §16)", () => {
  it("sends reasoning_depth at the top level when an option is provided", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      { reasoningDepth: "fast" },
    );
    expect(lastBody).not.toBeNull();
    expect(lastBody?.reasoning_depth).toBe("fast");
  });

  it("sends reasoning_depth: null when no depth is provided (runtime default)", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      {},
    );
    expect(lastBody).not.toBeNull();
    expect(lastBody?.reasoning_depth).toBeNull();
  });

  it("does NOT fold depth into the model selection (no applyDepth hack)", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      {
        reasoningDepth: "deep",
        // Pass a model selection with a null reasoning slot. The new
        // wire path must NOT mutate the model selection — depth flows
        // on the top-level field exclusively.
        model: {
          provider: "openai",
          model_name: "gpt-5.4-mini",
          reasoning: null,
        },
      },
    );
    expect(lastBody).not.toBeNull();
    expect(lastBody?.reasoning_depth).toBe("deep");
    expect(lastBody?.model).toMatchObject({
      provider: "openai",
      model_name: "gpt-5.4-mini",
      reasoning: null,
    });
  });

  it("accepts each ReasoningDepth literal", async () => {
    stubOkFetch();
    for (const depth of ["fast", "balanced", "deep"] as const) {
      lastBody = null;
      await createRun(
        "conv_001",
        "hello",
        { orgId: "org_001", userId: "user_001" },
        { reasoningDepth: depth },
      );
      const body = lastBody as Record<string, unknown> | null;
      expect(body?.reasoning_depth).toBe(depth);
    }
  });
});

describe("createRun — composer Tools popover (web-search + connector scopes)", () => {
  it("sends web_search_enabled: false ONLY on an explicit opt-out", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      { webSearchEnabled: false },
    );
    expect(lastBody?.web_search_enabled).toBe(false);
  });

  it("omits web_search_enabled when enabled (runtime default is on)", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      { webSearchEnabled: true },
    );
    expect(lastBody).not.toBeNull();
    expect(lastBody).not.toHaveProperty("web_search_enabled");
  });

  it("maps active connectorScopes onto request_context.connector_scopes", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      { connectorScopes: { "srv-1": [], "srv-2": ["read"] } },
    );
    expect(lastBody?.request_context).toEqual({
      connector_scopes: { "srv-1": [], "srv-2": ["read"] },
    });
  });

  it("omits request_context when no connectors are active (empty map)", async () => {
    stubOkFetch();
    await createRun(
      "conv_001",
      "hello",
      { orgId: "org_001", userId: "user_001" },
      { connectorScopes: {} },
    );
    expect(lastBody).not.toHaveProperty("request_context");
  });

  it("omits request_context when connectorScopes is not provided", async () => {
    stubOkFetch();
    await createRun("conv_001", "hello", {
      orgId: "org_001",
      userId: "user_001",
    });
    expect(lastBody).not.toHaveProperty("request_context");
  });
});
