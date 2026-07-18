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
  BrowserToolName,
  type BrowserActionRequest,
  type BrowserActionResult,
} from "./protocol";
import type { BrowserSession } from "./browser-session";
import type { BrowserWorkerPort } from "./browser-broker";
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

export class SessionWorkerPort implements BrowserWorkerPort {
  readonly #cfg: SessionWorkerPortConfig;
  readonly #sessions = new Map<string, BrowserSession>();

  constructor(cfg: SessionWorkerPortConfig) {
    this.#cfg = cfg;
  }

  listTools(): Promise<readonly BrowserToolSchema[]> {
    return Promise.resolve(
      browserToolSchemas({ includeActions: this.#cfg.includeActionTools }),
    );
  }

  async dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
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

  /** Tear down every live session (teardown / cancel / app shutdown). */
  async closeAll(): Promise<void> {
    const sessions = [...this.#sessions.values()];
    this.#sessions.clear();
    for (const session of sessions) {
      try {
        await session.close();
      } catch {
        // Best-effort teardown.
      }
    }
  }
}
