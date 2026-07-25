// Authenticated Electron-main <-> browser-worker RPC.
//
// The child-process IPC channel is private OS plumbing, but it is still an
// authority boundary: every request carries a per-boot 256-bit credential and
// a closed method name. The protocol has no generic "evaluate", "click", or
// arbitrary method lane. Both peers runtime-validate all consequential values.

import { randomBytes as nodeRandomBytes, timingSafeEqual } from "node:crypto";

import { z } from "zod";

import type { BrowserWorkerPort } from "./browser-broker";
import type { WorkerChildStdio, WorkerHealth } from "./browser-supervisor";
import type { BrowserPrivateEffectWorkerPort } from "./private-effect-bridge";
import {
  BROWSER_PROTOCOL_VERSION,
  BrowserActionPlanSchema,
  BrowserActionRequestSchema,
  BrowserActionResultSchema,
  BrowserEffectReceiptSchema,
  BrowserPrepareResultSchema,
  OpaqueBrowserRefSchema,
  type BrowserActionPlan,
  type BrowserActionRequest,
  type BrowserActionResult,
  type BrowserEffectReceipt,
  type BrowserPrepareResult,
} from "./protocol";
import { browserToolSchemas, type BrowserToolSchema } from "./tool-schemas";

export const BROWSER_WORKER_RPC_TOKEN_ENV = "BROWSER_WORKER_RPC_TOKEN";
export const BROWSER_WORKER_RPC_PROTOCOL = 1 as const;

const DEFAULT_RPC_TIMEOUT_MS = 35_000;
const RPC_ID_BYTES = 18;

const RpcMethod = {
  Health: "health",
  ListTools: "list_tools",
  DispatchRead: "dispatch_read",
  PrepareEffect: "prepare_effect",
  ApplyPrepared: "apply_prepared",
  ReconcileEffect: "reconcile_effect",
  CloseAll: "close_all",
} as const;
type RpcMethod = (typeof RpcMethod)[keyof typeof RpcMethod];

const RpcRequestSchema = z
  .object({
    protocol: z.literal(BROWSER_WORKER_RPC_PROTOCOL),
    token: z.string().min(32).max(256),
    requestId: z.string().min(1).max(128),
    method: z.enum([
      RpcMethod.Health,
      RpcMethod.ListTools,
      RpcMethod.DispatchRead,
      RpcMethod.PrepareEffect,
      RpcMethod.ApplyPrepared,
      RpcMethod.ReconcileEffect,
      RpcMethod.CloseAll,
    ]),
    payload: z.unknown().optional(),
  })
  .strict();

const RpcResponseSchema = z
  .object({
    protocol: z.literal(BROWSER_WORKER_RPC_PROTOCOL),
    requestId: z.string().min(1).max(128),
    ok: z.boolean(),
    result: z.unknown().optional(),
    error: z
      .object({
        code: z.string().min(1).max(128),
        message: z.string().min(1).max(512),
      })
      .strict()
      .optional(),
  })
  .strict();

export interface BrowserWorkerIpcChild {
  readonly pid?: number | undefined;
  readonly stdout: WorkerChildStdio | null;
  readonly stderr: WorkerChildStdio | null;
  on(
    event: "exit",
    listener: (code: number | null, signal: string | null) => void,
  ): unknown;
  on(event: "error", listener: (err: Error) => void): unknown;
  on(event: "message", listener: (message: unknown) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
  send(message: unknown, callback?: (error: Error | null) => void): boolean;
}

export interface BrowserWorkerRpcProcess {
  send?(message: unknown): boolean;
  on(event: "message", listener: (message: unknown) => void): unknown;
  off(event: "message", listener: (message: unknown) => void): unknown;
}

export class BrowserWorkerRpcError extends Error {
  readonly code: string;

