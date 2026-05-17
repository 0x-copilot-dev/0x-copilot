// @vitest-environment node
import { mkdtempSync, readFileSync } from "node:fs";
import { appendFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  markAdapterBroken,
  type AuditEntry,
  type BrokenMarkDeps,
  type BrokenMarkRegistry,
} from "./broken-mark";

function fakeRegistry() {
  const calls: Array<{ scheme: string; version: number; reason: string }> = [];
  const registry: BrokenMarkRegistry = {
    markBroken(scheme, version, reason) {
      calls.push({ scheme, version, reason });
    },
  };
  return { registry, calls };
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "quality-gate-broken-mark-"));
});

afterEach(() => {
  // tmp dir cleanup is intentionally skipped — tmpdir is rotated by the OS.
});

function depsAt(logPath: string, clock = () => 1700000000000): BrokenMarkDeps {
  return {
    logPath,
    clock,
    registry: fakeRegistry().registry,
    fs: { appendFile, mkdir },
  };
}

function readLines(path: string): AuditEntry[] {
  const raw = readFileSync(path, "utf8");
  return raw
    .split("\n")
    .filter((s) => s.length > 0)
    .map((s) => JSON.parse(s) as AuditEntry);
}

describe("Q6 — markAdapterBroken", () => {
  it("appends one JSON Lines record to the audit log", async () => {
    const logPath = join(tmpDir, "audit", "adapter-lifecycle.log");
    const { registry, calls } = fakeRegistry();
    await markAdapterBroken("email", 2, "render-error: TypeError", {
      logPath,
      clock: () => 1700000000000,
      registry,
      fs: { appendFile, mkdir },
    });
    void calls;
    const lines = readLines(logPath);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toEqual({
      ts: 1700000000000,
      kind: "broken-marked",
      scheme: "email",
      version: 2,
      reason: "render-error: TypeError",
    });
  });

  it("calls registry.markBroken exactly once with the same arguments", async () => {
    const logPath = join(tmpDir, "a", "b", "c.log");
    const { registry, calls } = fakeRegistry();
    await markAdapterBroken("slack", 7, "schema-drift", {
      logPath,
      clock: () => 1,
      registry,
      fs: { appendFile, mkdir },
    });
    expect(calls).toEqual([
      { scheme: "slack", version: 7, reason: "schema-drift" },
    ]);
  });

  it("creates the parent directory if missing (recursive mkdir)", async () => {
    const logPath = join(tmpDir, "nested", "deeper", "audit.log");
    const { registry } = fakeRegistry();
    await markAdapterBroken("notion", 1, "first-error", {
      logPath,
      clock: () => 42,
      registry,
      fs: { appendFile, mkdir },
    });
    const lines = readLines(logPath);
    expect(lines[0].scheme).toBe("notion");
  });

  it("appends additional records without rewriting prior ones (append-only)", async () => {
    const logPath = join(tmpDir, "audit.log");
    const { registry } = fakeRegistry();
    await markAdapterBroken("email", 1, "first", {
      logPath,
      clock: () => 100,
      registry,
      fs: { appendFile, mkdir },
    });
    await markAdapterBroken("email", 2, "second", {
      logPath,
      clock: () => 200,
      registry,
      fs: { appendFile, mkdir },
    });
    await markAdapterBroken("salesforce", 5, "third", {
      logPath,
      clock: () => 300,
      registry,
      fs: { appendFile, mkdir },
    });
    const lines = readLines(logPath);
    expect(lines.map((l) => l.reason)).toEqual(["first", "second", "third"]);
    expect(lines.map((l) => l.ts)).toEqual([100, 200, 300]);
  });

  it("uses fs.appendFile (NOT fs.writeFile) — audit log is append-only", async () => {
    const logPath = join(tmpDir, "audit.log");
    const appendSpy = vi.fn<typeof appendFile>(async (...args) => {
      await appendFile(...args);
    });
    const mkdirSpy = vi.fn<typeof mkdir>(async (...args) => {
      await mkdir(...args);
    });
    const { registry } = fakeRegistry();
    await markAdapterBroken("email", 1, "reason", {
      logPath,
      clock: () => 1,
      registry,
      fs: { appendFile: appendSpy, mkdir: mkdirSpy },
    });
    expect(appendSpy).toHaveBeenCalledTimes(1);
    expect(mkdirSpy).toHaveBeenCalledTimes(1);
    // mkdir is recursive — verify the call shape.
    expect(mkdirSpy.mock.calls[0][1]).toEqual({ recursive: true });
  });

  it("each line is a single valid JSON object (newline-delimited)", async () => {
    const logPath = join(tmpDir, "audit.log");
    const { registry } = fakeRegistry();
    await markAdapterBroken("email", 1, 'with\nnewline\nand"quote', {
      logPath,
      clock: () => 1,
      registry,
      fs: { appendFile, mkdir },
    });
    const raw = readFileSync(logPath, "utf8");
    // JSON.stringify escapes the embedded newline + quote, so the file
    // contains exactly one trailing newline.
    expect(raw.endsWith("\n")).toBe(true);
    expect(raw.split("\n").filter((s) => s.length > 0)).toHaveLength(1);
    const parsed = JSON.parse(raw.trim()) as AuditEntry;
    expect(parsed.reason).toBe('with\nnewline\nand"quote');
  });

  it("registry is called AFTER the audit entry is persisted", async () => {
    const logPath = join(tmpDir, "audit.log");
    const order: string[] = [];
    const trackingFs = {
      async appendFile(path: string, data: string): Promise<void> {
        order.push("appendFile");
        await appendFile(path, data);
      },
      async mkdir(
        path: string,
        opts: { recursive: true },
      ): Promise<string | undefined> {
        order.push("mkdir");
        return mkdir(path, opts);
      },
    };
    const registry: BrokenMarkRegistry = {
      markBroken: () => {
        order.push("markBroken");
      },
    };
    await markAdapterBroken("email", 1, "reason", {
      logPath,
      clock: () => 1,
      registry,
      fs: trackingFs,
    });
    expect(order).toEqual(["mkdir", "appendFile", "markBroken"]);
  });

  it("works against the default node fs primitives", async () => {
    const logPath = join(tmpDir, "default-fs.log");
    const { registry } = fakeRegistry();
    void depsAt; // silence unused
    await markAdapterBroken("email", 1, "happy", {
      logPath,
      clock: () => 1,
      registry,
      fs: { appendFile, mkdir },
    });
    const lines = readLines(logPath);
    expect(lines[0].kind).toBe("broken-marked");
  });
});
