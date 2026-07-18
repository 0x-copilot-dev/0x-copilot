// AC8 agentic browser — read-only session (worker-side automation logic).
//
// One session drives one isolated browser context for one run. It exposes ONLY
// the read-only tool surface (navigate / snapshot / wait / screenshot / close)
// and enforces, at dispatch time:
//
//   - origin policy on navigate (via egress-policy `evaluateUrlShape`),
//   - generation-bound element refs: any navigation or fresh snapshot bumps the
//     generation, and a ref from a prior generation returns `browser_element_stale`,
//   - bounded snapshots (depth + node caps; input VALUES are never included),
//   - a screenshot size ceiling, with bytes staged (never inlined to the model).
//
// Side-effecting tools (click/type/select/submit/upload/download) are DEFERRED:
// they are not routed here and the provider does not advertise them.

import {
  BrowserActionClass,
  BrowserActionStatus,
  BrowserErrorCode,
  BrowserToolName,
  CloseArgsSchema,
  NavigateArgsSchema,
  ScreenshotArgsSchema,
  SnapshotArgsSchema,
  SCREENSHOT_LIMITS,
  SNAPSHOT_LIMITS,
  WaitArgsSchema,
  type BrowserActionRequest,
  type BrowserActionResult,
  type BrowserOriginPolicy,
  type BrowserSnapshotNode,
} from "./protocol";
import type {
  BrowserEngine,
  EngineContext,
  EnginePage,
  RawAxNode,
} from "./browser-engine";
import { evaluateUrlShape } from "./egress-policy";
import type { ProfileManifest } from "./profile-store";
import type { StagingArea } from "./staging";

export interface BrowserSessionConfig {
  readonly engine: BrowserEngine;
  readonly manifest: ProfileManifest;
  readonly originPolicy: BrowserOriginPolicy;
  readonly staging: StagingArea;
  readonly runId: string;
  readonly randomId?: () => string;
}

export class BrowserSession {
  readonly #cfg: BrowserSessionConfig;
  readonly #approvedOrigins: ReadonlySet<string>;
  readonly #randomId: () => string;
  readonly #sessionId: string;
  #context: EngineContext | null = null;
  #page: EnginePage | null = null;
  #pageId = "";
  #generation = 0;
  #currentOrigin: string | undefined;

  constructor(cfg: BrowserSessionConfig) {
    this.#cfg = cfg;
    this.#approvedOrigins = new Set(cfg.originPolicy.topLevelOrigins);
    this.#randomId =
      cfg.randomId ?? (() => Math.random().toString(36).slice(2, 12));
    this.#sessionId = `ses_${this.#randomId()}`;
  }

  get sessionId(): string {
    return this.#sessionId;
  }

  get generation(): number {
    return this.#generation;
  }

