// @vitest-environment node
//
// P2-7a — the broker's MCP direct-connect credential route.
//
// This is the ONE route that hands a secret to the supervised child, so the
// tests are written against the two things that make that safe: the existing
// per-boot bearer envelope, and `SecretStorage`'s ACTIVE-WORKSPACE gate. The
// gate cases run against the REAL `SecretStorage` on a real temp dir — a
// hand-rolled fake store would only prove that the fake refuses, not that the
// class the app actually wires does.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CapabilityBroker,
  CAPABILITY_BROKER_PROTOCOL,
  type McpConnectionSecret,
  type McpSecretStore,
} from "./broker";
import type { Grant, GrantProvider, GrantSnapshot } from "./types";
import { SecretStorage, type SafeStorageLike } from "../auth/secret-storage";

const WORKSPACE = "wsp_acme";
const OTHER_WORKSPACE = "wsp_other";
const SERVER_ID = "linear";
const TOKEN_VALUE = "lin_oat_super-secret-value-9f2b";

const SECRET: McpConnectionSecret = {
  url: "https://mcp.linear.app/mcp",
  transport: "http",
  token: TOKEN_VALUE,
  expires_at: 1_900_000_000_000,
  headers: { "linear-workspace": "acme" },
};

class FakeGrants implements GrantProvider {
  async listAll(): Promise<readonly Grant[]> {
    return [];
  }
  async snapshotActive(): Promise<GrantSnapshot> {
    return { snapshotId: "snap-1", capturedAt: 0, grants: [] };
  }
}

// Deliberately reversible rather than real crypto: the point of these tests is
// the gate and the projection, not safeStorage itself.
function fakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (plaintext) =>
      Buffer.concat([
        Buffer.from("ENCv1:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x42),
      ]),
    decryptString: (cipher) =>
      Buffer.from(
        cipher.subarray("ENCv1:".length).map((b) => b ^ 0x42),
      ).toString("utf-8"),
  };
}

/** Counts reads so a refusal can be proven to have happened BEFORE the store. */
class CountingStore implements McpSecretStore {
  reads = 0;
  constructor(private readonly inner: McpSecretStore) {}
  getActiveWorkspace(): string | null {
    return this.inner.getActiveWorkspace();
  }
  async get(
    workspaceId: string,
    serverKind: "mcp",
    serverId: string,
  ): Promise<unknown | null> {
    this.reads += 1;
    return this.inner.get(workspaceId, serverKind, serverId);
  }
}