  constructor(code: string, message = "browser worker RPC failed") {
    super(message);
    this.name = "BrowserWorkerRpcError";
    this.code = code;
  }
}

interface PendingRpc {
  readonly resolve: (value: unknown) => void;
  readonly reject: (error: Error) => void;
  readonly timer: NodeJS.Timeout;
  readonly generation: number;
}

export interface BrowserWorkerRpcClientConfig {
  readonly token: string;
  readonly timeoutMs?: number;
  readonly randomBytes?: (size: number) => Buffer;
}

/**
 * Main-side typed client. `attach` is called for every supervised child spawn;
 * a crash rejects every in-flight call before the supervisor creates a new
 * generation. The worker credential has no getter and is never returned by the
 * browser broker.
 */
export class BrowserWorkerRpcClient
  implements BrowserWorkerPort, BrowserPrivateEffectWorkerPort
{
  readonly #token: Buffer;
  readonly #timeoutMs: number;
  readonly #randomBytes: (size: number) => Buffer;
  readonly #pending = new Map<string, PendingRpc>();
  #child: BrowserWorkerIpcChild | null = null;
  #generation = 0;

  constructor(config: BrowserWorkerRpcClientConfig) {
    const token = Buffer.from(config.token, "utf8");
    if (token.byteLength < 32) {
      throw new BrowserWorkerRpcError(
        "invalid_credential",
        "browser worker credential is invalid",
      );
    }
    this.#token = token;
    this.#timeoutMs = config.timeoutMs ?? DEFAULT_RPC_TIMEOUT_MS;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
  }

  attach(child: BrowserWorkerIpcChild): void {
    this.#rejectAll("worker_replaced");
    this.#child = child;
    this.#generation += 1;
    const generation = this.#generation;
    child.on("message", (message) => this.#onMessage(message, generation));
    child.on("exit", () => this.#onChildGone(child, generation));
    child.on("error", () => this.#onChildGone(child, generation));
  }

  async health(): Promise<WorkerHealth> {
    const result = await this.#call(RpcMethod.Health);
    const parsed = z
      .object({
        healthy: z.literal(true),
        version: z.string().min(1).max(128),
      })
      .strict()
      .parse(result);
    return parsed;
  }

  async listTools(): Promise<readonly BrowserToolSchema[]> {
    const result = await this.#call(RpcMethod.ListTools);
    const parsed = z
      .object({ tools: z.array(z.unknown()) })
      .strict()
      .parse(result);
    // The worker is authoritative for availability, but the schema set is
    // closed on both sides. Exact-plan action schemas may be discovered here;
    // `dispatch_read` still rejects them and only the private RPC can apply.
    const allowed = new Map(
      browserToolSchemas({ includeActions: true }).map(
        (tool) => [tool.name, tool] as const,
      ),
    );
    const tools: BrowserToolSchema[] = [];
    for (const raw of parsed.tools) {
      if (typeof raw !== "object" || raw === null) {
        throw new BrowserWorkerRpcError("invalid_response");
      }
      const name = (raw as Record<string, unknown>).name;
      const expected = typeof name === "string" ? allowed.get(name) : undefined;
      if (
        expected === undefined ||
        JSON.stringify(raw) !== JSON.stringify(expected)
      ) {
        throw new BrowserWorkerRpcError("invalid_tool_schema");
      }
      tools.push(expected);
    }
    return tools;
  }

  async dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const safe = BrowserActionRequestSchema.parse(request);
    return BrowserActionResultSchema.parse(
      await this.#call(RpcMethod.DispatchRead, safe),
    );
  }

  async prepareAction(plan: BrowserActionPlan): Promise<BrowserPrepareResult> {
    const safe = BrowserActionPlanSchema.parse(plan);
    return BrowserPrepareResultSchema.parse(
      await this.#call(RpcMethod.PrepareEffect, safe),
    );
  }

  async applyPrepared(preparedRef: string): Promise<BrowserEffectReceipt> {
    const safeRef = OpaqueBrowserRefSchema.parse(preparedRef);
    return BrowserEffectReceiptSchema.parse(
      await this.#call(RpcMethod.ApplyPrepared, { preparedRef: safeRef }),
    );
  }

  async reconcileAction(preparedRef: string): Promise<BrowserEffectReceipt> {
    const safeRef = OpaqueBrowserRefSchema.parse(preparedRef);
    return BrowserEffectReceiptSchema.parse(
      await this.#call(RpcMethod.ReconcileEffect, { preparedRef: safeRef }),
    );
  }

  async closeAll(): Promise<void> {
    await this.#call(RpcMethod.CloseAll);
  }

  dispose(): void {
    this.#child = null;
    this.#generation += 1;
    this.#rejectAll("client_disposed");
    this.#token.fill(0);
  }

  #call(method: RpcMethod, payload?: unknown): Promise<unknown> {
    const child = this.#child;
    if (child === null) {
      return Promise.reject(new BrowserWorkerRpcError("worker_unavailable"));
    }
    const requestId = this.#randomBytes(RPC_ID_BYTES).toString("base64url");
    const generation = this.#generation;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(requestId);
        reject(new BrowserWorkerRpcError("timeout"));
      }, this.#timeoutMs);
      this.#pending.set(requestId, {
        resolve,
        reject,
        timer,
        generation,
      });
      const accepted = child.send(
        {
          protocol: BROWSER_WORKER_RPC_PROTOCOL,
          token: this.#token.toString("utf8"),
          requestId,
          method,
          ...(payload === undefined ? {} : { payload }),
        },
        (error) => {
          if (error === null) return;
          const pending = this.#pending.get(requestId);
          if (pending === undefined) return;
          clearTimeout(pending.timer);
          this.#pending.delete(requestId);
          pending.reject(new BrowserWorkerRpcError("send_failed"));
        },
      );
      if (!accepted) {
        const pending = this.#pending.get(requestId);
        if (pending !== undefined) {
          clearTimeout(pending.timer);
          this.#pending.delete(requestId);
          pending.reject(new BrowserWorkerRpcError("backpressure"));
        }
      }
    });
  }

  #onMessage(message: unknown, generation: number): void {
    if (generation !== this.#generation) return;
    const parsed = RpcResponseSchema.safeParse(message);
    if (!parsed.success) return;
    const pending = this.#pending.get(parsed.data.requestId);
    if (pending === undefined || pending.generation !== generation) return;
    clearTimeout(pending.timer);
    this.#pending.delete(parsed.data.requestId);
    if (!parsed.data.ok) {
      pending.reject(
        new BrowserWorkerRpcError(
          parsed.data.error?.code ?? "worker_error",
          parsed.data.error?.message ?? "browser worker RPC failed",
        ),
      );
      return;
    }
    pending.resolve(parsed.data.result);
  }

  #onChildGone(child: BrowserWorkerIpcChild, generation: number): void {
    if (this.#child !== child || generation !== this.#generation) return;
    this.#child = null;
    this.#rejectAll("worker_exited");
  }

  #rejectAll(code: string): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new BrowserWorkerRpcError(code));
    }
    this.#pending.clear();
  }
}

