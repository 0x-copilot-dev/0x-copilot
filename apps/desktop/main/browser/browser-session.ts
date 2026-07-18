// AC8 agentic browser — session (worker-side automation logic).
//
// One session drives one isolated browser context for one run. It exposes the
// read tool surface (navigate / snapshot / wait / screenshot / close) AND the
// action layer (click / type / select / submit / download), and enforces, at
// dispatch time:
//
//   - origin policy on navigate (via egress-policy `evaluateUrlShape`),
//   - generation-bound element refs: any navigation, fresh snapshot, or DOM-
//     mutating action bumps the generation, and a ref from a prior generation
//     returns `browser_element_stale`,
//   - APPROVAL GATING: every side-effecting action (click/type/select/submit/
//     download) MUST clear the injected `BrowserApprovalPort` before it
//     dispatches; reads never touch it. Fails CLOSED when no port is wired,
//   - bounded snapshots (depth + node caps; input VALUES are never included),
//   - a screenshot size ceiling, with bytes staged (never inlined to the model),
//   - downloads captured into the per-RUN staging directory (never an arbitrary
//     host path), with executable-shaped / oversized content denied.
//
// Upload remains deferred (it needs an AC5 object-ref grant).

import {
  BrowserActionClass,
  BrowserActionStatus,
  BrowserErrorCode,
  BrowserToolName,
  ClickArgsSchema,
  CloseArgsSchema,
  DownloadArgsSchema,
  NavigateArgsSchema,
  ScreenshotArgsSchema,
  SelectArgsSchema,
  SnapshotArgsSchema,
  SubmitArgsSchema,
  TypeArgsSchema,
  SCREENSHOT_LIMITS,
  SNAPSHOT_LIMITS,
  WaitArgsSchema,
  actionRequiresApproval,
  classifyTool,
  type BrowserActionRequest,
  type BrowserActionResult,
  type BrowserOriginPolicy,
  type BrowserSnapshotNode,
} from "./protocol";
import type {
  BrowserEngine,
  ElementTarget,
  EngineContext,
  EnginePage,
  RawAxNode,
} from "./browser-engine";
import {
  BrowserApprovalDecision,
  type BrowserApprovalPort,
} from "./action-policy";
import {
  downloadExtension,
  evaluateDownloadPolicy,
  sanitizeDownloadName,
  sha256Hex,
} from "./downloads";
import { evaluateUrlShape } from "./egress-policy";
import type { ProfileManifest } from "./profile-store";
import type { StagingArea } from "./staging";

