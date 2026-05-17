// @vitest-environment node
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createFileAuthAuditLog, type AuthAuditEvent } from "./audit-log";

let workDir: string;
let logPath: string;

beforeEach(async () => {
  workDir = await mkdtemp(join(tmpdir(), "auth-audit-"));
  logPath = join(workDir, "nested", "auth.log");
});

afterEach(async () => {
  await rm(workDir, { recursive: true, force: true });
});

function fixedClock(start = "2026-05-17T20:00:00.000Z"): () => Date {
  let n = 0;
  return () => new Date(Date.parse(start) + 1000 * n++);
}

describe("createFileAuthAuditLog — round-trips each event kind", () => {
  it("sign-in-success", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    const event: AuthAuditEvent = {
      kind: "sign-in-success",
      workspaceId: "wsp_acme",
      sub: "user_42",
      mode: "oidc",
    };
    await log.append(event);
    const entries = await log.readAll();
    expect(entries).toEqual([{ ts: "2026-05-17T20:00:00.000Z", event }]);
  });

  it("sign-in-failure", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    const event: AuthAuditEvent = {
      kind: "sign-in-failure",
      workspaceId: "wsp_acme",
      mode: "oidc",
      reason: "state mismatch",
    };
    await log.append(event);
    const [entry] = await log.readAll();
    expect(entry.event).toEqual(event);
  });

  it("sign-out", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({ kind: "sign-out", workspaceId: "wsp_acme" });
    expect((await log.readAll())[0].event).toEqual({
      kind: "sign-out",
      workspaceId: "wsp_acme",
    });
  });

  it("token-refresh-success", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({
      kind: "token-refresh-success",
      workspaceId: "wsp_acme",
    });
    expect((await log.readAll())[0].event.kind).toBe("token-refresh-success");
  });

  it("token-refresh-failure", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({
      kind: "token-refresh-failure",
      workspaceId: "wsp_acme",
      reason: "no refresh token",
    });
    const [entry] = await log.readAll();
    expect(entry.event).toEqual({
      kind: "token-refresh-failure",
      workspaceId: "wsp_acme",
      reason: "no refresh token",
    });
  });

  it("unauthorized-retry", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({
      kind: "unauthorized-retry",
      workspaceId: "wsp_acme",
      path: "/v1/me/profile",
    });
    const [entry] = await log.readAll();
    expect(entry.event).toEqual({
      kind: "unauthorized-retry",
      workspaceId: "wsp_acme",
      path: "/v1/me/profile",
    });
  });

  it("secret-storage-gate-violation (shared sink with secret-storage)", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({
      kind: "secret-storage-gate-violation",
      claimedWorkspaceId: "wsp_globex",
      sessionWorkspaceId: "wsp_acme",
      serverKind: "saas",
      serverId: "salesforce",
    });
    const [entry] = await log.readAll();
    expect(entry.event).toEqual({
      kind: "secret-storage-gate-violation",
      claimedWorkspaceId: "wsp_globex",
      sessionWorkspaceId: "wsp_acme",
      serverKind: "saas",
      serverId: "salesforce",
    });
  });
});

describe("createFileAuthAuditLog — file format", () => {
  it("writes one JSON object per line, parseable individually", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({
      kind: "sign-in-success",
      workspaceId: "wsp_acme",
      sub: "user_42",
      mode: "oidc",
    });
    await log.append({ kind: "sign-out", workspaceId: "wsp_acme" });

    const raw = await readFile(logPath, "utf8");
    const lines = raw.split("\n").filter((l) => l.length > 0);
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(() => JSON.parse(line)).not.toThrow();
    }
  });

  it("appends — does not rewrite earlier lines", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock(),
    });
    await log.append({ kind: "sign-out", workspaceId: "wsp_a" });
    const after1 = await readFile(logPath, "utf8");
    await log.append({ kind: "sign-out", workspaceId: "wsp_b" });
    const after2 = await readFile(logPath, "utf8");

    expect(after2.startsWith(after1)).toBe(true);
    expect(after2.length).toBeGreaterThan(after1.length);
  });

  it("survives across instances (each instance opens the same file in append mode)", async () => {
    const logA = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock("2026-05-17T20:00:00.000Z"),
    });
    await logA.append({ kind: "sign-out", workspaceId: "wsp_a" });

    const logB = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock("2026-05-17T21:00:00.000Z"),
    });
    await logB.append({ kind: "sign-out", workspaceId: "wsp_b" });

    const entries = await logB.readAll();
    expect(entries).toHaveLength(2);
    expect(entries[0].event).toEqual({
      kind: "sign-out",
      workspaceId: "wsp_a",
    });
    expect(entries[0].ts).toBe("2026-05-17T20:00:00.000Z");
    expect(entries[1].event).toEqual({
      kind: "sign-out",
      workspaceId: "wsp_b",
    });
    expect(entries[1].ts).toBe("2026-05-17T21:00:00.000Z");
  });

  it("each entry carries an ISO timestamp from the injected clock", async () => {
    const log = createFileAuthAuditLog({
      filePath: logPath,
      now: fixedClock("2026-05-17T20:00:00.000Z"),
    });
    await log.append({ kind: "sign-out", workspaceId: "wsp_a" });
    await log.append({ kind: "sign-out", workspaceId: "wsp_b" });

    const entries = await log.readAll();
    expect(entries.map((e) => e.ts)).toEqual([
      "2026-05-17T20:00:00.000Z",
      "2026-05-17T20:00:01.000Z",
    ]);
  });

  it("readAll() returns [] when the file does not exist yet", async () => {
    const log = createFileAuthAuditLog({ filePath: logPath });
    const entries = await log.readAll();
    expect(entries).toEqual([]);
  });
});
