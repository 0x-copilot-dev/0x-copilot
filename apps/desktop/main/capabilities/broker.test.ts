// @vitest-environment node
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
import { HostFs } from "./host-fs";
import { FS_LIMITS } from "./path-validation";
import type { Grant, GrantProvider, GrantSnapshot } from "./types";

const b64 = (s: string): string => Buffer.from(s, "utf-8").toString("base64");

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

describe("CapabilityBroker — filesystem write ops + mode gating", () => {
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
  const setMode = (mode: Grant["mode"]) => {
    grants.grant = { ...grants.grant, mode };
  };
  const onDisk = (rel: string) => readFileSync(join(root, rel), "utf-8");

  beforeEach(async () => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "cap-broker-wr-")));
    writeFileSync(join(root, "file.txt"), "hello broker\n");
    grants = new RealRootGrants(root);
    // Default to full authority; individual tests narrow the mode.
    grants.grant = { ...grants.grant, mode: "read_write" };
    broker = new CapabilityBroker({ grants, hostFs: new HostFs() });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
    rmSync(root, { recursive: true, force: true });
  });

  it("advertises the write methods in the handshake", async () => {
    const body = (await post("/v1/handshake", {}).then((r) => r.json())) as {
      methods: string[];
    };
    expect(body.methods).toEqual(
      expect.arrayContaining([
        "writeFile",
        "editFile",
        "makeDir",
        "deletePath",
        "movePath",
      ]),
    );
  });

  it("write creates a file, returns created:true, and never leaks the host path", async () => {
    const res = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: "new.txt",
      content_base64: b64("written via broker"),
    });
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain(root); // host path must never appear
    const body = JSON.parse(text) as { created: boolean; path: string };
    expect(body).toMatchObject({ created: true, path: "new.txt" });
    expect(onDisk("new.txt")).toBe("written via broker");
  });

  it("edit replaces an existing file's contents", async () => {
    const res = await post("/v1/fs/edit", {
      grant_id: "grant-1",
      path: "file.txt",
      content_base64: b64("edited"),
    });
    expect(res.status).toBe(200);
    expect(onDisk("file.txt")).toBe("edited");
  });

  it("mkdir creates a directory", async () => {
    const res = await post("/v1/fs/mkdir", {
      grant_id: "grant-1",
      path: "created-dir",
    });
    expect(res.status).toBe(200);
    expect(existsSync(join(root, "created-dir"))).toBe(true);
  });

  it("delete removes a file under read_write", async () => {
    const res = await post("/v1/fs/delete", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(res.status).toBe(200);
    expect(existsSync(join(root, "file.txt"))).toBe(false);
  });

  it("move renames a file under read_write", async () => {
    const res = await post("/v1/fs/move", {
      grant_id: "grant-1",
      from: "file.txt",
      to: "renamed.txt",
    });
    expect(res.status).toBe(200);
    expect(existsSync(join(root, "renamed.txt"))).toBe(true);
    expect(existsSync(join(root, "file.txt"))).toBe(false);
  });

  it("read_only DENIES every write op (403 permission_denied), mutating nothing", async () => {
    setMode("read_only");
    const cases: Array<[string, unknown]> = [
      [
        "/v1/fs/write",
        { grant_id: "grant-1", path: "x.txt", content_base64: b64("x") },
      ],
      [
        "/v1/fs/edit",
        { grant_id: "grant-1", path: "file.txt", content_base64: b64("x") },
      ],
      ["/v1/fs/mkdir", { grant_id: "grant-1", path: "d" }],
      ["/v1/fs/delete", { grant_id: "grant-1", path: "file.txt" }],
      ["/v1/fs/move", { grant_id: "grant-1", from: "file.txt", to: "y.txt" }],
    ];
    for (const [route, body] of cases) {
      const res = await post(route, body);
      expect(res.status).toBe(403);
      expect((await res.json()) as unknown).toEqual({
        error: "permission_denied",
      });
    }
    expect(onDisk("file.txt")).toBe("hello broker\n");
  });

  it("read_write_no_delete allows write/edit/mkdir but DENIES delete + move", async () => {
    setMode("read_write_no_delete");
    expect(
      (
        await post("/v1/fs/write", {
          grant_id: "grant-1",
          path: "ok.txt",
          content_base64: b64("ok"),
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await post("/v1/fs/edit", {
          grant_id: "grant-1",
          path: "file.txt",
          content_base64: b64("ed"),
        })
      ).status,
    ).toBe(200);
    expect(
      (await post("/v1/fs/mkdir", { grant_id: "grant-1", path: "okdir" }))
        .status,
    ).toBe(200);

    const del = await post("/v1/fs/delete", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(del.status).toBe(403);
    expect((await del.json()) as unknown).toEqual({
      error: "permission_denied",
    });

    const mv = await post("/v1/fs/move", {
      grant_id: "grant-1",
      from: "file.txt",
      to: "z.txt",
    });
    expect(mv.status).toBe(403);
    expect((await mv.json()) as unknown).toEqual({
      error: "permission_denied",
    });

    // The rename-away target never appeared and the source survives.
    expect(existsSync(join(root, "z.txt"))).toBe(false);
    expect(existsSync(join(root, "file.txt"))).toBe(true);
  });

  it("read_write_no_delete PERMITS an atomic same-file overwrite", async () => {
    setMode("read_write_no_delete");
    expect(
      (
        await post("/v1/fs/write", {
          grant_id: "grant-1",
          path: "same.txt",
          content_base64: b64("v1"),
        })
      ).status,
    ).toBe(200);
    const second = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: "same.txt",
      content_base64: b64("v2"),
    });
    expect(second.status).toBe(200);
    expect(((await second.json()) as { created: boolean }).created).toBe(false);
    expect(onDisk("same.txt")).toBe("v2");
  });

  it("denies creating a sensitive file even under read_write (403)", async () => {
    const res = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: ".env",
      content_base64: b64("SECRET=1"),
    });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({
      error: "permission_denied",
    });
    expect(existsSync(join(root, ".env"))).toBe(false);
  });

  it("content over the write ceiling is rejected (413 too_large)", async () => {
    const tooBig = Buffer.alloc(FS_LIMITS.maxWriteBytes + 1).toString("base64");
    const res = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: "big.bin",
      content_base64: tooBig,
    });
    expect(res.status).toBe(413);
    expect((await res.json()) as unknown).toEqual({ error: "too_large" });
    expect(existsSync(join(root, "big.bin"))).toBe(false);
  });

  it("rejects a write missing content_base64 (400 invalid_request)", async () => {
    const res = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: "x.txt",
    });
    expect(res.status).toBe(400);
    expect((await res.json()) as unknown).toEqual({ error: "invalid_request" });
  });

  it("denies a traversal write (400 invalid_path), writing nothing outside", async () => {
    const res = await post("/v1/fs/write", {
      grant_id: "grant-1",
      path: "../escape.txt",
      content_base64: b64("x"),
    });
    expect(res.status).toBe(400);
    expect((await res.json()) as unknown).toEqual({ error: "invalid_path" });
    expect(existsSync(join(root, "..", "escape.txt"))).toBe(false);
  });

  it("still refuses an unauthenticated write (401)", async () => {
    const res = await fetch(`${baseUrl}/v1/fs/write`, {
      method: "POST",
      headers: {
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        grant_id: "grant-1",
        path: "x.txt",
        content_base64: b64("x"),
      }),
    });
    expect(res.status).toBe(401);
    expect(existsSync(join(root, "x.txt"))).toBe(false);
  });
});

