// @vitest-environment node
import { EventEmitter } from "node:events";
import type { SpawnOptions } from "node:child_process";

import { describe, expect, it } from "vitest";

import { BROWSER_BROKER_AUDIENCE } from "./protocol";
import {
  createProductionDesktopBrowserSubsystem,
  readOriginPolicy,
  type SpawnBrowserWorker,
} from "./desktop-runtime";
import {
  BROWSER_WORKER_RPC_PROTOCOL,
  BROWSER_WORKER_RPC_TOKEN_ENV,
  type BrowserWorkerIpcChild,
} from "./worker-rpc";
import { BROWSER_TOOL_SCHEMAS } from "./tool-schemas";

const POLICY = {
  version: 1,
  topLevelOrigins: ["https://example.com"],
  subresourceOrigins: [],
  denyPrivateNetworks: true,
  serviceWorkers: "block",
};

class FakeStdio {
  readonly #events = new EventEmitter();
  on(event: "data", listener: (chunk: Buffer | string) => void): this {
    this.#events.on(event, listener);
    return this;
  }
  emit(chunk: string): void {
    this.#events.emit("data", chunk);
  }
}

class FakeProductionWorker implements BrowserWorkerIpcChild {
  readonly pid = 987_654_321;
  readonly stdout = new FakeStdio();
  readonly stderr = new FakeStdio();
  readonly #events = new EventEmitter();
  readonly requests: Array<Record<string, unknown>> = [];
  readonly token: string;
  version = "149.0.7827.55";
  healthy = true;

  constructor(env: NodeJS.ProcessEnv, options: { healthy?: boolean } = {}) {
    this.token = String(env[BROWSER_WORKER_RPC_TOKEN_ENV] ?? "");
    this.healthy = options.healthy ?? true;
    queueMicrotask(() => {
      this.stdout.emit(`READY ${JSON.stringify({ version: this.version })}\n`);
    });
  }

  on(
    event: "exit",
    listener: (code: number | null, signal: string | null) => void,
  ): this;
  on(event: "error", listener: (err: Error) => void): this;
  on(event: "message", listener: (message: unknown) => void): this;
  on(
    event: "exit" | "error" | "message",
    listener:
      | ((code: number | null, signal: string | null) => void)
      | ((err: Error) => void)
      | ((message: unknown) => void),
  ): this {
    this.#events.on(event, listener);
    return this;
  }

  send(message: unknown, callback?: (error: Error | null) => void): boolean {
    const request = message as Record<string, unknown>;
    this.requests.push(request);
    queueMicrotask(() => {
      callback?.(null);
      if (
        request.protocol !== BROWSER_WORKER_RPC_PROTOCOL ||
        request.token !== this.token
      ) {
        return;
      }
      const method = request.method;
      let result: unknown;
      if (method === "health") {
        result = { healthy: this.healthy, version: this.version };
      } else if (method === "list_tools") {
        result = { tools: BROWSER_TOOL_SCHEMAS };
      } else if (method === "dispatch_read") {
        const action = request.payload as Record<string, unknown>;
        result = {
          version: 1,
          requestId: action.requestId,
          sessionId: "ses",
          actionId: "act",
          status: "succeeded",
          safeSummary: "ok",
          artifactRefs: [],
        };
      } else if (method === "close_all") {
        result = { closed: true };
      } else {
        this.#events.emit("message", {
          protocol: BROWSER_WORKER_RPC_PROTOCOL,
          requestId: request.requestId,
          ok: false,
          error: { code: "unsupported", message: "unsupported" },
        });
        return;
      }
      this.#events.emit("message", {
        protocol: BROWSER_WORKER_RPC_PROTOCOL,
        requestId: request.requestId,
        ok: true,
        result,
      });
    });
    return true;
  }

  kill(signal?: NodeJS.Signals): boolean {
    queueMicrotask(() => this.#events.emit("exit", 0, signal ?? null));
    return true;
  }
}

function spawnHarness(config: { healthy?: boolean } = {}) {
  let child: FakeProductionWorker | null = null;
  let spawnOptions: SpawnOptions | null = null;
  const spawn: SpawnBrowserWorker = (_command, _args, value) => {
    spawnOptions = value;
    child = new FakeProductionWorker(value.env ?? {}, config);
    return child;
  };
  return {
    spawn,
    child: () => child,
    options: () => spawnOptions,
  };
}