  /** Open the isolated context + first page. Idempotent. */
  async open(): Promise<void> {
    if (this.#context !== null) return;
    this.#context = await this.#cfg.engine.newContext({
      userDataDir: this.#cfg.manifest.userDataDir,
      persistent: this.#cfg.manifest.mode === "persistent",
    });
    this.#page = await this.#context.newPage();
    this.#pageId = `pg_${this.#randomId()}`;
  }

  /** Route a validated action request to its read-only handler. */
  async dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    // Side-effecting classes are DEFERRED; refuse them before touching a page.
    if (
      request.actionClass !== BrowserActionClass.Read &&
      request.actionClass !== BrowserActionClass.Navigate
    ) {
      return this.#result(request, {
        status: BrowserActionStatus.Denied,
        errorCode: BrowserErrorCode.ToolNotImplemented,
        safeSummary: "side-effecting actions are not enabled",
      });
    }
    if (this.#page === null) await this.open();

    switch (request.toolName) {
      case BrowserToolName.Navigate:
        return this.#navigate(request);
      case BrowserToolName.Snapshot:
        return this.#snapshot(request);
      case BrowserToolName.Wait:
        return this.#wait(request);
      case BrowserToolName.Screenshot:
        return this.#screenshot(request);
      case BrowserToolName.Close:
        return this.#close(request);
      default:
        return this.#result(request, {
          status: BrowserActionStatus.Denied,
          errorCode: BrowserErrorCode.ToolNotImplemented,
          safeSummary: "unknown or unimplemented tool",
        });
    }
  }

  async #navigate(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = NavigateArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);

    const shape = evaluateUrlShape(parsed.data.url, this.#approvedOrigins);
    if (!shape.allowed || shape.origin === undefined) {
      return this.#result(request, {
        status: BrowserActionStatus.Denied,
        errorCode:
          shape.reason === "origin_not_approved"
            ? BrowserErrorCode.OriginApprovalRequired
            : BrowserErrorCode.NetworkDenied,
        safeSummary: `navigation denied: ${shape.reason}`,
      });
    }
    const page = this.#requirePage();
    const outcome = await page.goto(parsed.data.url, {
      timeoutMs: request.deadlineMs,
    });
    // A navigation invalidates every prior element ref.
    this.#generation += 1;
    this.#currentOrigin = shape.origin;
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: shape.origin,
      safeSummary: `navigated (${outcome.status})`,
      nextGeneration: this.#generation,
    });
  }

  async #snapshot(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = SnapshotArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const page = this.#requirePage();
    const raw = await page.accessibilitySnapshot();
    // A fresh snapshot mints a new generation of refs.
    this.#generation += 1;
    const depth = Math.min(
      parsed.data.depth ?? SNAPSHOT_LIMITS.maxDepth,
      SNAPSHOT_LIMITS.maxDepth,
    );
    const tree = raw === null ? undefined : this.#bound(raw, depth);
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: "snapshot captured",
      nextGeneration: this.#generation,
      snapshot: tree,
    });
  }

  async #wait(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = WaitArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const page = this.#requirePage();
    const timeout = Math.min(parsed.data.timeoutMs ?? 10_000, 30_000);
    try {
      await page.waitFor(parsed.data.condition, timeout);
    } catch {
      return this.#result(request, {
        status: BrowserActionStatus.Failed,
        errorCode: BrowserErrorCode.ActionTimeout,
        safeSummary: "wait condition not met before timeout",
      });
    }
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `wait ${parsed.data.condition} satisfied`,
    });
  }

  async #screenshot(
    request: BrowserActionRequest,
  ): Promise<BrowserActionResult> {
    const parsed = ScreenshotArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const page = this.#requirePage();
    const bytes = await page.screenshot({
      fullPage: parsed.data.fullPage ?? false,
    });
    if (bytes.byteLength > SCREENSHOT_LIMITS.maxBytes) {
      return this.#result(request, {
        status: BrowserActionStatus.Failed,
        errorCode: BrowserErrorCode.ArtifactQuotaExceeded,
        safeSummary: "screenshot exceeds the artifact byte ceiling",
      });
    }
    const staged = await this.#cfg.staging.stage("screenshot", bytes);
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: "screenshot staged",
      artifactRefs: [staged.ref],
    });
  }

  async #close(request: BrowserActionRequest): Promise<BrowserActionResult> {
    CloseArgsSchema.parse(request.arguments ?? {});
    await this.close();
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      safeSummary: "session closed",
    });
  }

  /** Tear down the context and clean the run staging area. Best-effort. */
  async close(): Promise<void> {
    const ctx = this.#context;
    this.#context = null;
    this.#page = null;
    if (ctx !== null) {
      try {
        await ctx.close();
      } catch {
        // Best-effort teardown.
      }
    }
    await this.#cfg.staging.cleanup();
  }

  /**
   * Convert a raw accessibility tree into a bounded snapshot, assigning
   * generation-bound refs (`e<gen>_<n>`) and dropping input VALUES. Enforces
   * the depth + node caps.
   */
  #bound(root: RawAxNode, maxDepth: number): BrowserSnapshotNode {
    let count = 0;
    const gen = this.#generation;
    const convert = (node: RawAxNode, depth: number): BrowserSnapshotNode => {
      const out: BrowserSnapshotNode = {
        ref: `e${gen}_${count}`,
        role: node.role,
        // The accessible NAME (label), never `node.value` (input contents).
        name: node.name ?? "",
      };
      count += 1;
      if (
        depth < maxDepth &&
        count < SNAPSHOT_LIMITS.maxNodes &&
        node.children &&
        node.children.length > 0
      ) {
        const children: BrowserSnapshotNode[] = [];
        for (const child of node.children) {
          if (count >= SNAPSHOT_LIMITS.maxNodes) break;
          children.push(convert(child, depth + 1));
        }
        if (children.length > 0) out.children = children;
      }
      return out;
    };
    return convert(root, 0);
  }

  #requirePage(): EnginePage {
    if (this.#page === null) throw new Error("session not open");
    return this.#page;
  }

  #invalid(request: BrowserActionRequest): BrowserActionResult {
    return this.#result(request, {
      status: BrowserActionStatus.Denied,
      errorCode: BrowserErrorCode.InvalidRequest,
      safeSummary: "invalid tool arguments",
    });
  }

  #result(
    request: BrowserActionRequest,
    fields: {
      status: BrowserActionResult["status"];
      safeSummary: string;
      currentOrigin?: string;
      errorCode?: string;
      nextGeneration?: number;
      artifactRefs?: readonly string[];
      snapshot?: BrowserSnapshotNode;
    },
  ): BrowserActionResult {
    return {
      version: 1,
      requestId: request.requestId,
      sessionId: this.#sessionId,
      actionId: `act_${this.#randomId()}`,
      status: fields.status,
      currentOrigin: fields.currentOrigin,
      safeSummary: fields.safeSummary,
      artifactRefs: fields.artifactRefs ?? [],
      nextGeneration: fields.nextGeneration,
      errorCode: fields.errorCode,
      snapshot: fields.snapshot,
    };
  }
}