describe("CapabilityBroker — per-run grant snapshot", () => {
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
    root = realpathSync(mkdtempSync(join(tmpdir(), "cap-broker-run-")));
    writeFileSync(join(root, "file.txt"), "hello broker\n");
    grants = new RealRootGrants(root); // read_only suffices to prove pinning
    broker = new CapabilityBroker({ grants, hostFs: new HostFs() });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
    rmSync(root, { recursive: true, force: true });
  });

  it("runs/begin mints an opaque context id + a PATH-FREE grant projection", async () => {
    const res = await post("/v1/runs/begin", {});
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain(root); // no host root leak
    const body = JSON.parse(text) as {
      runCapabilityContext: string;
      grants: Array<Record<string, unknown>>;
    };
    expect(body.runCapabilityContext).toMatch(/^rcx_[A-Za-z0-9_-]{43}$/u);
    expect(body.grants[0]).not.toHaveProperty("root");
    expect(body.grants[0].mount).toMatch(/^mnt_/u);
  });

  it("a run-context-bound op resolves against the PINNED snapshot (survives a mid-run revoke)", async () => {
    const begin = (await post("/v1/runs/begin", {}).then((r) => r.json())) as {
      runCapabilityContext: string;
    };
    const rcx = begin.runCapabilityContext;

    // Revoke the grant AFTER the run started.
    grants.grant = { ...grants.grant, status: "revoked" };

    // A LIVE op (no run context) now fails closed on the revoke.
    const live = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
    });
    expect(live.status).toBe(403);
    expect((await live.json()) as unknown).toEqual({ error: "grant_required" });

    // The SAME op bound to the pinned run context still succeeds.
    const pinned = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
      run_capability_context: rcx,
    });
    expect(pinned.status).toBe(200);
    expect(((await pinned.json()) as { type: string }).type).toBe("file");
  });

  it("rejects an unknown / forged run_capability_context (403 grant_required)", async () => {
    const res = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
      run_capability_context: "rcx_forged-value",
    });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({ error: "grant_required" });
  });

  it("runs/end releases the context; a subsequently bound op fails closed", async () => {
    const begin = (await post("/v1/runs/begin", {}).then((r) => r.json())) as {
      runCapabilityContext: string;
    };
    const rcx = begin.runCapabilityContext;

    const end = await post("/v1/runs/end", { run_capability_context: rcx });
    expect(end.status).toBe(200);
    expect((await end.json()) as unknown).toEqual({ released: true });

    const after = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
      run_capability_context: rcx,
    });
    expect(after.status).toBe(403);
    expect((await after.json()) as unknown).toEqual({
      error: "grant_required",
    });
  });

  it("stop() clears all run contexts (RAM-only; a restart never inherits them)", async () => {
    const begin = (await post("/v1/runs/begin", {}).then((r) => r.json())) as {
      runCapabilityContext: string;
    };
    const rcx = begin.runCapabilityContext;

    await broker.stop();
    await broker.start();
    baseUrl = broker.baseUrl();
    token = broker.authToken();

    const after = await post("/v1/fs/stat", {
      grant_id: "grant-1",
      path: "file.txt",
      run_capability_context: rcx,
    });
    expect(after.status).toBe(403);
    expect((await after.json()) as unknown).toEqual({
      error: "grant_required",
    });
  });

  it("runs/begin and runs/end require auth (401)", async () => {
    const noAuth = {
      "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
      "content-type": "application/json",
    };
    const begin = await fetch(`${baseUrl}/v1/runs/begin`, {
      method: "POST",
      headers: noAuth,
      body: "{}",
    });
    expect(begin.status).toBe(401);
    const end = await fetch(`${baseUrl}/v1/runs/end`, {
      method: "POST",
      headers: noAuth,
      body: JSON.stringify({ run_capability_context: "rcx_x" }),
    });
    expect(end.status).toBe(401);
  });
});
