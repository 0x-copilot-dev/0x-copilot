// @vitest-environment node
import { mkdtempSync } from "node:fs";
import { appendFile, mkdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  appendLifecycleEvent,
  readLifecycleEvents,
  type LifecycleAuditEntry,
  type LifecycleEventsDeps,
} from "./lifecycle-events";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "lifecycle-events-"));
});

async function readUtf8(path: string, _encoding: "utf8"): Promise<string> {
  return readFile(path, "utf8");
}

function depsAt(logPath: string): LifecycleEventsDeps {
  return {
    logPath,
    fs: { appendFile, mkdir, readFile: readUtf8 },
  };
}

describe("appendLifecycleEvent", () => {
  it("appends one JSON-Lines record and creates parent dirs", async () => {
    const logPath = join(tmpDir, "audit", "adapter-lifecycle.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps,
    );
    const raw = await readFile(logPath, "utf8");
    expect(raw).toBe(
      `${JSON.stringify({ ts: 1, kind: "requested", scheme: "email", version: 1 })}\n`,
    );
  });

  it("appends additional records without rewriting prior ones", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 2, kind: "generated", scheme: "email", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 3, kind: "installed", scheme: "email", version: 1 },
      deps,
    );
    const all = await readLifecycleEvents({}, deps);
    expect(all).toHaveLength(3);
    expect(all.map((e) => e.kind)).toEqual([
      "requested",
      "generated",
      "installed",
    ]);
  });

  it("uses fs.appendFile (never writeFile/truncate/unlink)", async () => {
    const logPath = join(tmpDir, "audit.log");
    const appendSpy = vi.fn<typeof appendFile>(async (...args) => {
      await appendFile(...args);
    });
    const mkdirSpy = vi.fn<typeof mkdir>(async (...args) => {
      await mkdir(...args);
    });
    const deps: LifecycleEventsDeps = {
      logPath,
      fs: { appendFile: appendSpy, mkdir: mkdirSpy, readFile: readUtf8 },
    };
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "x", version: 1 },
      deps,
    );
    expect(appendSpy).toHaveBeenCalledTimes(1);
    expect(mkdirSpy.mock.calls[0][1]).toEqual({ recursive: true });
  });

  it("preserves the optional detail field", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      {
        ts: 5,
        kind: "render-error",
        scheme: "email",
        version: 2,
        detail: "TypeError: x is undefined",
      },
      deps,
    );
    const all = await readLifecycleEvents({}, deps);
    expect(all[0].detail).toBe("TypeError: x is undefined");
  });
});

describe("readLifecycleEvents", () => {
  it("returns [] when the file does not exist", async () => {
    const deps = depsAt(join(tmpDir, "missing.log"));
    const events = await readLifecycleEvents({}, deps);
    expect(events).toEqual([]);
  });

  it("filters by scheme", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 2, kind: "requested", scheme: "slack", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 3, kind: "installed", scheme: "email", version: 1 },
      deps,
    );
    const onlyEmail = await readLifecycleEvents({ scheme: "email" }, deps);
    expect(onlyEmail.map((e) => e.kind)).toEqual(["requested", "installed"]);
  });

  it("filters by kind", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 2, kind: "installed", scheme: "email", version: 1 },
      deps,
    );
    await appendLifecycleEvent(
      { ts: 3, kind: "installed", scheme: "slack", version: 1 },
      deps,
    );
    const onlyInstalled = await readLifecycleEvents(
      { kind: "installed" },
      deps,
    );
    expect(onlyInstalled).toHaveLength(2);
    expect(onlyInstalled.every((e) => e.kind === "installed")).toBe(true);
  });

  it("applies limit to the tail of the log", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    for (let i = 0; i < 10; i += 1) {
      await appendLifecycleEvent(
        { ts: i, kind: "requested", scheme: "email", version: i },
        deps,
      );
    }
    const tail = await readLifecycleEvents({ limit: 3 }, deps);
    expect(tail).toHaveLength(3);
    expect(tail.map((e) => e.version)).toEqual([7, 8, 9]);
  });

  it("ignores malformed lines without throwing", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps,
    );
    await appendFile(logPath, "this is not json\n");
    await appendFile(logPath, `${JSON.stringify({ wrong: "shape" })}\n`);
    await appendLifecycleEvent(
      { ts: 2, kind: "installed", scheme: "email", version: 1 },
      deps,
    );
    const all = await readLifecycleEvents({}, deps);
    expect(all.map((e) => e.kind)).toEqual(["requested", "installed"]);
  });

  it("survives a 'restart' (close + reopen the file path)", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps1 = depsAt(logPath);
    await appendLifecycleEvent(
      { ts: 1, kind: "requested", scheme: "email", version: 1 },
      deps1,
    );
    // Simulate restart by reading through a fresh deps object — the file
    // remains the audit-log source of truth.
    const deps2 = depsAt(logPath);
    const events = await readLifecycleEvents({}, deps2);
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual<LifecycleAuditEntry>({
      ts: 1,
      kind: "requested",
      scheme: "email",
      version: 1,
    });
  });

  it("preserves insertion order across many writes", async () => {
    const logPath = join(tmpDir, "audit.log");
    const deps = depsAt(logPath);
    const kinds = ["requested", "generated", "installed"] as const;
    for (let i = 0; i < 30; i += 1) {
      await appendLifecycleEvent(
        { ts: i, kind: kinds[i % 3], scheme: "x", version: 1 },
        deps,
      );
    }
    const all = await readLifecycleEvents({}, deps);
    expect(all.map((e) => e.ts)).toEqual(
      Array.from({ length: 30 }, (_, i) => i),
    );
  });
});