describe("CapabilityBroker — POST /mcp/secret", () => {
  let userDataDir: string;
  let storage: SecretStorage;
  let store: CountingStore;
  let broker: CapabilityBroker;
  let baseUrl: string;
  let token: string;
  let consoleSpies: ReturnType<typeof spyOnConsole>;

  const H = (extra: Record<string, string> = {}) => ({
    authorization: `Bearer ${token}`,
    "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
    "content-type": "application/json",
    ...extra,
  });

  const post = (body: unknown, extra: Record<string, string> = {}) =>
    fetch(`${baseUrl}/mcp/secret`, {
      method: "POST",
      headers: H(extra),
      body: JSON.stringify(body),
    });

  beforeEach(async () => {
    userDataDir = mkdtempSync(join(tmpdir(), "cap-broker-mcp-"));
    storage = new SecretStorage({
      userDataDir,
      safeStorage: fakeSafeStorage(),
    });
    storage.setActiveWorkspace(WORKSPACE);
    await storage.set(WORKSPACE, "mcp", SERVER_ID, SECRET);
    store = new CountingStore(storage);
    broker = new CapabilityBroker({
      grants: new FakeGrants(),
      mcpSecrets: store,
    });
    const handle = await broker.start();
    baseUrl = handle.baseUrl;
    token = broker.authToken();
    consoleSpies = spyOnConsole();
  });

  afterEach(async () => {
    consoleSpies.restore();
    await broker.stop();
    rmSync(userDataDir, { recursive: true, force: true });
  });

  it("returns the connection material for a connector in the active workspace", async () => {
    const res = await post({ server_id: SERVER_ID });
    expect(res.status).toBe(200);
    expect((await res.json()) as unknown).toEqual({
      url: "https://mcp.linear.app/mcp",
      transport: "http",
      token: TOKEN_VALUE,
      expires_at: 1_900_000_000_000,
      headers: { "linear-workspace": "acme" },
    });
  });

  it("advertises readMcpSecret only when a secret store is wired", async () => {
    const withStore = (await fetch(`${baseUrl}/v1/handshake`, {
      method: "POST",
      headers: H(),
      body: "{}",
    }).then((r) => r.json())) as { methods: string[] };
    expect(withStore.methods).toContain("readMcpSecret");

    const bare = new CapabilityBroker({ grants: new FakeGrants() });
    const handle = await bare.start();
    try {
      const body = (await fetch(`${handle.baseUrl}/v1/handshake`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${bare.authToken()}`,
          "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
          "content-type": "application/json",
        },
        body: "{}",
      }).then((r) => r.json())) as { methods: string[] };
      expect(body.methods).not.toContain("readMcpSecret");
    } finally {
      await bare.stop();
    }
  });

  it("fails closed with unsupported (404) when no secret store is wired", async () => {
    const bare = new CapabilityBroker({ grants: new FakeGrants() });
    const handle = await bare.start();
    try {
      const res = await fetch(`${handle.baseUrl}/mcp/secret`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${bare.authToken()}`,
          "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
          "content-type": "application/json",
        },
        body: JSON.stringify({ server_id: SERVER_ID }),
      });
      expect(res.status).toBe(404);
      expect((await res.json()) as unknown).toEqual({ error: "unsupported" });
    } finally {
      await bare.stop();
    }
  });

  // --- authentication (the existing envelope, on the secret route) ---------

  it("refuses an unauthenticated read (401) without touching the store", async () => {
    const res = await fetch(`${baseUrl}/mcp/secret`, {
      method: "POST",
      headers: {
        "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
        "content-type": "application/json",
      },
      body: JSON.stringify({ server_id: SERVER_ID }),
    });
    expect(res.status).toBe(401);
    expect(await res.text()).not.toContain(TOKEN_VALUE);
    expect(store.reads).toBe(0);
  });

  it("refuses a wrong bearer (401) without touching the store", async () => {
    const res = await post(
      { server_id: SERVER_ID },
      { authorization: "Bearer not-the-real-token" },
    );
    expect(res.status).toBe(401);
    expect(await res.text()).not.toContain(TOKEN_VALUE);
    expect(store.reads).toBe(0);
  });

  it("refuses a browser-shaped caller (403) and a non-POST method (405)", async () => {
    const browser = await post(
      { server_id: SERVER_ID },
      { origin: "http://evil.example" },
    );
    expect(browser.status).toBe(403);
    expect(store.reads).toBe(0);

    const wrongMethod = await fetch(`${baseUrl}/mcp/secret`, {
      method: "GET",
      headers: H(),
    });
    expect(wrongMethod.status).toBe(405);
    expect(store.reads).toBe(0);
  });

  // --- the active-workspace gate ------------------------------------------

  it("refuses a read asserting a FOREIGN workspace before touching the store", async () => {
    const res = await post({
      server_id: SERVER_ID,
      workspace_id: OTHER_WORKSPACE,
    });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({
      error: "permission_denied",
    });
    // The refusal is decided from main-owned state; the store is never asked,
    // so this cannot become an existence oracle for another workspace.
    expect(store.reads).toBe(0);
  });

  it("accepts a read that asserts the workspace it is actually bound to", async () => {
    const res = await post({ server_id: SERVER_ID, workspace_id: WORKSPACE });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { token: string }).token).toBe(TOKEN_VALUE);
  });

  it("cannot reach a connector stored under another workspace", async () => {
    // Same server id, a real secret, but written while workspace B was active.
    storage.setActiveWorkspace(OTHER_WORKSPACE);
    await storage.set(OTHER_WORKSPACE, "mcp", "notion", {
      ...SECRET,
      token: "foreign-workspace-token",
    });
    storage.setActiveWorkspace(WORKSPACE);

    const res = await post({ server_id: "notion" });
    expect(res.status).toBe(404);
    expect((await res.json()) as unknown).toEqual({ error: "not_found" });
  });

  it("refuses every read while signed out (no active workspace)", async () => {
    storage.setActiveWorkspace(null);
    const res = await post({ server_id: SERVER_ID });
    expect(res.status).toBe(403);
    expect((await res.json()) as unknown).toEqual({
      error: "permission_denied",
    });
    expect(store.reads).toBe(0);
  });

  it("fails closed when the active workspace moves under the read", async () => {
    // The broker resolves the active workspace, then reads. If the session
    // re-binds in between, the store's own gate must still refuse — the answer
    // is "no usable material", never workspace B's token.
    const racing: McpSecretStore = {
      getActiveWorkspace: () => WORKSPACE,
      get: (workspaceId, serverKind, serverId) => {
        storage.setActiveWorkspace(OTHER_WORKSPACE);
        return storage.get(workspaceId, serverKind, serverId);
      },
    };
    const raced = new CapabilityBroker({
      grants: new FakeGrants(),
      mcpSecrets: racing,
    });
    const handle = await raced.start();
    try {
      const res = await fetch(`${handle.baseUrl}/mcp/secret`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${raced.authToken()}`,
          "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
          "content-type": "application/json",
        },
        body: JSON.stringify({ server_id: SERVER_ID }),
      });
      expect(res.status).toBe(404);
      expect(await res.text()).not.toContain(TOKEN_VALUE);
    } finally {
      await raced.stop();
    }
  });

  // --- typed not-found, never a partial success ---------------------------

  it("returns a typed not_found (404) for an unknown server_id", async () => {
    const res = await post({ server_id: "never-connected" });
    expect(res.status).toBe(404);
    expect((await res.json()) as unknown).toEqual({ error: "not_found" });
  });

  it("returns not_found rather than a 200 with an empty token", async () => {
    await storage.set(WORKSPACE, "mcp", "empty-token", {
      url: "https://mcp.example/mcp",
      transport: "http",
      token: "",
    });
    const res = await post({ server_id: "empty-token" });
    expect(res.status).toBe(404);
    expect((await res.json()) as unknown).toEqual({ error: "not_found" });
  });

  it.each([
    ["no token key", { url: "https://mcp.example/mcp", transport: "http" }],
    ["no url", { transport: "http", token: TOKEN_VALUE }],
    ["no transport", { url: "https://mcp.example/mcp", token: TOKEN_VALUE }],
    [
      "a non-string token",
      { url: "https://mcp.example/mcp", transport: "http", token: 42 },
    ],
    [
      "a boolean expiry",
      {
        url: "https://mcp.example/mcp",
        transport: "http",
        token: TOKEN_VALUE,
        expires_at: true,
      },
    ],
    [
      "non-string headers",
      {
        url: "https://mcp.example/mcp",
        transport: "http",
        token: TOKEN_VALUE,
        headers: { "x-a": 1 },
      },
    ],
    ["a non-object record", "just-a-string"],
  ])("returns not_found for a record with %s", async (_label, record) => {
    await storage.set(WORKSPACE, "mcp", "broken", record);
    const res = await post({ server_id: "broken" });
    expect(res.status).toBe(404);
    expect((await res.json()) as unknown).toEqual({ error: "not_found" });
  });

  it("normalizes a missing expiry to an explicit null", async () => {
    await storage.set(WORKSPACE, "mcp", "no-expiry", {
      url: "https://mcp.example/mcp",
      transport: "sse",
      token: TOKEN_VALUE,
    });
    const res = await post({ server_id: "no-expiry" });
    expect(res.status).toBe(200);
    expect((await res.json()) as unknown).toEqual({
      url: "https://mcp.example/mcp",
      transport: "sse",
      token: TOKEN_VALUE,
      expires_at: null,
    });
  });

  it("projects field by field — a stored refresh token never crosses", async () => {
    await storage.set(WORKSPACE, "mcp", "with-refresh", {
      ...SECRET,
      refresh_token: "rt_must_never_leave_main",
      client_secret: "cs_must_never_leave_main",
      workspace_id: WORKSPACE,
    });
    const res = await post({ server_id: "with-refresh" });
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain("rt_must_never_leave_main");
    expect(text).not.toContain("cs_must_never_leave_main");
    expect(Object.keys(JSON.parse(text) as object).sort()).toEqual([
      "expires_at",
      "headers",
      "token",
      "transport",
      "url",
    ]);
  });

  it.each([
    ["missing", {}],
    ["empty", { server_id: "" }],
    ["not a string", { server_id: 7 }],
    ["shaped like a path", { server_id: "../../backend/facade" }],
  ])("rejects a %s server_id with invalid_request (400)", async (_l, body) => {
    const res = await post(body);
    expect(res.status).toBe(400);
    expect((await res.json()) as unknown).toEqual({ error: "invalid_request" });
  });

  // --- the token never appears anywhere but the 200 body ------------------

  it("never writes the token to the console on any path", async () => {
    await post({ server_id: SERVER_ID });
    await post({ server_id: SERVER_ID, workspace_id: OTHER_WORKSPACE });
    await post({ server_id: "never-connected" });
    await post({});
    storage.setActiveWorkspace(null);
    await post({ server_id: SERVER_ID });

    const logged = consoleSpies.text();
    expect(logged).not.toContain(TOKEN_VALUE);
    // The gate's own audit line is allowed to fire, but must stay value-free.
    expect(logged).not.toContain(SECRET.url);
  });

  it("never echoes the token into an error body", async () => {
    const bodies = await Promise.all(
      [
        { server_id: SERVER_ID, workspace_id: OTHER_WORKSPACE },
        { server_id: "never-connected" },
        { server_id: "" },
      ].map((body) => post(body).then((r) => r.text())),
    );
    for (const body of bodies) {
      expect(body).not.toContain(TOKEN_VALUE);
      expect(body).not.toContain(OTHER_WORKSPACE);
      expect(Object.keys(JSON.parse(body) as object)).toEqual(["error"]);
    }
  });
});

/**
 * The real `SecretStorage` must keep satisfying the broker's narrow read port.
 * If the store's shape drifts (a renamed partition, an added required arg),
 * this fails at compile time rather than at the first live connector read.
 */
describe("McpSecretStore port", () => {
  it("is structurally satisfied by SecretStorage", () => {
    const tmp = mkdtempSync(join(tmpdir(), "cap-broker-mcp-port-"));
    try {
      const storage = new SecretStorage({
        userDataDir: tmp,
        safeStorage: fakeSafeStorage(),
      });
      const port: McpSecretStore = storage;
      expect(port.getActiveWorkspace()).toBeNull();
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

function spyOnConsole() {
  const spies = (["log", "info", "warn", "error", "debug"] as const).map(
    (level) => vi.spyOn(console, level).mockImplementation(() => {}),
  );
  return {
    text: () =>
      spies
        .flatMap((spy) => spy.mock.calls)
        .map((call) => call.map((arg) => safeString(arg)).join(" "))
        .join("\n"),
    restore: () => {
      for (const spy of spies) spy.mockRestore();
    },
  };
}

function safeString(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}
