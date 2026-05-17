// @vitest-environment node
import { mkdtempSync } from "node:fs";
import {
  appendFile,
  mkdir,
  readFile,
  unlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { wireQualityGateForTier2 } from "./integrate";
import {
  readLifecycleEvents,
  type LifecycleEventsDeps,
} from "./lifecycle-events";
import {
  installAdapter,
  markBrokenFromBoundary,
  uninstallAdapter,
  type RegistryHostDeps,
  type RendererDispatcher,
} from "./registry-host";
import type {
  SmokeFailKind,
  SmokeMethod,
  SmokeRenderExecutor,
} from "./quality-gate";

let tmpDir: string;

beforeAll(() => {
  wireQualityGateForTier2();
});

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "registry-host-"));
});

const GOOD_SOURCE = [
  "const adapter = {",
  '  scheme: "email",',
  '  matches: (uri) => uri.startsWith("email://"),',
  '  renderCurrent: (state) => ({ type: "div", props: {}, key: null }),',
  '  renderDiff: (diff) => ({ type: "div", props: {}, key: null }),',
  '  metadata: { origin: "agent-generated", schemaVersion: 1 },',
  "};",
  "module.exports = adapter;",
].join("\n");

const BAD_ALLOWLIST_SOURCE = [
  "const x = fetch;",
  "const adapter = {",
  '  scheme: "email",',
  "  matches: (uri) => true,",
  '  renderCurrent: () => ({ type: "div", props: {} }),',
  '  renderDiff: () => ({ type: "div", props: {} }),',
  '  metadata: { origin: "agent-generated", schemaVersion: 1 },',
  "};",
  "module.exports = adapter;",
].join("\n");

const BAD_SHAPE_SOURCE = [
  'const adapter = { scheme: "email" };',
  "module.exports = adapter;",
].join("\n");

function makeDispatcher(): {
  dispatcher: RendererDispatcher;
  sends: Array<{ channel: string; payload: unknown }>;
} {
  const sends: Array<{ channel: string; payload: unknown }> = [];
  const dispatcher: RendererDispatcher = {
    send(channel, payload) {
      sends.push({ channel, payload });
    },
  };
  return { dispatcher, sends };
}

async function readUtf8(path: string, _encoding: "utf8"): Promise<string> {
  return readFile(path, "utf8");
}

function audit(logPath: string): LifecycleEventsDeps {
  return {
    logPath,
    fs: { appendFile, mkdir, readFile: readUtf8 },
  };
}

function alwaysOkSmoke(): SmokeRenderExecutor {
  return {
    async execute(_a, _p, _b) {
      return { ok: true };
    },
  };
}

function alwaysThrowSmoke(method: SmokeMethod): SmokeRenderExecutor {
  return {
    async execute(_a, payload, _b) {
      if (payload.method === method) {
        return {
          ok: false,
          kind: "throw" as SmokeFailKind,
          error: new Error("smoke failed"),
        };
      }
      return { ok: true };
    },
  };
}

function deps(args: {
  smokeExecutor?: SmokeRenderExecutor;
  clock?: () => number;
}): {
  hostDeps: RegistryHostDeps;
  sends: Array<{ channel: string; payload: unknown }>;
  logPath: string;
} {
  const { dispatcher, sends } = makeDispatcher();
  const logPath = join(tmpDir, "audit", "adapter-lifecycle.log");
  const hostDeps: RegistryHostDeps = {
    adapterDir: join(tmpDir, "adapters"),
    clock: args.clock ?? (() => 1700000000000),
    dispatcher,
    audit: audit(logPath),
    installer: { fs: { writeFile, mkdir, unlink } },
    smokeExecutor: args.smokeExecutor ?? alwaysOkSmoke(),
  };
  return { hostDeps, sends, logPath };
}

