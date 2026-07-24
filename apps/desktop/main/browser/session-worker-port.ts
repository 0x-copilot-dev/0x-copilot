// AC8 agentic browser — worker-side action port.
//
// Implements `BrowserWorkerPort` by mapping typed action requests onto
// per-run `BrowserSession`s. This is the WORKER'S logic: it runs where
// Playwright runs (the supervised child), behind the broker. It caches one
// session per run id, dispatches read-only actions, and tears the session down
// on `browser_close` (or on `closeAll` at teardown).
//
// Session construction is injected (`createSession`) so the profile resolution
// + staging wiring lives on the main side (which owns profile paths), while the
// port owns only the per-run lifecycle. Unit tests inject a fake session
// factory + fake engine and never launch a browser.

import {
  BrowserActionStatus,
  BrowserErrorCode,
  BrowserToolName,
  type BrowserActionPlan,
  type BrowserActionRequest,
  type BrowserEffectReceipt,
  type BrowserPrepareResult,
  type BrowserActionResult,
} from "./protocol";
import type { BrowserSession } from "./browser-session";
import type { BrowserWorkerPort } from "./browser-broker";
import type { BrowserPrivateEffectWorkerPort } from "./private-effect-bridge";
import { browserToolSchemas, type BrowserToolSchema } from "./tool-schemas";

export interface SessionWorkerPortConfig {
  /** Build (and open) a session for a run binding. Main supplies profile paths. */
  readonly createSession: (
    binding: BrowserActionRequest["binding"],
  ) => Promise<BrowserSession>;
  /**
   * Advertise the side-effecting action tools. Only set true when the injected
   * sessions are composed with an approval authority; otherwise the action
   * tools are hidden and the read-only surface is advertised. Default false.
   */
  readonly includeActionTools?: boolean;
}

export class SessionWorkerPort
  implements BrowserWorkerPort, BrowserPrivateEffectWorkerPort
{
  readonly #cfg: SessionWorkerPortConfig;
  readonly #sessions = new Map<string, BrowserSession>();
  readonly #preparedOwners = new Map<string, BrowserSession>();

  constructor(cfg: SessionWorkerPortConfig) {
    this.#cfg = cfg;
  }

  listTools(): Promise<readonly BrowserToolSchema[]> {
    // Generic side-effect schemas remain unadvertised. Passing a legacy
    // includeActionTools flag cannot reopen browser_click outside the staged
    // BrowserPrivateEffectBridge protocol.
    return Promise.resolve(browserToolSchemas({ includeActions: false }));
  }

  async dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    // The broker performs this check too, but the worker is an authority
    // boundary in its own right. An internal caller cannot sidestep the staged
    // protocol by calling `dispatch(browser_click)` directly.
    const advertised = await this.listTools();
    if (!advertised.some((tool) => tool.name === request.toolName)) {
      return {
        version: 1,
        requestId: request.requestId,
        sessionId: "",
        actionId: "",
        status: BrowserActionStatus.Denied,
        safeSummary: "browser side-effecting actions require staged review",
        artifactRefs: [],
        errorCode: BrowserErrorCode.ToolNotImplemented,
      };
    }
    const runId = request.binding.runId;
    let session = this.#sessions.get(runId);
    if (session === undefined) {
      session = await this.#cfg.createSession(request.binding);
      this.#sessions.set(runId, session);
    }
    const result = await session.dispatch(request);
    if (request.toolName === BrowserToolName.Close) {
      this.#sessions.delete(runId);
    }
    return result;
  }

  /** Electron-main private bridge only; absent from the public broker port. */
  async prepareAction(plan: BrowserActionPlan): Promise<BrowserPrepareResult> {
    const session = this.#sessionForOpaqueRef(plan.sessionRef);
    const prepared = await session.prepareAction(plan);
    if (prepared.preparedRef !== undefined) {
      this.#preparedOwners.set(prepared.preparedRef, session);
    }
    return prepared;
  }

  async applyPrepared(preparedRef: string): Promise<BrowserEffectReceipt> {
    const owner = this.#preparedOwners.get(preparedRef);
    this.#preparedOwners.delete(preparedRef);
    return owner === undefined
      ? {
          outcome: "indeterminate",
          safeMessage: "The prepared browser action is no longer available.",
        }
      : owner.applyPrepared(preparedRef);
  }

  async reconcileAction(preparedRef: string): Promise<BrowserEffectReceipt> {
    // This calls no page action. The opaque prepared ref is the only lookup
    // key and its owner remains local to this worker port.
    const owner = this.#preparedOwners.get(preparedRef);
    return owner === undefined
      ? {
          outcome: "indeterminate",
          safeMessage: "The browser action outcome could not be confirmed.",
        }
      : owner.reconcileAction(preparedRef);
  }

  /** Tear down every live session (teardown / cancel / app shutdown). */
  async closeAll(): Promise<void> {
    const sessions = [...this.#sessions.values()];
    this.#sessions.clear();
    this.#preparedOwners.clear();
    for (const session of sessions) {
      try {
        await session.close();
      } catch {
        // Best-effort teardown.
      }
    }
  }

  #sessionForOpaqueRef(sessionRef: string): BrowserSession {
    for (const session of this.#sessions.values()) {
      if (session.sessionRef === sessionRef) return session;
    }
    throw new Error("browser session reference is unavailable");
  }
}
