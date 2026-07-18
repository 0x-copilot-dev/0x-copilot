// @vitest-environment node
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
import { HostFs } from "./host-fs";
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

  it("lists grants as a path-free projection (no host root leaks)", async () => {
    const res = await fetch(`${baseUrl}/v1/grants/list`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    expect(res.status).toBe(200);
    const text = await res.text();
    // G1: the canonical host root must NEVER appear in the response body.
    expect(text).not.toContain("/data/private");
    const body = JSON.parse(text) as {
      grants: Array<Record<string, unknown>>;
    };
    expect(body.grants).toHaveLength(1);
    const g = body.grants[0];
    expect(Object.keys(g).sort()).toEqual([
      "grantId",
      "label",
      "mode",
      "mount",
      "status",
    ]);
    expect(g).not.toHaveProperty("root");
    expect(typeof g.mount).toBe("string");
    expect(g.mount).toMatch(/^mnt_/u);
  });

  it("snapshots only active grants, path-free with a stable mount id", async () => {
    grants.grants = [
      makeGrant({ grantId: "a", status: "active" }),
      makeGrant({ grantId: "b", status: "revoked" }),
    ];
    const res = await fetch(`${baseUrl}/v1/grants/snapshot`, {
      method: "POST",
      headers: H(),
      body: "{}",
    });
    const text = await res.text();
    expect(text).not.toContain("/data/private"); // G1: no host root
    const body = JSON.parse(text) as {
      grants: Array<Record<string, unknown>>;
    };
    expect(body.grants).toHaveLength(1);
    expect(body.grants[0].grantId).toBe("a");
    expect(body.grants[0]).not.toHaveProperty("root");
    expect(body.grants[0].mount).toMatch(/^mnt_/u);
  });

  it("mount id is stable across list and snapshot for the same root", async () => {
    grants.grants = [makeGrant({ grantId: "a", status: "active" })];
    const list = (await fetch(`${baseUrl}/v1/grants/list`, {
      method: "POST",
      headers: H(),
      body: "{}",
    }).then((r) => r.json())) as { grants: Array<{ mount: string }> };
    const snap = (await fetch(`${baseUrl}/v1/grants/snapshot`, {
      method: "POST",
      headers: H(),
      body: "{}",
    }).then((r) => r.json())) as { grants: Array<{ mount: string }> };
    expect(list.grants[0].mount).toBe(snap.grants[0].mount);
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

// Grant provider backed by a real on-disk root so the broker's FS routes hit
// HostFs end-to-end. Supports revocation (revoked grants drop out of the
// active snapshot, exactly like the real GrantStore).
class RealRootGrants implements GrantProvider {
  grant: Grant;
  constructor(root: string) {
    this.grant = makeGrant({ grantId: "grant-1", root, mode: "read_only" });
  }
  async listAll(): Promise<readonly Grant[]> {
    return [this.grant];
  }
  async snapshotActive(): Promise<GrantSnapshot> {
    return {
      snapshotId: "snap",
      capturedAt: 0,
      grants: this.grant.status === "active" ? [this.grant] : [],
    };
  }
}

describe("CapabilityBroker — filesystem read ops", () => {
  let broker: CapabilityBroker;
  let grants: RealRootGrants;
  let baseUrl: string;
  let token: string;
  let root: string;

  const H = () => ({
    authorization: `Bearer ${token}`,
    "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
    "content-type": "application/json",
  });

  const post = (route: string, body: unknown) =>
    fetch(`${baseUrl}${route}`, {
      method: "POST",
      headers: H(),
      body: JSON.stringify(body),
    });

  beforeEach(async () => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "cap-broker-fs-")));
    writeFileSync(join(root, "file.txt"), "hello broker\nneedle line\n");
    grants = new RealRootGrants(root);
    broker = new CapabilityBroker({ grants, hostFs: new HostFs() });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
    rmSync(root, { recursive: true, force: true });
  });

  it("advertises the FS methods in the handshake", async () => {
    const res = await post("/v1/handshake", {});
    const body = (await res.json()) as { methods: string[] };
    expect(body.methods).toEqual(
      expect.arrayContaining(["statPath", "readFile", "glob", "grep"]),
    );
  });

  it("stat succeeds for a granted file", async () => {
    const res = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { type: string; size: number };
    expect(body.type).toBe("file");
    expect(body.size).toBe(Buffer.byteLength("hello broker\nneedle line\n"));
  });

  it("read returns base64 content and never the host path", async () => {
    const res = await post("/v1/fs/read", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain(root); // host path must never appear
    const body = JSON.parse(text) as { base64: string };
    expect(Buffer.from(body.base64, "base64").toString("utf-8")).toContain(
      "hello broker",
    );
  });

  it("list and glob and grep all work over the loopback", async () => {
    const list = (await await post("/v1/fs/list", {
      grant_id: "grant-1",
      path: "",
    }).then((r) => r.json())) as { entries: { name: string }[] };
    expect(list.entries.map((e) => e.name)).toContain("file.txt");

    const glob = (await post("/v1/fs/glob", {
      grant_id: "grant-1",
      pattern: "*.txt",
    }).then((r) => r.json())) as { paths: string[] };
    expect(glob.paths).toEqual(["file.txt"]);

    const grep = (await post("/v1/fs/grep", {
      grant_id: "grant-1",
      pattern: "needle",
    }).then((r) => r.json())) as { hits: { path: string }[] };
    expect(grep.hits[0].path).toBe("file.txt");
  });

  it("returns grant_required (403) for an unknown grant id", async () => {
    const res = await post("/v1/fs/stat", {
      grant_id: "does-not-exist",
      path: "file.txt",
    });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({ error: "grant_required" });
  });

  it("returns grant_required (403) after the grant is revoked", async () => {
    grants.grant = { ...grants.grant, status: "revoked" };
    const res = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({ error: "grant_required" });
  });

  it("returns invalid_path (400) for a traversal attempt", async () => {
    const res = await post("/v1/fs/read", {
      grant_id: "grant-1",
      path: "../escape",
    });
    expect(res.status).toBe(400);
    expect((await res.json()) as unknown).toEqual({ error: "invalid_path" });
  });

  it("returns not_found (404) for a missing path", async () => {
    const res = await post("/v1/fs/read", {
      grant_id: "grant-1",
      path: "nope.txt",
    });
    expect(res.status).toBe(404);
    expect((await res.json()) as unknown).toEqual({ error: "not_found" });
  });

  it("rejects a request missing the path param (400 invalid_request)", async () => {
    const res = await post("/v1/fs/stat", { grant_id: "grant-1" });
    expect(res.status).toBe(400);
    expect((await res.json()) as unknown).toEqual({ error: "invalid_request" });
  });

  it("fails closed with unsupported (404) when no HostFs is wired", async () => {
    const noFs = new CapabilityBroker({ grants });
    const handle = await noFs.start();
    try {
      const res = await fetch(`${handle.baseUrl}/v1/fs/stat`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${noFs.authToken()}`,
          "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
          "content-type": "application/json",
        },
        body: JSON.stringify({ grant_id: "grant-1", path: "file.txt" }),
      });
      expect(res.status).toBe(404);
      expect((await res.json()) as unknown).toEqual({ error: "unsupported" });
    } finally {
      await noFs.stop();
    }
  });

  it("still refuses an unauthenticated FS request (401)", async () => {
    const res = await fetch(`${baseUrl}/v1/fs/stat`, {
      method: "POST",
      headers: {
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      },
      body: JSON.stringify({ grant_id: "grant-1", path: "file.txt" }),
    });
    expect(res.status).toBe(401);
  });
});