export interface BrowserSessionConfig {
  readonly engine: BrowserEngine;
  readonly manifest: ProfileManifest;
  readonly originPolicy: BrowserOriginPolicy;
  readonly staging: StagingArea;
  readonly runId: string;
  /**
   * Authority consulted before every side-effecting action. When undefined the
   * session fails CLOSED — side effects are denied with
   * `browser_action_approval_required`. Reads never consult it.
   */
  readonly approval?: BrowserApprovalPort;
  /** Open the context with downloads enabled (action layer). Default false. */
  readonly acceptDownloads?: boolean;
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
  /** ref -> redacted {role,name} for the CURRENT generation only. */
  readonly #refIndex = new Map<string, { role: string; name: string }>();

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
      acceptDownloads: this.#cfg.acceptDownloads ?? false,
    });
    this.#page = await this.#context.newPage();
    this.#pageId = `pg_${this.#randomId()}`;
  }

  /** Route a validated action request to its handler. */
  async dispatch(request: BrowserActionRequest): Promise<BrowserActionResult> {
    // The tool name is the AUTHORITATIVE source of the action class — the
    // caller-supplied `actionClass` is untrusted and NOT used to decide
    // routing/approval (a mislabelled request cannot smuggle a side effect
    // through the read path). An unknown/deferred tool is refused up front.
    if (classifyTool(request.toolName) === null) {
      return this.#result(request, {
        status: BrowserActionStatus.Denied,
        errorCode: BrowserErrorCode.ToolNotImplemented,
        safeSummary: "unknown or unimplemented tool",
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
      case BrowserToolName.Click:
        return this.#click(request);
      case BrowserToolName.Type:
        return this.#type(request);
      case BrowserToolName.Select:
        return this.#select(request);
      case BrowserToolName.Submit:
        return this.#submit(request);
      case BrowserToolName.Download:
        return this.#download(request);
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
    this.#refIndex.clear();
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
    // A fresh snapshot mints a new generation of refs and replaces the index
    // the action layer resolves refs against.
    this.#generation += 1;
    this.#refIndex.clear();
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

  // --- action layer (side-effecting; each clears the approval gate) --------

  async #click(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = ClickArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const target = this.#resolveRef(parsed.data.ref);
    if (target === null) return this.#stale(request);
    const gate = await this.#gate(request, BrowserActionClass.ExternalEffect, {
      targetLabel: this.#label(target),
      summary: `click "${this.#label(target)}"`,
    });
    if (gate !== null) return gate;
    await this.#requirePage().clickRef(target);
    // A click may navigate or mutate the DOM — invalidate the generation.
    this.#bumpGeneration();
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `clicked ${this.#label(target)}`,
      nextGeneration: this.#generation,
    });
  }

  async #type(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = TypeArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const target = this.#resolveRef(parsed.data.ref);
    if (target === null) return this.#stale(request);
    // The typed text is a sensitive argument: it is NEVER echoed into the
    // approval summary, the safe summary, or any result field.
    const gate = await this.#gate(request, BrowserActionClass.Input, {
      targetLabel: this.#label(target),
      summary: `type into "${this.#label(target)}"`,
    });
    if (gate !== null) return gate;
    await this.#requirePage().fillRef(target, parsed.data.text);
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `typed into ${this.#label(target)} (${parsed.data.text.length} chars)`,
    });
  }

  async #select(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = SelectArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const target = this.#resolveRef(parsed.data.ref);
    if (target === null) return this.#stale(request);
    const gate = await this.#gate(request, BrowserActionClass.Input, {
      targetLabel: this.#label(target),
      summary: `select in "${this.#label(target)}"`,
    });
    if (gate !== null) return gate;
    await this.#requirePage().selectRef(target, parsed.data.value);
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `selected option in ${this.#label(target)}`,
    });
  }

  async #submit(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = SubmitArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const target = this.#resolveRef(parsed.data.ref);
    if (target === null) return this.#stale(request);
    const gate = await this.#gate(request, BrowserActionClass.Submit, {
      targetLabel: this.#label(target),
      summary: `submit via "${this.#label(target)}"`,
    });
    if (gate !== null) return gate;
    await this.#requirePage().submitRef(target);
    this.#bumpGeneration();
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `submitted ${this.#label(target)}`,
      nextGeneration: this.#generation,
    });
  }

  async #download(request: BrowserActionRequest): Promise<BrowserActionResult> {
    const parsed = DownloadArgsSchema.safeParse(request.arguments);
    if (!parsed.success) return this.#invalid(request);
    const target = this.#resolveRef(parsed.data.ref);
    if (target === null) return this.#stale(request);
    const gate = await this.#gate(request, BrowserActionClass.Download, {
      targetLabel: this.#label(target),
      summary: `download via "${this.#label(target)}"`,
    });
    if (gate !== null) return gate;

    const capture = await this.#requirePage().downloadViaRef(target, {
      timeoutMs: request.deadlineMs,
    });
    const sanitizedName = sanitizeDownloadName(capture.suggestedName);
    const decision = evaluateDownloadPolicy({
      sanitizedName,
      byteLength: capture.body.byteLength,
    });
    if (!decision.allowed) {
      // The captured bytes are dropped here — never staged, never written to
      // any host path.
      return this.#result(request, {
        status: BrowserActionStatus.Denied,
        errorCode: BrowserErrorCode.DownloadDenied,
        safeSummary: `download denied: ${decision.reason}`,
      });
    }
    // Bytes land ONLY under the per-run staging directory with a generated
    // name; the site-suggested name is sanitized metadata used only for the
    // (also sanitized) extension.
    const ext =
      sanitizedName !== null ? downloadExtension(sanitizedName) : undefined;
    const staged = await this.#cfg.staging.stage("download", capture.body, {
      ext: ext === "" ? undefined : ext,
    });
    const sha = sha256Hex(capture.body);
    return this.#result(request, {
      status: BrowserActionStatus.Succeeded,
      currentOrigin: this.#currentOrigin,
      safeSummary: `downloaded ${staged.byteLength} bytes (sha256 ${sha.slice(0, 12)})`,
      artifactRefs: [staged.ref],
    });
  }

  /**
   * The approval gate. Reads pass through (returns null). A side-effecting
   * action MUST clear the injected `BrowserApprovalPort`; when no port is wired
   * or the decision is not `approved`, it fails CLOSED and returns a denied
   * result the caller returns directly.
   */
  async #gate(
    request: BrowserActionRequest,
    actionClass: BrowserActionClass,
    detail: { targetLabel: string; summary: string },
  ): Promise<BrowserActionResult | null> {
    if (!actionRequiresApproval(actionClass)) return null;
    const port = this.#cfg.approval;
    const denied = (summary: string): BrowserActionResult =>
      this.#result(request, {
        status: BrowserActionStatus.Denied,
        errorCode: BrowserErrorCode.ActionApprovalRequired,
        safeSummary: summary,
      });
    if (port === undefined) {
      return denied(
        "side-effecting action requires approval (none configured)",
      );
    }
    const decision = await port.requestApproval({
      requestId: request.requestId,
      runId: request.binding.runId,
      workspaceId: request.binding.workspaceId,
      approvalId: request.binding.approvalId,
      toolName: request.toolName,
      actionClass,
      currentOrigin: this.#currentOrigin,
      targetLabel: detail.targetLabel,
      summary: detail.summary,
    });
    if (decision !== BrowserApprovalDecision.Approved) {
      return denied("side-effecting action was not approved");
    }
    return null;
  }

  /** Resolve a ref against the CURRENT generation; null when stale/unknown. */
  #resolveRef(ref: string): ElementTarget | null {
    const entry = this.#refIndex.get(ref);
    if (entry === undefined) return null;
    return { ref, role: entry.role, name: entry.name };
  }

  #bumpGeneration(): void {
    this.#generation += 1;
    this.#refIndex.clear();
  }

  #label(target: ElementTarget): string {
    return target.name !== "" ? target.name : target.role;
  }

  #stale(request: BrowserActionRequest): BrowserActionResult {
    return this.#result(request, {
      status: BrowserActionStatus.Denied,
      errorCode: BrowserErrorCode.ElementStale,
      safeSummary: "element ref is stale; take a fresh snapshot",
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
      const ref = `e${gen}_${count}`;
      const name = node.name ?? "";
      const out: BrowserSnapshotNode = {
        ref,
        role: node.role,
        // The accessible NAME (label), never `node.value` (input contents).
        name,
      };
      // Record the ref so the action layer can resolve it to a role/name
      // locator for the CURRENT generation (redacted label only, no value).
      this.#refIndex.set(ref, { role: node.role, name });
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
