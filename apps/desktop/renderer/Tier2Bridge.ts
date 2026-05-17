import { createElement, type ReactElement } from "react";

import {
  markBroken,
  registerAdapter,
  Tier2Loader,
  unregisterAdapter,
  type SaaSRendererAdapter,
  type Tier2LoaderProps,
  type Tier2WorkerLike,
} from "@enterprise-search/chat-surface";
import {
  CHANNELS,
  Tier2InstallPayloadSchema,
  Tier2MarkBrokenPayloadSchema,
  Tier2UninstallPayloadSchema,
  type Tier2InstallPayload,
  type Tier2MarkBrokenPayload,
  type Tier2UninstallPayload,
  type WindowBridge,
} from "@enterprise-search/chat-transport";

// Phase 6C renderer-side bridge. Listens for tier2.install / tier2.uninstall
// / tier2.mark-broken pushes from main and calls into chat-surface's
// SurfaceRegistry. On adapter failure (Tier2Loader.onFailure trips), sends a
// tier2.boundary-error back to main so the lifecycle (Q6) can demote the
// version and record the audit trail.

type RawHandler = (raw: unknown) => void;

export interface Tier2BridgeOptions {
  readonly bridge: WindowBridge;
  readonly workerFactory?: () => Tier2WorkerLike;
}

function adapterMatches(scheme: string, uri: string): boolean {
  if (typeof uri !== "string" || uri.length === 0) return false;
  const idx = uri.indexOf("://");
  if (idx <= 0) return false;
  return uri.slice(0, idx) === scheme;
}

function buildTier2Adapter(
  payload: Tier2InstallPayload,
  bridge: WindowBridge,
  workerFactory?: () => Tier2WorkerLike,
): SaaSRendererAdapter {
  const { scheme, version, source } = payload;

  const reportBoundary = (
    method: "renderCurrent" | "renderDiff",
    message: string,
  ): void => {
    // The IPC invocation is fire-and-forget — boundary errors do not block
    // the host's tier-3 fallback. The lifecycle on the main side will
    // call markBroken via the IPC dispatcher.
    void bridge.ipc.invoke(CHANNELS.tier2BoundaryError, {
      scheme,
      version,
      method,
      message,
    });
  };

  const renderViaLoader = (
    mode: "current" | "diff",
    payloadInput: unknown,
  ): ReactElement => {
    const props: Tier2LoaderProps = {
      adapterSource: source,
      scheme,
      version,
      state: mode === "current" ? payloadInput : undefined,
      pendingDiff: mode === "diff" ? { diff: payloadInput } : null,
      workerFactory,
      onFailure: (reason, detail) => {
        const method = mode === "current" ? "renderCurrent" : "renderDiff";
        reportBoundary(method, `${reason}: ${detail ?? ""}`);
      },
    };
    return createElement(Tier2Loader, props);
  };

  return {
    scheme,
    matches: (uri: string) => adapterMatches(scheme, uri),
    renderCurrent: (state: unknown) => renderViaLoader("current", state),
    renderDiff: (diff: unknown) => renderViaLoader("diff", diff),
    metadata: {
      origin: "agent-generated",
      schemaVersion: version,
      generatedAt: payload.generatedAt,
      generatorModel: payload.generatorModel,
    },
  };
}

export class Tier2Bridge {
  readonly #bridge: WindowBridge;
  readonly #workerFactory?: () => Tier2WorkerLike;
  #disposers: Array<() => void> = [];

  constructor(opts: Tier2BridgeOptions) {
    this.#bridge = opts.bridge;
    this.#workerFactory = opts.workerFactory;
  }

  attach(): () => void {
    const install: RawHandler = (raw) => {
      const parsed = Tier2InstallPayloadSchema.safeParse(raw);
      if (!parsed.success) return;
      const adapter = buildTier2Adapter(
        parsed.data,
        this.#bridge,
        this.#workerFactory,
      );
      registerAdapter(adapter);
    };

    const uninstall: RawHandler = (raw) => {
      const parsed = Tier2UninstallPayloadSchema.safeParse(raw);
      if (!parsed.success) return;
      const p: Tier2UninstallPayload = parsed.data;
      unregisterAdapter(p.scheme, p.version);
    };

    const markBrokenHandler: RawHandler = (raw) => {
      const parsed = Tier2MarkBrokenPayloadSchema.safeParse(raw);
      if (!parsed.success) return;
      const p: Tier2MarkBrokenPayload = parsed.data;
      markBroken(p.scheme, p.version, p.reason);
    };

    this.#disposers.push(
      this.#bridge.ipc.on(CHANNELS.tier2Install, install),
      this.#bridge.ipc.on(CHANNELS.tier2Uninstall, uninstall),
      this.#bridge.ipc.on(CHANNELS.tier2MarkBroken, markBrokenHandler),
    );

    return () => {
      this.detach();
    };
  }

  detach(): void {
    for (const dispose of this.#disposers) {
      try {
        dispose();
      } catch {
        // disposers must not throw; swallow to keep idempotent teardown.
      }
    }
    this.#disposers = [];
  }
}