export interface BrowserWorkerRpcServerConfig {
  readonly process: BrowserWorkerRpcProcess;
  readonly token: string;
  readonly version: string;
  readonly worker: BrowserWorkerPort &
    BrowserPrivateEffectWorkerPort & {
      closeAll(): Promise<void>;
    };
}

/** Install the worker-side closed dispatcher after Playwright is ready. */
export function installBrowserWorkerRpcServer(
  config: BrowserWorkerRpcServerConfig,
): () => void {
  const expectedToken = Buffer.from(config.token, "utf8");
  if (expectedToken.byteLength < 32 || config.process.send === undefined) {
    throw new BrowserWorkerRpcError("invalid_server_configuration");
  }
  const listener = (message: unknown): void => {
    const parsed = RpcRequestSchema.safeParse(message);
    if (!parsed.success) return;
    if (!tokenMatches(parsed.data.token, expectedToken)) {
      config.process.send?.(
        rpcError(parsed.data.requestId, "unauthorized", "unauthorized"),
      );
      return;
    }
    void dispatchWorkerRequest(config, parsed.data)
      .then((result) => {
        config.process.send?.({
          protocol: BROWSER_WORKER_RPC_PROTOCOL,
          requestId: parsed.data.requestId,
          ok: true,
          ...(result === undefined ? {} : { result }),
        });
      })
      .catch(() => {
        config.process.send?.(
          rpcError(
            parsed.data.requestId,
            "operation_failed",
            "browser worker operation failed",
          ),
        );
      });
  };
  config.process.on("message", listener);
  return () => {
    config.process.off("message", listener);
    expectedToken.fill(0);
  };
}

async function dispatchWorkerRequest(
  config: BrowserWorkerRpcServerConfig,
  request: z.infer<typeof RpcRequestSchema>,
): Promise<unknown> {
  switch (request.method) {
    case RpcMethod.Health:
      return { healthy: true, version: config.version };
    case RpcMethod.ListTools:
      return { tools: await config.worker.listTools() };
    case RpcMethod.DispatchRead:
      return config.worker.dispatch(
        BrowserActionRequestSchema.parse(request.payload),
      );
    case RpcMethod.PrepareEffect:
      return config.worker.prepareAction(
        BrowserActionPlanSchema.parse(request.payload),
      );
    case RpcMethod.ApplyPrepared: {
      const payload = z
        .object({ preparedRef: OpaqueBrowserRefSchema })
        .strict()
        .parse(request.payload);
      return config.worker.applyPrepared(payload.preparedRef);
    }
    case RpcMethod.ReconcileEffect: {
      const payload = z
        .object({ preparedRef: OpaqueBrowserRefSchema })
        .strict()
        .parse(request.payload);
      return config.worker.reconcileAction(payload.preparedRef);
    }
    case RpcMethod.CloseAll:
      await config.worker.closeAll();
      return { closed: true };
  }
}

function rpcError(
  requestId: string,
  code: string,
  message: string,
): z.infer<typeof RpcResponseSchema> {
  return {
    protocol: BROWSER_WORKER_RPC_PROTOCOL,
    requestId,
    ok: false,
    error: { code, message },
  };
}

function tokenMatches(value: string, expected: Buffer): boolean {
  const provided = Buffer.from(value, "utf8");
  return (
    provided.byteLength === expected.byteLength &&
    timingSafeEqual(provided, expected)
  );
}

export const BROWSER_WORKER_RPC_TRANSPORT_VERSION = BROWSER_PROTOCOL_VERSION;