describe("installAdapter — Q1→Q5 pipeline", () => {
  it("dispatches tier2.install on a fully valid adapter", async () => {
    const { hostDeps, sends, logPath } = deps({
      smokeExecutor: alwaysOkSmoke(),
    });
    const result = await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: GOOD_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "render-adapter-generator/v1",
      },
      hostDeps,
    );
    expect(result.ok).toBe(true);
    expect(sends).toHaveLength(1);
    expect(sends[0].channel).toBe("tier2.install");
    expect(sends[0].payload).toMatchObject({ scheme: "email", version: 1 });
    const events = await readLifecycleEvents({}, audit(logPath));
    expect(events.map((e) => e.kind)).toEqual(["installed"]);
  });

  it("Q2 (allowlist) failure short-circuits before Q1/Q3", async () => {
    const smokeCalls = vi.fn<SmokeRenderExecutor["execute"]>();
    const executor: SmokeRenderExecutor = {
      async execute(...args) {
        smokeCalls(...args);
        return { ok: true };
      },
    };
    const { hostDeps, sends, logPath } = deps({ smokeExecutor: executor });
    const result = await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: BAD_ALLOWLIST_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "render-adapter-generator/v1",
      },
      hostDeps,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.gate).toBe("allowlist");
    expect(smokeCalls).not.toHaveBeenCalled();
    expect(sends).toHaveLength(0);
    const events = await readLifecycleEvents({}, audit(logPath));
    expect(events.map((e) => e.kind)).toEqual(["validated"]);
    expect(events[0].detail).toMatch(/gate=allowlist/);
  });

  it("Q1 (schema) failure short-circuits before Q3", async () => {
    const smokeCalls = vi.fn<SmokeRenderExecutor["execute"]>();
    const executor: SmokeRenderExecutor = {
      async execute(...args) {
        smokeCalls(...args);
        return { ok: true };
      },
    };
    const { hostDeps, sends } = deps({ smokeExecutor: executor });
    const result = await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: BAD_SHAPE_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "x",
      },
      hostDeps,
    );
    expect(result.ok).toBe(false);
    // BAD_SHAPE_SOURCE fails the vm sandbox shape check first (looksLikeAdapter
    // in compileAdapter returns false because matches/renderCurrent/renderDiff
    // are missing), so the gate reported is "compile".
    if (!result.ok) {
      expect(["compile", "schema"]).toContain(result.gate);
    }
    expect(smokeCalls).not.toHaveBeenCalled();
    expect(sends).toHaveLength(0);
  });

  it("Q3 (smoke) failure short-circuits before persist + dispatch", async () => {
    const { hostDeps, sends, logPath } = deps({
      smokeExecutor: alwaysThrowSmoke("renderCurrent"),
    });
    const result = await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: GOOD_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "x",
      },
      hostDeps,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.gate).toBe("smoke");
    expect(sends).toHaveLength(0);
    const events = await readLifecycleEvents({}, audit(logPath));
    expect(events.map((e) => e.kind)).toEqual(["validated"]);
    expect(events[0].detail).toMatch(/gate=smoke/);
  });

  it("persists the source to disk on install success", async () => {
    const { hostDeps } = deps({ smokeExecutor: alwaysOkSmoke() });
    await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: GOOD_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "x",
      },
      hostDeps,
    );
    const onDisk = await readFile(
      join(hostDeps.adapterDir, "email-v1.js"),
      "utf8",
    );
    expect(onDisk).toBe(GOOD_SOURCE);
  });

  it("audit detail includes the generator model on success", async () => {
    const { hostDeps, logPath } = deps({ smokeExecutor: alwaysOkSmoke() });
    await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: GOOD_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "render-adapter-generator/v1",
      },
      hostDeps,
    );
    const events = await readLifecycleEvents({}, audit(logPath));
    expect(events[0].detail).toBe("model=render-adapter-generator/v1");
  });
});

describe("uninstallAdapter", () => {
  it("dispatches tier2.uninstall and appends marked-broken audit", async () => {
    const { hostDeps, sends, logPath } = deps({});
    // Pre-install to have a file to remove.
    await installAdapter(
      {
        scheme: "email",
        version: 1,
        source: GOOD_SOURCE,
        generatedAt: "2026-05-17T00:00:00Z",
        generatorModel: "x",
      },
      hostDeps,
    );
    sends.length = 0;
    await uninstallAdapter({ scheme: "email", version: 1 }, hostDeps);
    expect(sends).toEqual([
      {
        channel: "tier2.uninstall",
        payload: { scheme: "email", version: 1 },
      },
    ]);
    const events = await readLifecycleEvents({}, audit(logPath));
    expect(events.find((e) => e.kind === "marked-broken")?.detail).toBe(
      "uninstall",
    );
  });
});

describe("markBrokenFromBoundary", () => {
  it("appends render-error THEN marked-broken in order", async () => {
    const { hostDeps, sends, logPath } = deps({});
    await markBrokenFromBoundary(
      {
        scheme: "email",
        version: 2,
        method: "renderCurrent",
        reason: "TypeError: x is undefined",
      },
      hostDeps,
    );
    const events = await readLifecycleEvents(
      { scheme: "email" },
      audit(logPath),
    );
    expect(events.map((e) => e.kind)).toEqual([
      "render-error",
      "marked-broken",
    ]);
    expect(sends).toEqual([
      {
        channel: "tier2.mark-broken",
        payload: {
          scheme: "email",
          version: 2,
          method: "renderCurrent",
          reason: "TypeError: x is undefined",
        },
      },
    ]);
  });

  it("preserves the method in the render-error audit detail", async () => {
    const { hostDeps, logPath } = deps({});
    await markBrokenFromBoundary(
      {
        scheme: "salesforce",
        version: 3,
        method: "renderDiff",
        reason: "diff schema drift",
      },
      hostDeps,
    );
    const events = await readLifecycleEvents(
      { scheme: "salesforce", kind: "render-error" },
      audit(logPath),
    );
    expect(events[0].detail).toBe("renderDiff: diff schema drift");
  });
});
