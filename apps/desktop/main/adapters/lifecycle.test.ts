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

import type { AdapterGeneratedPayload } from "@0x-copilot/api-types";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { wireQualityGateForTier2 } from "./integrate";
import {
  readLifecycleEvents,
  type LifecycleEventsDeps,
} from "./lifecycle-events";
import {
  startTier2Lifecycle,
  type LifecycleBoundaryEvent,
  type LifecycleEventSource,
} from "./lifecycle";
import type { RegistryHostDeps, RendererDispatcher } from "./registry-host";
import type { SmokeRenderExecutor } from "./quality-gate";

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

const BAD_ALLOWLIST_SOURCE = ["const x = fetch;", "module.exports = {};"].join(
  "\n",
);

let tmpDir: string;

beforeAll(() => {
  wireQualityGateForTier2();
});

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "lifecycle-"));
});

function alwaysOkSmoke(): SmokeRenderExecutor {
  return {
    async execute() {
      return { ok: true };
    },
  };
}

function makeSource(): {
  source: LifecycleEventSource;
  fireGenerated: (payload: AdapterGeneratedPayload) => void;
  fireBoundary: (info: LifecycleBoundaryEvent) => void;
} {
  let genHandler: ((p: AdapterGeneratedPayload) => void) | null = null;
  let boundaryHandler: ((info: LifecycleBoundaryEvent) => void) | null = null;
  const source: LifecycleEventSource = {
    onAdapterGenerated(handler) {
      genHandler = handler;
      return () => {
        genHandler = null;
      };
    },
    onBoundaryError(handler) {
      boundaryHandler = handler;
      return () => {
        boundaryHandler = null;
      };
    },
  };
  return {
    source,
    fireGenerated: (p) => {
      if (genHandler) genHandler(p);
    },
    fireBoundary: (info) => {
      if (boundaryHandler) boundaryHandler(info);
    },
  };
}

interface HostBundle {
  hostDeps: RegistryHostDeps;
  sends: Array<{ channel: string; payload: unknown }>;
  audit: LifecycleEventsDeps;
}

function makeHost(smoke?: SmokeRenderExecutor): HostBundle {
  const sends: Array<{ channel: string; payload: unknown }> = [];
  const dispatcher: RendererDispatcher = {
    send(channel, payload) {
      sends.push({ channel, payload });
    },
  };
  const logPath = join(tmpDir, "audit", "lifecycle.log");
  const audit: LifecycleEventsDeps = {
    logPath,
    fs: {
      appendFile,
      mkdir,
      readFile: async (p, _e) => readFile(p, "utf8"),
    },
  };
  return {
    hostDeps: {
      adapterDir: join(tmpDir, "adapters"),
      clock: (() => {
        let n = 1700000000000;
        return () => {
          n += 1;
          return n;
        };
      })(),
      dispatcher,
      audit,
      installer: { fs: { writeFile, mkdir, unlink } },
      smokeExecutor: smoke ?? alwaysOkSmoke(),
    },
    sends,
    audit,
  };
}

function payload(
  overrides: Partial<AdapterGeneratedPayload> = {},
): AdapterGeneratedPayload {
  return {
    scheme: "email",
    layout: "form",
    schema_version: 1,
    adapter_source: GOOD_SOURCE,
    generated_at: "2026-05-17T00:00:00Z",
    generator_model: "render-adapter-generator/v1",
    ...overrides,
  };
}

async function flush(): Promise<void> {
  // Allow the void-returning IIFE inside the handler to settle. The install
  // pipeline chains many awaits (audit append, AST scan, compile, schema,
  // smoke render, persist, dispatch, second audit append). 50 ticks is the
  // conservative upper bound — well below any sleep-based threshold.
  for (let i = 0; i < 50; i += 1) {
    await new Promise((r) => setImmediate(r));
  }
}

describe("startTier2Lifecycle — happy path", () => {
  it("install pipeline runs and dispatches tier2.install", async () => {
    const host = makeHost();
    const src = makeSource();
    const errors: Error[] = [];
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      onError: (e) => errors.push(e),
    });
    src.fireGenerated(payload());
    await flush();
    expect(errors).toEqual([]);
    expect(host.sends.find((s) => s.channel === "tier2.install")).toBeTruthy();
    expect(handle.attempts("email")).toBe(0);
    handle.stop();
  });

  it("appends 'generated' then 'installed' audit events in order", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
    });
    src.fireGenerated(payload());
    await flush();
    const events = await readLifecycleEvents({}, host.audit);
    expect(events.map((e) => e.kind)).toEqual(["generated", "installed"]);
    handle.stop();
  });

  it("resets the attempt counter on a successful install", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      retryBudget: 3,
    });
    // First boundary error increments the counter.
    src.fireBoundary({
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      reason: "boom",
    });
    await flush();
    expect(handle.attempts("email")).toBe(1);
    // Successful install resets to 0.
    src.fireGenerated(payload({ schema_version: 2 }));
    await flush();
    expect(handle.attempts("email")).toBe(0);
    handle.stop();
  });
});

