// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
import type { Grant, GrantProvider, GrantSnapshot } from "./types";

function makeGrant(overrides: Partial<Grant> = {}): Grant {
  return {
    grantId: "11111111-1111-4111-8111-111111111111",
    root: "/data/private",
    mode: "read_only",
    label: "private",
    status: "active",
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

class FakeGrants implements GrantProvider {
  grants: Grant[] = [makeGrant()];
  async listAll(): Promise<readonly Grant[]> {
    return this.grants;
  }
  async snapshotActive(): Promise<GrantSnapshot> {
    return {
      snapshotId: "snap-1",
      capturedAt: 42,
      grants: this.grants.filter((g) => g.status === "active"),
    };
  }
}

describe("CapabilityBroker", () => {
  let broker: CapabilityBroker;
  let grants: FakeGrants;
  let baseUrl: string;
  let token: string;

  const H = (extra: Record<string, string> = {}) => ({
    authorization: `Bearer ${token}`,
    "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
    "content-type": "application/json",
    ...extra,
  });

  beforeEach(async () => {
    grants = new FakeGrants();
    broker = new CapabilityBroker({ grants });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("binds loopback on an ephemeral port with a non-secret base url", () => {
    expect(baseUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/u);
  });

  it("mints a 256-bit token (43-char base64url)", () => {
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/u);
  });

  it("handshake succeeds with a valid token + protocol version", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      protocol: string;
      methods: string[];
    };
    expect(body.protocol).toBe(CAPABILITY_BROKER_PROTOCOL);
    expect(body.methods).toContain("listGrants");
    expect(body.methods).toContain("snapshotGrants");
  });

  it("rejects a missing token with 401", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: {
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      },
      body: "{}",
    });
    expect(res.status).toBe(401);
  });

  it("rejects a wrong token with 401", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ authorization: "Bearer not-the-real-token" }),
      body: "{}",
    });
    expect(res.status).toBe(401);
  });

  it("rejects any request carrying an Origin header with 403", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ origin: "http://evil.example" }),
      body: "{}",
    });
    expect(res.status).toBe(403);
  });

  it("rejects requests carrying Sec-Fetch-Site browser metadata with 403", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ "sec-fetch-site": "cross-site" }),
      body: "{}",
    });
    expect(res.status).toBe(403);
  });

  it("rejects requests carrying Sec-Fetch-Dest browser metadata with 403", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ "sec-fetch-dest": "empty" }),
      body: "{}",
    });
    expect(res.status).toBe(403);
  });

  it("rejects a wrong protocol version with 400", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ "x-capability-protocol": "999" }),
      body: "{}",
    });
    expect(res.status).toBe(400);
  });

  it("rejects a missing protocol version with 400", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
      },
      body: "{}",
    });
    expect(res.status).toBe(400);
  });

  it("rejects non-POST methods with 405", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "GET",
      headers: H(),
    });
    expect(res.status).toBe(405);
  });

  it("rejects an oversized body with 413", async () => {
    const big = "x".repeat(70 * 1024);
    const res = await fetch(`${baseUrl}/v1/grants/list`, {
      method: "POST",
      headers: H(),
      body: JSON.stringify({ pad: big }),
    });
    expect(res.status).toBe(413);
  });

  it("lists grants for an authenticated caller", async () => {
    const res = await fetch(`${baseUrl}/v1/grants/list`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { grants: Grant[] };
    expect(body.grants).toHaveLength(1);
    expect(body.grants[0].root).toBe("/data/private");
  });

  it("snapshots only active grants", async () => {
    grants.grants = [
      makeGrant({ grantId: "a", status: "active" }),
      makeGrant({ grantId: "b", status: "revoked" }),
    ];
    const res = await fetch(`${baseUrl}/v1/grants/snapshot`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    const body = (await res.json()) as GrantSnapshot;
    expect(body.grants).toHaveLength(1);
    expect(body.grants[0].grantId).toBe("a");
  });

  it("returns 404 for an unknown route (authenticated)", async () => {
    const res = await fetch(`${baseUrl}/v1/nope`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    expect(res.status).toBe(404);
  });

  it("a 401 body never contains the real token", async () => {
    const res = await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H({ authorization: "Bearer wrong" }),
      body: "{}",
    });
    const text = await res.text();
    expect(text).not.toContain(token);
  });

  it("rotates the token on restart (a fresh start mints a new secret)", async () => {
    const first = token;
    await broker.stop();
    await broker.start();
    const second = broker.authToken();
    expect(second).not.toBe(first);
    // The old token no longer authenticates against the new listener.
    const res = await fetch(`${broker.baseUrl()}/v1/handshake`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${first}`,
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      },
      body: "{}",
    });
    expect(res.status).toBe(401);
  });

  it("throws if started twice without stopping", async () => {
    await expect(broker.start()).rejects.toThrow(/already running/u);
  });

  it("throws when asked for the token or url while stopped", async () => {
    await broker.stop();
    expect(() => broker.authToken()).toThrow(/not running/u);
    expect(() => broker.baseUrl()).toThrow(/not running/u);
  });
});
