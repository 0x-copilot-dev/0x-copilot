// @vitest-environment node
import { EventEmitter } from "node:events";

import { describe, expect, it } from "vitest";

import type { BrowserWorkerPort } from "./browser-broker";
import type { BrowserPrivateEffectWorkerPort } from "./private-effect-bridge";
import {
  BrowserActionClass,
  BrowserEffectActionKind,
  BrowserEffectOutcome,
  BrowserProfileMode,
  type BrowserActionPlan,
  type BrowserActionRequest,
  type BrowserActionResult,
} from "./protocol";
import { BROWSER_TOOL_SCHEMAS, type BrowserToolSchema } from "./tool-schemas";
import {
  BROWSER_WORKER_RPC_PROTOCOL,
  BrowserWorkerRpcClient,
  type BrowserWorkerIpcChild,
  type BrowserWorkerRpcProcess,
  installBrowserWorkerRpcServer,
} from "./worker-rpc";

const TOKEN = "a".repeat(43);
const TEST_BROWSER_VERSION = "test-browser-version";

const READ: BrowserActionRequest = {
  version: 1,
  requestId: "request-1",
  binding: {
    version: 1,
    runId: "run-1",
    workspaceId: "workspace-1",
    profileId: "ephemeral",
    profileMode: BrowserProfileMode.Ephemeral,
    approvalId: `browser-origin-policy:${"a".repeat(64)}`,
    originPolicy: {
      version: 1,
      topLevelOrigins: ["https://example.com"],
      subresourceOrigins: [],
      denyPrivateNetworks: true,
      serviceWorkers: "block",
    },
    expiresAt: "2099-01-01T00:00:00.000Z",
    nonce: "binding-nonce",
  },
  actionClass: BrowserActionClass.Navigate,
  toolName: "browser_navigate",
  arguments: { url: "https://example.com" },
  deadlineMs: 5_000,
};

const PLAN: BrowserActionPlan = {
  sessionRef: "browser-session://ses_123",
  pageRef: "browser-page://pg_123",
  origin: "https://example.com",
  topLevelOrigin: "https://example.com",
  actionKind: BrowserEffectActionKind.Click,
  elementRef: "e3_0",
  elementFingerprint: "a".repeat(64),
  canonicalFieldsRef:
    "operation://op_00000000-0000-4000-8000-000000000001/args",
  fieldsDigest: "b".repeat(64),
  uploadArtifactRefs: [],
  uploadArtifacts: [],
  precondition: {
    pageGeneration: 3,
    origin: "https://example.com",
    elementFingerprint: "a".repeat(64),
  },
  preconditionDigest: "c".repeat(64),
  userVisibleSummary: "Review browser click on https://example.com.",
};

class FakeWorker implements BrowserWorkerPort, BrowserPrivateEffectWorkerPort {
  dispatches = 0;
  prepares = 0;
  applies = 0;
  reconciles = 0;
  closes = 0;

  listTools(): Promise<readonly BrowserToolSchema[]> {
    return Promise.resolve(BROWSER_TOOL_SCHEMAS);
  }

  dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    this.dispatches += 1;
    return Promise.resolve({
      version: 1,
      requestId: request.requestId,
      sessionId: "ses",
      actionId: "act",
      status: "succeeded",
      safeSummary: "ok",
      artifactRefs: [],
    });
  }

  async prepareAction() {
    this.prepares += 1;
    return {
      preparedRef: "browser-prepared://ses_123/one",
      observedPreconditionDigest: "c".repeat(64),
      preconditionDrift: false,
    };
  }

  async applyPrepared() {
    this.applies += 1;
    return { outcome: BrowserEffectOutcome.Applied };
  }

  async reconcileAction() {
    this.reconciles += 1;
    return { outcome: BrowserEffectOutcome.Indeterminate };
  }

  async closeAll(): Promise<void> {
    this.closes += 1;
  }
}

class FakeRpcProcess implements BrowserWorkerRpcProcess {
  readonly #events = new EventEmitter();
  child: FakeIpcChild | null = null;
  responses: unknown[] = [];

  send(message: unknown): boolean {
    this.responses.push(message);
    queueMicrotask(() => this.child?.emitMessage(message));
    return true;
  }

  on(event: "message", listener: (message: unknown) => void): this {
    this.#events.on(event, listener);
    return this;
  }

  off(event: "message", listener: (message: unknown) => void): this {
    this.#events.off(event, listener);
    return this;
  }

  receive(message: unknown): void {
    queueMicrotask(() => this.#events.emit("message", message));
  }
}

class FakeIpcChild implements BrowserWorkerIpcChild {
  readonly pid = 42;
  readonly stdout = null;
  readonly stderr = null;
  readonly #events = new EventEmitter();

