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
import { MainBrowserReadAuthority } from "./read-authority";
import {
  LOCAL_BROKER_AUDIENCE,
  LocalServiceIdentityRegistry,
} from "../services/local-service-identity";
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

const ORIGIN_POLICY = {
  version: 1 as const,
  topLevelOrigins: ["https://example.com"],
  subresourceOrigins: [],
  denyPrivateNetworks: true as const,
  serviceWorkers: "block" as const,
};

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
    broker = new BrowserBroker({
      worker,
      now: () => NOW,
      readAuthority: new MainBrowserReadAuthority({
        originPolicy: ORIGIN_POLICY,
        now: () => NOW,
        randomBytes: () => Buffer.alloc(24, 7),
      }),
    });
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
      body: envelope({
        runId: "run-1",
        workspaceId: "ws",
        tool: {
          name: "browser_navigate",
          arguments: { url: "https://example.com" },
        },
        // Forged authority fields are ignored; main derives the real values.
        actionClass: BrowserActionClass.ExternalEffect,
        originPolicy: { topLevelOrigins: ["https://evil.example"] },
      }),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.result.status).toBe("succeeded");
    expect(worker.lastRequest?.toolName).toBe("browser_navigate");
    expect(worker.lastRequest?.actionClass).toBe(BrowserActionClass.Navigate);
    expect(worker.lastRequest?.binding.profileMode).toBe(
      BrowserProfileMode.Ephemeral,
    );
    expect(worker.lastRequest?.binding.profileId).toBe("ephemeral");
    expect(worker.lastRequest?.binding.originPolicy).toEqual(ORIGIN_POLICY);
  });

  it("refuses an unadvertised generic click even with a valid broker credential", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/action`, {
      method: "POST",
      headers: H(),
      body: envelope({
        runId: "run-1",
        workspaceId: "ws",
        tool: { name: "browser_click", arguments: { ref: "e1_0" } },
      }),
    });
    expect(res.status).toBe(403);
    expect((await res.json()).error).toBe("read_operation_required");
    expect(worker.lastRequest).toBeNull();
  });

  it("pins a run to one workspace and rejects scope switching", async () => {
    const call = (workspaceId: string) =>
      fetch(`${baseUrl}/v1/browser/action`, {
        method: "POST",
        headers: H(),
        body: envelope({
          runId: "run-pinned",
          workspaceId,
          tool: { name: "browser_snapshot", arguments: {} },
        }),
      });

    expect((await call("ws-a")).status).toBe(200);
    const switched = await call("ws-b");
    expect(switched.status).toBe(403);
    expect((await switched.json()).error).toBe("scope_mismatch");
  });

  it("rejects an envelope whose validity exceeds the replay-cache TTL", async () => {
    const res = await fetch(`${baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers: H(),
      body: envelope({ expiresAt: NOW + 5 * 60 * 1000 + 1 }),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("expiry_too_far");
  });
});

describe("BrowserBroker named local clients", () => {
  it("has no fallback bearer and refuses a sibling, missing-audience, and capability-channel swap", async () => {
    let byte = 0;
    const identities = new LocalServiceIdentityRegistry({
      randomBytes: (size) => Buffer.alloc(size, ++byte),
    });
    const broker = new BrowserBroker({
      worker: new FakeWorker(),
      now: () => NOW,
      readAuthority: new MainBrowserReadAuthority({
        originPolicy: ORIGIN_POLICY,
      }),
      clientCredentials: [
        identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.browser),
      ],
    });
    const { baseUrl } = await broker.start();
    const headers = (
      service: string,
      audience: string | undefined,
      credential: string,
    ) => ({
      authorization: `Bearer ${credential}`,
      "x-browser-protocol": BROWSER_BROKER_PROTOCOL,
      "x-desktop-local-service": service,
      ...(audience === undefined
        ? {}
        : { "x-desktop-local-audience": audience }),
    });
    try {
      await expect(
        fetch(`${baseUrl}/v1/browser/handshake`, {
          method: "POST",
          headers: headers(
            "backend",
            LOCAL_BROKER_AUDIENCE.browser,
            identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.browser)
              .credential,
          ),
        }),
      ).resolves.toMatchObject({ status: 401 });
      await expect(
        fetch(`${baseUrl}/v1/browser/handshake`, {
          method: "POST",
          headers: headers(
            "ai-backend",
            undefined,
            identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.browser)
              .credential,
          ),
        }),
      ).resolves.toMatchObject({ status: 401 });
      await expect(
        fetch(`${baseUrl}/v1/browser/handshake`, {
          method: "POST",
          headers: headers(
            "ai-backend",
            LOCAL_BROKER_AUDIENCE.capability,
            identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.browser)
              .credential,
          ),
        }),
      ).resolves.toMatchObject({ status: 401 });
      await expect(
        fetch(`${baseUrl}/v1/browser/handshake`, {
          method: "POST",
          headers: headers(
            "ai-backend",
            LOCAL_BROKER_AUDIENCE.browser,
            identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.capability)
              .credential,
          ),
        }),
      ).resolves.toMatchObject({ status: 401 });
      await expect(
        fetch(`${baseUrl}/v1/browser/handshake`, {
          method: "POST",
          headers: headers(
            "ai-backend",
            LOCAL_BROKER_AUDIENCE.browser,
            identities.forBroker("ai-backend", LOCAL_BROKER_AUDIENCE.browser)
              .credential,
          ),
        }),
      ).resolves.toMatchObject({ status: 200 });
      expect(() => broker.authToken()).toThrow(/disabled/i);
    } finally {
      await broker.stop();
    }
  });

  it("rejects a channel credential configured for another broker", () => {
    const identities = new LocalServiceIdentityRegistry();
    expect(
      () =>
        new BrowserBroker({
          worker: new FakeWorker(),
          now: () => NOW,
          readAuthority: new MainBrowserReadAuthority({
            originPolicy: ORIGIN_POLICY,
          }),
          clientCredentials: [
            identities.forBroker(
              "ai-backend",
              LOCAL_BROKER_AUDIENCE.capability,
            ),
          ],
        }),
    ).toThrow(/audience/i);
  });
});