describe("startTier2Lifecycle — broken adapter (Q6 trip)", () => {
  it("a single boundary-error increments the counter exactly once", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
    });
    src.fireBoundary({
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      reason: "TypeError",
    });
    await flush();
    expect(handle.attempts("email")).toBe(1);
    expect(
      host.sends.find((s) => s.channel === "tier2.mark-broken"),
    ).toBeTruthy();
    handle.stop();
  });

  it("appends render-error AND marked-broken audit events", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
    });
    src.fireBoundary({
      scheme: "email",
      version: 1,
      method: "renderDiff",
      reason: "diff drift",
    });
    await flush();
    const events = await readLifecycleEvents({ scheme: "email" }, host.audit);
    expect(events.map((e) => e.kind)).toEqual([
      "render-error",
      "marked-broken",
    ]);
    handle.stop();
  });
});

describe("startTier2Lifecycle — bounded retry budget", () => {
  it("budget exhaustion fires lifecycle-exhausted and skips installAdapter", async () => {
    const host = makeHost();
    const src = makeSource();
    const exhausted: string[] = [];
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      retryBudget: 1,
      onExhausted: (scheme) => exhausted.push(scheme),
    });
    // Drive one boundary error → counter at 1 (the budget).
    src.fireBoundary({
      scheme: "email",
      version: 1,
      method: "renderCurrent",
      reason: "boom",
    });
    await flush();
    // Subsequent adapter_generated for the SAME scheme is rejected.
    host.sends.length = 0;
    src.fireGenerated(payload({ schema_version: 2 }));
    await flush();
    expect(
      host.sends.find((s) => s.channel === "tier2.install"),
    ).toBeUndefined();
    expect(exhausted).toEqual(["email"]);
    const events = await readLifecycleEvents(
      { scheme: "email", kind: "lifecycle-exhausted" },
      host.audit,
    );
    expect(events).toHaveLength(1);
    handle.stop();
  });

  it("install failure inside budget queues regen and increments the counter", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      retryBudget: 3,
    });
    src.fireGenerated(
      payload({ adapter_source: BAD_ALLOWLIST_SOURCE, schema_version: 1 }),
    );
    await flush();
    expect(handle.attempts("email")).toBe(1);
    const events = await readLifecycleEvents(
      { scheme: "email", kind: "regen-queued" },
      host.audit,
    );
    expect(events).toHaveLength(1);
    handle.stop();
  });

  it("the third failure trips lifecycle-exhausted", async () => {
    const host = makeHost();
    const src = makeSource();
    const exhausted: string[] = [];
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      retryBudget: 3,
      onExhausted: (s) => exhausted.push(s),
    });
    for (let i = 0; i < 3; i += 1) {
      src.fireGenerated(
        payload({
          adapter_source: BAD_ALLOWLIST_SOURCE,
          schema_version: 1 + i,
        }),
      );
      await flush();
    }
    expect(exhausted).toEqual(["email"]);
    handle.stop();
  });
});

describe("startTier2Lifecycle — per-attempt deadline", () => {
  it("counts a timed-out install attempt against the budget", async () => {
    // Install hangs by returning a never-resolving Promise via a smoke
    // executor that itself never resolves.
    const hangingSmoke: SmokeRenderExecutor = {
      execute: () => new Promise(() => {}),
    };
    const host = makeHost(hangingSmoke);
    const src = makeSource();

    let firedTimer: (() => void) | null = null;
    const setTimeoutSpy = vi.fn((cb: () => void) => {
      firedTimer = cb;
      return 1 as unknown;
    });

    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
      retryBudget: 3,
      attemptTimeoutMs: 100,
      setTimeout: setTimeoutSpy,
      clearTimeout: () => {},
    });

    src.fireGenerated(payload());
    // Wait for the install promise to start and the timer to be registered.
    // The lifecycle's raceWithDeadline registers the timer synchronously
    // when the install promise begins.
    for (let i = 0; i < 50 && firedTimer === null; i += 1) {
      await new Promise((r) => setImmediate(r));
    }
    expect(firedTimer).not.toBeNull();
    firedTimer!();
    await flush();

    expect(handle.attempts("email")).toBe(1);
    const events = await readLifecycleEvents(
      { scheme: "email", kind: "regen-queued" },
      host.audit,
    );
    expect(events[0].detail).toMatch(/attempt-timeout/);
    handle.stop();
  });
});

describe("startTier2Lifecycle — stop unsubscribes", () => {
  it("stop() prevents further dispatches", async () => {
    const host = makeHost();
    const src = makeSource();
    const handle = startTier2Lifecycle({
      source: src.source,
      host: host.hostDeps,
    });
    handle.stop();
    src.fireGenerated(payload());
    await flush();
    expect(host.sends).toHaveLength(0);
  });
});
