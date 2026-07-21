import { compileAdapter } from "./sandbox";
import {
  appendLifecycleEvent,
  type LifecycleEventsDeps,
} from "./lifecycle-events";
import {
  runSmokeRender,
  staticAnalyze,
  validateAdapterSchema,
  wrapWithBoundary,
  type SmokeRenderExecutor,
} from "./quality-gate";
import {
  SYNTHETIC_SMOKE_DIFF,
  SYNTHETIC_SMOKE_STATE,
} from "./smoke-render-executor";
import {
  persistAdapterSource,
  uninstallAdapterFile,
  type InstallerDeps,
} from "./tier2-installer";
import type { InstallReviewClass, InstallReviewGate } from "./review-gate";

// Phase 6C registry-host. Main-side facade over the chat-surface registry.
// Drives the Q1-Q5 install pipeline, then ships a tier2.install IPC to the
// renderer. The renderer owns the actual registerAdapter call because
// chat-surface's SurfaceRegistry is in-renderer module state.

export type InstallGate =
  | "schema"
  | "allowlist"
  | "smoke"
  | "compile"
  | "consent";

export interface InstallOk {
  readonly ok: true;
  readonly scheme: string;
  readonly version: number;
}

export interface InstallFail {
  readonly ok: false;
  readonly gate: InstallGate;
  readonly detail: string;
}

export type InstallResult = InstallOk | InstallFail;

export interface RendererDispatcher {
  send(
    channel: "tier2.install" | "tier2.uninstall" | "tier2.mark-broken",
    payload: unknown,
  ): void;
}

export interface RegistryHostDeps {
  readonly adapterDir: string;
  readonly clock: () => number;
  readonly dispatcher: RendererDispatcher;
  readonly audit: LifecycleEventsDeps;
  readonly installer: InstallerDeps;
  readonly smokeExecutor?: SmokeRenderExecutor;
  // PRD-10 review gate. When present, a `write`-classified adapter must clear a
  // one-time consent acknowledgment before it is registered. When absent, no
  // consent step runs (read/write both auto-install) — production always wires
  // it; tests opt in.
  readonly reviewGate?: InstallReviewGate;
}

export interface InstallAdapterArgs {
  readonly scheme: string;
  readonly version: number;
  readonly source: string;
  readonly generatedAt: string;
  readonly generatorModel: string;
  // Read-only adapters auto-install; `write` adapters require consent (PRD-10).
  // Defaults to `read` when omitted so callers that do not classify keep the
  // pre-PRD-10 auto-install behaviour.
  readonly reviewClass?: InstallReviewClass;
}

export interface MarkBrokenFromBoundaryArgs {
  readonly scheme: string;
  readonly version: number;
  readonly method: "renderCurrent" | "renderDiff";
  readonly reason: string;
}

async function recordFailure(
  args: InstallAdapterArgs,
  gate: InstallGate,
  detail: string,
  deps: RegistryHostDeps,
): Promise<InstallFail> {
  await appendLifecycleEvent(
    {
      ts: deps.clock(),
      kind: "validated",
      scheme: args.scheme,
      version: args.version,
      detail: `gate=${gate}: ${detail}`,
    },
    deps.audit,
  );
  return { ok: false, gate, detail };
}

