// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  BrowserBroker,
  BROWSER_BROKER_PROTOCOL,
  type BrowserWorkerPort,
} from "./browser-broker";
import {
  BROWSER_BROKER_AUDIENCE,
  BrowserActionClass,
  BrowserProfileMode,
  type BrowserActionRequest,
  type BrowserActionResult,
} from "./protocol";
import { BROWSER_TOOL_SCHEMAS, type BrowserToolSchema } from "./tool-schemas";

class FakeWorker implements BrowserWorkerPort {
  lastRequest: BrowserActionRequest | null = null;
  listTools(): Promise<readonly BrowserToolSchema[]> {
    return Promise.resolve(BROWSER_TOOL_SCHEMAS);
  }
  dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    this.lastRequest = request;
    return Promise.resolve({
      version: 1,
      requestId: request.requestId,
      sessionId: "ses",
      actionId: "act",
      status: "succeeded",
      safeSummary: "ok",
      artifactRefs: [],
    });
  }
}

const NOW = 1000;

function actionRequest(): BrowserActionRequest {
  return {
    version: 1,
    requestId: "rq",
    binding: {
      version: 1,
      runId: "run-1",
      workspaceId: "ws",
      profileId: "prf",
      profileMode: BrowserProfileMode.Ephemeral,
      approvalId: "ap",
      originPolicy: {
        version: 1,
        topLevelOrigins: ["https://example.com"],
        subresourceOrigins: [],
        denyPrivateNetworks: true,
        serviceWorkers: "block",
      },
      expiresAt: "2099-01-01T00:00:00Z",
      nonce: "n",
    },
    actionClass: BrowserActionClass.Navigate,
    toolName: "browser_navigate",
    arguments: { url: "https://example.com" },
    deadlineMs: 5000,
  };
}

describe("BrowserBroker", () => {
  let broker: BrowserBroker;
  let worker: FakeWorker;
  let baseUrl: string;
  let token: string;

  const H = (extra: Record<string, string> = {}): Record<string, string> => ({
    authorization: `Bearer ${token}`,
    "x-browser-protocol": BROWSER_BROKER_PROTOCOL,
    "content-type": "application/json",
    ...extra,
  });

  const envelope = (overrides: Record<string, unknown> = {}): string =>
    JSON.stringify({
      aud: BROWSER_BROKER_AUDIENCE,
      nonce: `nonce-${Math.random()}`,
      requestId: `rid-${Math.random()}`,
      expiresAt: NOW + 10_000,
      ...overrides,
    });

  beforeEach(async () => {
    worker = new FakeWorker();
    broker = new BrowserBroker({ worker, now: () => NOW });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("rejects a request with no bearer", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/handshake`, {
      method: "POST",
      headers: { "x-browser-protocol": BROWSER_BROKER_PROTOCOL },
    });
    expect(res.status).toBe(401);
  });

  it("rejects an unsupported protocol version", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/handshake`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "x-browser-protocol": "999",
      },
    });
    expect(res.status).toBe(400);
  });

  it("rejects a browser (CORS) caller by fetch metadata", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/handshake`, {
      method: "POST",
      headers: H({ origin: "https://example.com" }),
    });
    expect(res.status).toBe(403);
  });

  it("handshakes and advertises the audience", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/handshake`, {
      method: "POST",
      headers: H(),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.audience).toBe(BROWSER_BROKER_AUDIENCE);
  });

  it("lists only the read-only tools", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body: envelope(),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    const names = body.tools.map((t: { name: string }) => t.name);
    expect(names).toContain("browser_navigate");
    expect(names).not.toContain("browser_submit");
    expect(names).not.toContain("browser_download");
  });

  it("rejects a wrong audience", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body: envelope({ aud: "some-other-audience" }),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("wrong_audience");
  });

  it("rejects an expired envelope", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body: envelope({ expiresAt: NOW - 1 }),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("expired");
  });

  it("rejects a replayed nonce", async () => {
    const body = envelope({ nonce: "fixed-nonce" });
    const first = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body,
    });
    expect(first.status).toBe(200);
    const second = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body: envelope({ nonce: "fixed-nonce", requestId: "different" }),
    });
    expect(second.status).toBe(401);
    expect((await second.json()).error).toBe("replayed_nonce");
  });

  it("dispatches a typed action to the worker", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/action`, {
      method: "POST",
      headers: H(),
      body: envelope({ action: actionRequest() }),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.result.status).toBe("succeeded");
    expect(worker.lastRequest?.toolName).toBe("browser_navigate");
  });
});
