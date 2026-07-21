// @vitest-environment jsdom
//
// PRD-10 (Wave 4) AC3 — end-to-end: an `adapter_generated` event arriving on
// the run feed drives the real desktop pipeline (RunFeedLifecycleEventSource →
// startTier2Lifecycle → real quality gates → registry-host) to a `tier2.install`
// IPC, whose payload the renderer's Tier2Bridge registers so the SurfaceRegistry
// resolves the new scheme. Then a boundary error round-trips through the same
// path to demote (markBroken) the adapter.
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { AdapterGeneratedPayload } from "@0x-copilot/api-types";
import { clearRegistry, resolveAdapter } from "@0x-copilot/chat-surface";
import {
  CHANNELS,
  Tier2InstallPayloadSchema,
  type WindowBridge,
} from "@0x-copilot/chat-transport";

import { Tier2Bridge } from "../../renderer/Tier2Bridge";
import {
  wireQualityGateForTier2,
  wireSmokeRenderExecutorForTier2,
} from "./integrate";
import {
  readLifecycleEvents,
  RunFeedLifecycleEventSource,
  type LifecycleEventsDeps,
} from "./lifecycle-events";
import { startTier2Lifecycle } from "./lifecycle";
import type { RegistryHostDeps, RendererDispatcher } from "./registry-host";

// Real CommonJS adapter (passes the real AST allowlist + vm compile + smoke).
const GOOD_SOURCE = [
  "const adapter = {",
  '  scheme: "record",',
  '  matches: (uri) => uri.indexOf("record://") === 0,',
  '  renderCurrent: (state) => ({ type: "div", props: {}, key: null }),',
  '  renderDiff: (diff) => ({ type: "div", props: {}, key: null }),',
  '  metadata: { origin: "agent-generated", schemaVersion: 5 },',
  "};",
  "module.exports = adapter;",
].join("\n");

function makeMemFs() {
  const files = new Map<string, string>();
  return {
    async appendFile(path: string, data: string) {
      files.set(path, (files.get(path) ?? "") + data);
    },
    async writeFile(path: string, data: string) {
      files.set(path, data);
    },
    async unlink(path: string) {
      files.delete(path);
    },
    async mkdir() {
      return undefined;
    },
    async readFile(path: string) {
      const content = files.get(path);
      if (content === undefined) {
        const err = new Error(`ENOENT: ${path}`) as NodeJS.ErrnoException;
        err.code = "ENOENT";
        throw err;
      }
      return content;
    },
  };
}

let tmpDir: string;

beforeAll(() => {
  // Un-refuse the D29 fail-closed gates: wire the real AST allowlist checker and
  // the real smoke-render executor (which now has a worker factory behind it).
  wireQualityGateForTier2();
  wireSmokeRenderExecutorForTier2();
});

beforeEach(() => {
  clearRegistry();
  tmpDir = mkdtempSync(join(tmpdir(), "tier2-e2e-"));
});

function envelope(payload: AdapterGeneratedPayload): string {
  return JSON.stringify({
    event_id: "evt_1",
    run_id: "run_1",
    conversation_id: "conv_1",
    sequence_no: 1,
    event_type: "adapter_generated",
    activity_kind: "tool",
    payload,
    created_at: "2026-05-17T00:00:00Z",
  });
}

const READ_PAYLOAD: AdapterGeneratedPayload = {
  scheme: "record",
  layout: "table", // read → auto-install
  schema_version: 5,
  adapter_source: GOOD_SOURCE,
  generated_at: "2026-05-17T00:00:00Z",
  generator_model: "render-adapter-generator/v1",
};

describe("tier-2 lifecycle end-to-end (AC3)", () => {
  it("adapter_generated → pipeline → tier2.install → registry resolves; markBroken demotes", async () => {
    const mem = makeMemFs();
    // Bridge the main-side dispatcher into a renderer-side Tier2Bridge so the
    // IPC payloads actually drive registerAdapter/markBroken.
    const rendererHandlers = new Map<string, (raw: unknown) => void>();
    const windowBridge: WindowBridge = {
      ipc: {
        invoke: <T>() => Promise.resolve(null as unknown as T),
        on: (channel, handler) => {
          rendererHandlers.set(channel, handler);
          return () => rendererHandlers.delete(channel);
        },
      },
    };
    new Tier2Bridge({ bridge: windowBridge }).attach();

    const dispatcher: RendererDispatcher = {
      send(channel, payload) {
        rendererHandlers.get(channel)?.(payload);
      },
    };

    const audit: LifecycleEventsDeps = {
      logPath: join(tmpDir, "audit", "lifecycle.log"),
      fs: {
        appendFile: mem.appendFile,
        mkdir: mem.mkdir,
        readFile: mem.readFile,
      },
    };
    const host: RegistryHostDeps = {
      adapterDir: join(tmpDir, "adapters"),
      clock: (() => {
        let n = 1700000000000;
        return () => (n += 1);
      })(),
      dispatcher,
      audit,
      installer: {
        fs: { writeFile: mem.writeFile, mkdir: mem.mkdir, unlink: mem.unlink },
      },
    };

    const source = new RunFeedLifecycleEventSource();
    const handle = startTier2Lifecycle({ source, host });

    // 1. Feed the run-feed message; the real pipeline installs it.
    source.feedStreamMessage(envelope(READ_PAYLOAD));
    await handle.settled();

    // The install IPC payload is valid per the frozen schema.
    const installed = resolveAdapter("record://item-42");
    expect(installed).not.toBeNull();
    expect(installed?.scheme).toBe("record");
    expect(installed?.metadata.schemaVersion).toBe(5);
    expect(installed?.metadata.origin).toBe("agent-generated");

    const events = await readLifecycleEvents({}, audit);
    expect(events.map((e) => e.kind)).toEqual(["generated", "installed"]);

    // 2. markBroken round-trip: a live boundary error demotes the adapter.
    source.feedBoundaryError({
      scheme: "record",
      version: 5,
      method: "renderCurrent",
      reason: "TypeError: x is undefined",
    });
    await handle.settled();
    expect(resolveAdapter("record://item-42")).toBeNull();
    expect(handle.attempts("record")).toBe(1);

    handle.stop();
  });

  it("the persisted install payload matches the frozen Tier2 install schema", async () => {
    const mem = makeMemFs();
    const captured: unknown[] = [];
    const dispatcher: RendererDispatcher = {
      send(channel, payload) {
        if (channel === "tier2.install") captured.push(payload);
      },
    };
    const host: RegistryHostDeps = {
      adapterDir: join(tmpDir, "adapters"),
      clock: () => 1700000000000,
      dispatcher,
      audit: {
        logPath: join(tmpDir, "audit.log"),
        fs: {
          appendFile: mem.appendFile,
          mkdir: mem.mkdir,
          readFile: mem.readFile,
        },
      },
      installer: {
        fs: { writeFile: mem.writeFile, mkdir: mem.mkdir, unlink: mem.unlink },
      },
    };
    const source = new RunFeedLifecycleEventSource();
    const handle = startTier2Lifecycle({ source, host });
    source.feedStreamMessage(envelope(READ_PAYLOAD));
    await handle.settled();
    handle.stop();

    expect(captured).toHaveLength(1);
    const parsed = Tier2InstallPayloadSchema.safeParse(captured[0]);
    expect(parsed.success).toBe(true);
  });
});