  constructor(readonly workerProcess: FakeRpcProcess) {
    workerProcess.child = this;
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
    this.workerProcess.receive(message);
    queueMicrotask(() => callback?.(null));
    return true;
  }

  kill(): boolean {
    this.#events.emit("exit", 0, "SIGTERM");
    return true;
  }

  emitMessage(message: unknown): void {
    this.#events.emit("message", message);
  }
}

function harness(token = TOKEN) {
  const worker = new FakeWorker();
  const workerProcess = new FakeRpcProcess();
  const child = new FakeIpcChild(workerProcess);
  const uninstall = installBrowserWorkerRpcServer({
    process: workerProcess,
    token,
    version: TEST_BROWSER_VERSION,
    worker,
  });
  return { worker, workerProcess, child, uninstall };
}

describe("authenticated browser worker RPC", () => {
  it("round-trips health, read dispatch, and exact staged-effect methods", async () => {
    const h = harness();
    const client = new BrowserWorkerRpcClient({
      token: TOKEN,
      randomBytes: () => Buffer.alloc(18, 5),
    });
    client.attach(h.child);

    await expect(client.health()).resolves.toEqual({
      healthy: true,
      version: TEST_BROWSER_VERSION,
    });
    await expect(client.listTools()).resolves.toEqual(BROWSER_TOOL_SCHEMAS);
    await expect(client.dispatch(READ)).resolves.toMatchObject({
      requestId: "request-1",
      status: "succeeded",
    });
    const prepared = await client.prepareAction(PLAN);
    await client.applyPrepared(prepared.preparedRef!);
    await client.reconcileAction(prepared.preparedRef!);
    await client.closeAll();

    expect(h.worker.dispatches).toBe(1);
    expect(h.worker.prepares).toBe(1);
    expect(h.worker.applies).toBe(1);
    expect(h.worker.reconciles).toBe(1);
    expect(h.worker.closes).toBe(1);
    expect(JSON.stringify(client)).not.toContain(TOKEN);
    h.uninstall();
    client.dispose();
  });

  it("rejects a main client with the wrong per-boot credential", async () => {
    const h = harness("b".repeat(43));
    const client = new BrowserWorkerRpcClient({
      token: TOKEN,
      randomBytes: () => Buffer.alloc(18, 6),
    });
    client.attach(h.child);

    await expect(client.health()).rejects.toMatchObject({
      code: "unauthorized",
    });
    expect(h.worker.dispatches).toBe(0);
    h.uninstall();
    client.dispose();
  });

  it("has no generic method lane and ignores an unknown RPC method", async () => {
    const h = harness();
    h.workerProcess.receive({
      protocol: BROWSER_WORKER_RPC_PROTOCOL,
      token: TOKEN,
      requestId: "forged",
      method: "evaluate_or_click",
      payload: { script: "document.querySelector('button').click()" },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(h.workerProcess.responses).toEqual([]);
    expect(h.worker.dispatches).toBe(0);
    expect(h.worker.applies).toBe(0);
    h.uninstall();
  });

  it("rejects partial form identity before any worker RPC is sent", async () => {
    const h = harness();
    const client = new BrowserWorkerRpcClient({
      token: TOKEN,
      randomBytes: () => Buffer.alloc(18, 9),
    });
    client.attach(h.child);

    await expect(
      client.prepareAction({
        ...PLAN,
        formActionUrl: "https://example.com/send",
      }),
    ).rejects.toThrow(/form identity/u);

    expect(h.worker.prepares).toBe(0);
    h.uninstall();
    client.dispose();
  });

  it("rejects a submit plan without the exact form-payload digest", async () => {
    const h = harness();
    const client = new BrowserWorkerRpcClient({
      token: TOKEN,
      randomBytes: () => Buffer.alloc(18, 10),
    });
    client.attach(h.child);

    await expect(
      client.prepareAction({
        ...PLAN,
        actionKind: BrowserEffectActionKind.Submit,
        formFingerprint: "d".repeat(64),
        formActionUrl: "https://example.com/send",
        method: "POST",
        precondition: {
          ...PLAN.precondition,
          formFingerprint: "d".repeat(64),
        },
      }),
    ).rejects.toThrow(/form identity/u);

    expect(h.worker.prepares).toBe(0);
    h.uninstall();
    client.dispose();
  });

  it("rejects every in-flight call when the supervised child exits", async () => {
    const workerProcess = new FakeRpcProcess();
    const child = new FakeIpcChild(workerProcess);
    const client = new BrowserWorkerRpcClient({
      token: TOKEN,
      timeoutMs: 10_000,
      randomBytes: () => Buffer.alloc(18, 8),
    });
    client.attach(child);

    const pending = client.health();
    child.kill();
    await expect(pending).rejects.toMatchObject({ code: "worker_exited" });
    client.dispose();
  });
});