function brokerEnvelope(extra: Record<string, unknown> = {}) {
  return {
    aud: BROWSER_BROKER_AUDIENCE,
    nonce: `nonce-${Math.random()}`,
    requestId: `request-${Math.random()}`,
    expiresAt: Date.now() + 30_000,
    ...extra,
  };
}

describe("production desktop browser composition", () => {
  it("starts worker before broker and maps the narrow read envelope end-to-end", async () => {
    const h = spawnHarness();
    const subsystem = createProductionDesktopBrowserSubsystem({
      userDataDir: "/tmp/test-user-data",
      workerEntryPath: "/app/out/browser-worker/index.js",
      electronExecutable: "/app/electron",
      browserExecutablePath: "/app/runtime/browser/chromium/chrome",
      processEnv: {
        BROWSER_ORIGIN_POLICY: JSON.stringify(POLICY),
        SUPER_SECRET_THAT_MUST_NOT_LEAK: "no",
      },
      spawn: h.spawn,
      platform: "darwin",
    });
    const handle = await subsystem.start();
    const headers = {
      authorization: `Bearer ${subsystem.broker.authToken()}`,
      "x-browser-protocol": "1",
      "content-type": "application/json",
    };

    const list = await fetch(`${handle.baseUrl}/v1/browser/tools/list`, {
      method: "POST",
      headers,
      body: JSON.stringify(brokerEnvelope()),
    });
    expect(list.status).toBe(200);
    const action = await fetch(`${handle.baseUrl}/v1/browser/action`, {
      method: "POST",
      headers,
      body: JSON.stringify(
        brokerEnvelope({
          runId: "run-1",
          workspaceId: "workspace-1",
          tool: {
            name: "browser_navigate",
            arguments: { url: "https://example.com/path" },
          },
        }),
      ),
    });
    expect(action.status).toBe(200);

    const dispatch = h
      .child()!
      .requests.find((request) => request.method === "dispatch_read")!;
    const mapped = dispatch.payload as Record<string, any>;
    expect(mapped.toolName).toBe("browser_navigate");
    expect(mapped.actionClass).toBe("navigate");
    expect(mapped.binding.profileMode).toBe("ephemeral");
    expect(mapped.binding.originPolicy).toEqual(POLICY);
    expect(mapped.binding.workspaceId).toBe("workspace-1");
    expect(h.options()?.env?.SUPER_SECRET_THAT_MUST_NOT_LEAK).toBeUndefined();
    expect(h.options()?.env?.ELECTRON_RUN_AS_NODE).toBe("1");
    expect(h.options()?.env?.BROWSER_EXECUTABLE_PATH).toBe(
      "/app/runtime/browser/chromium/chrome",
    );

    await subsystem.stop();
    expect(subsystem.supervisor.state).toBe("stopped");
  });

  it("fails closed before spawning when exact-origin policy is missing or empty", () => {
    expect(() => readOriginPolicy({})).toThrow(/requires/u);
    expect(() =>
      readOriginPolicy({
        BROWSER_ORIGIN_POLICY: JSON.stringify({
          ...POLICY,
          topLevelOrigins: [],
        }),
      }),
    ).toThrow(/at least one/u);
  });

  it("never binds the broker when the supervised worker is unhealthy", async () => {
    const h = spawnHarness({ healthy: false });
    const subsystem = createProductionDesktopBrowserSubsystem({
      userDataDir: "/tmp/test-user-data",
      workerEntryPath: "/app/out/browser-worker/index.js",
      electronExecutable: "/app/electron",
      browserExecutablePath: "/app/runtime/browser/chromium/chrome",
      processEnv: {
        BROWSER_ORIGIN_POLICY: JSON.stringify(POLICY),
      },
      spawn: h.spawn,
      platform: "darwin",
    });

    await expect(subsystem.start()).rejects.toThrow(/healthy/u);

    expect(subsystem.broker.isRunning()).toBe(false);
    expect(subsystem.supervisor.state).toBe("stopped");
  });
});