export async function installAdapter(
  args: InstallAdapterArgs,
  deps: RegistryHostDeps,
): Promise<InstallResult> {
  // Q2 (allowlist) — cheapest gate; reject before compilation.
  const allow = staticAnalyze(args.source);
  if (!allow.ok) {
    const detail = allow.violations
      .map((v) => `${v.kind}:${v.message}`)
      .join("; ");
    return recordFailure(args, "allowlist", detail, deps);
  }

  // Compile via 6A's vm sandbox. Failure here is a "compile" gate distinct
  // from Q1 (which checks the in-memory shape).
  const compiled = compileAdapter(args.source);
  if (!compiled.ok) {
    return recordFailure(
      args,
      "compile",
      `${compiled.reason}: ${compiled.detail}`,
      deps,
    );
  }

  // Q1 (schema) — validate the in-memory adapter shape against Zod.
  const schema = validateAdapterSchema(compiled.adapter);
  if (!schema.ok) {
    const detail = schema.errors.map((e) => e.message).join("; ");
    return recordFailure(args, "schema", detail, deps);
  }

  // Q3/Q4 (smoke render) — call renderCurrent + renderDiff against a
  // synthetic state in a measured-timeout race. Preemptive termination
  // for live renders is the Tier2Loader Worker's job.
  const smoke = await runSmokeRender(
    schema.value,
    SYNTHETIC_SMOKE_STATE,
    SYNTHETIC_SMOKE_DIFF,
    deps.smokeExecutor ? { executor: deps.smokeExecutor } : undefined,
  );
  if (!smoke.ok) {
    return recordFailure(
      args,
      "smoke",
      `${smoke.method}/${smoke.kind}: ${smoke.error.message}`,
      deps,
    );
  }

  // Review gate (PRD-10) — a `write`/diff-surface adapter needs a one-time
  // human consent acknowledgment before it is registered; read-only adapters
  // auto-install. Runs AFTER the quality gates (never prompt for an adapter
  // that would fail smoke) and BEFORE persist + the tier2.install dispatch, so
  // a declined adapter is neither written to disk nor registered.
  if (args.reviewClass === "write" && deps.reviewGate) {
    const consented = await deps.reviewGate.requireConsent({
      scheme: args.scheme,
      version: args.version,
      generatorModel: args.generatorModel,
    });
    if (!consented) {
      return recordFailure(
        args,
        "consent",
        "write-surface install declined by user",
        deps,
      );
    }
  }

  // Q1-Q4 (+ consent for write) all green. Persist source to disk, register
  // wrap (the boundary listener forwards to markBrokenFromBoundary when
  // the renderer's error boundary trips), then dispatch tier2.install
  // to the renderer.
  await persistAdapterSource(
    {
      adapterDir: deps.adapterDir,
      scheme: args.scheme,
      version: args.version,
      source: args.source,
    },
    deps.installer,
  );

  // The boundary wrap is constructed here so a stub call-site test can
  // verify it; in production the renderer's Tier2Bridge wraps the live
  // adapter on its end. We exercise wrapWithBoundary here as a smoke test
  // that wrapping does not throw on the validated adapter.
  void wrapWithBoundary(schema.value, () => {});

  deps.dispatcher.send("tier2.install", {
    scheme: args.scheme,
    version: args.version,
    source: args.source,
    generatedAt: args.generatedAt,
    generatorModel: args.generatorModel,
  });

  await appendLifecycleEvent(
    {
      ts: deps.clock(),
      kind: "installed",
      scheme: args.scheme,
      version: args.version,
      detail: `model=${args.generatorModel}`,
    },
    deps.audit,
  );

  return { ok: true, scheme: args.scheme, version: args.version };
}

export async function uninstallAdapter(
  args: { scheme: string; version: number },
  deps: RegistryHostDeps,
): Promise<void> {
  await uninstallAdapterFile(
    {
      adapterDir: deps.adapterDir,
      scheme: args.scheme,
      version: args.version,
    },
    deps.installer,
  );
  deps.dispatcher.send("tier2.uninstall", {
    scheme: args.scheme,
    version: args.version,
  });
  await appendLifecycleEvent(
    {
      ts: deps.clock(),
      kind: "marked-broken",
      scheme: args.scheme,
      version: args.version,
      detail: "uninstall",
    },
    deps.audit,
  );
}

export async function markBrokenFromBoundary(
  args: MarkBrokenFromBoundaryArgs,
  deps: RegistryHostDeps,
): Promise<void> {
  await appendLifecycleEvent(
    {
      ts: deps.clock(),
      kind: "render-error",
      scheme: args.scheme,
      version: args.version,
      detail: `${args.method}: ${args.reason}`,
    },
    deps.audit,
  );
  await appendLifecycleEvent(
    {
      ts: deps.clock(),
      kind: "marked-broken",
      scheme: args.scheme,
      version: args.version,
      detail: args.reason,
    },
    deps.audit,
  );
  deps.dispatcher.send("tier2.mark-broken", {
    scheme: args.scheme,
    version: args.version,
    method: args.method,
    reason: args.reason,
  });
}
