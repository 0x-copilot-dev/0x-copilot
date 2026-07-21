import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  BrandMark,
  FirstRunLocalCard,
  FirstRunSurface,
  QWEN3_4B_PRESET,
  createFirstRunLocalModelsPort,
  createModelsPort,
  createProviderKeysPort,
  useFirstRunLocalModel,
} from "@0x-copilot/chat-surface";
import { IpcTransport } from "@0x-copilot/chat-transport";

import { FIRST_RUN_CHANNELS } from "../main/services/first-run-channels";
// The preload bridge type exposes `invoke(channel: string, …)`, so it can reach
// the app-local `first-run.*` channels (the chat-transport WindowBridge narrows
// `invoke` to the shared ChannelName union). This mirrors how SettingsMount
// reaches the app-local secure-storage channels via `window.bridge`.
import type { WindowBridge } from "../preload/window-bridge-types";

import "./firstrun.css";

interface FirstRunGetResult {
  readonly completed: boolean;
}

type Phase = { kind: "loading" } | { kind: "first-run" } | { kind: "complete" };

export interface FirstRunGateProps {
  readonly bridge: WindowBridge;
  /** Namespacing key for the per-install flag (RendererSession.workspaceId). */
  readonly workspaceId: string;
  /**
   * The onboarding surface. Receives `onComplete` — call it when the user
   * finishes setup, sends their first run, or skips. The gate persists the
   * per-workspace flag and swaps to `children` (the workspace shell). P0 passes
   * a minimal placeholder here; P1 passes the full 3-state FirstRunSurface.
   */
  readonly renderFirstRun: (onComplete: () => void) => ReactNode;
  /** The signed-in workspace shell, mounted once onboarding is complete. */
  readonly children: ReactNode;
}

/**
 * Gates the workspace shell behind first-run onboarding, mirroring the
 * BootGate / SignInGate pattern. Sits between SignInGate's signed-in render and
 * the shell: a returning user (flag set) drops straight through to `children`;
 * a first-time user sees the onboarding surface until they finish or skip.
 *
 * The gate is host-owned (like SignInGate) — only the onboarding *surface*
 * (passed via `renderFirstRun`) is the shared chat-surface component.
 */
export function FirstRunGate(props: FirstRunGateProps): ReactElement {
  const { bridge, workspaceId, renderFirstRun, children } = props;
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    bridge.ipc
      .invoke<FirstRunGetResult>(FIRST_RUN_CHANNELS.get, { workspaceId })
      .then((res) => {
        if (cancelled) return;
        setPhase(res.completed ? { kind: "complete" } : { kind: "first-run" });
      })
      .catch(() => {
        // A failed read must not trap the user on a blank gate — fail OPEN to
        // onboarding (never skip it on a bad read; the flag persists on exit).
        if (!cancelled) setPhase({ kind: "first-run" });
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, workspaceId]);

  const complete = useCallback(() => {
    // Advance the UI immediately; persist is fire-and-forget — a write failure
    // only means onboarding may show once more next launch (non-fatal).
    setPhase({ kind: "complete" });
    void bridge.ipc
      .invoke(FIRST_RUN_CHANNELS.set, { workspaceId, completed: true })
      .catch(() => undefined);
  }, [bridge, workspaceId]);

  switch (phase.kind) {
    case "loading":
      return <FirstRunLoading />;
    case "first-run":
      return <>{renderFirstRun(complete)}</>;
    case "complete":
      return <>{children}</>;
  }
}

function FirstRunLoading(): ReactElement {
  return (
    <div className="fr-boot" data-testid="first-run-loading">
      <span className="fr-boot__spin" aria-hidden="true" />
    </div>
  );
}

// Desktop transport capabilities (mirrors bootstrap.tsx's DESKTOP_CAPABILITIES;
// kept local so this binder builds its own transport without a bootstrap import
// cycle). The bearer is attached in main on every outbound request, so the
// renderer holds an opaque "session for workspace X" handle only.
const FIRST_RUN_CAPABILITIES = {
  substrate: "desktop-webview" as const,
  nativeSecretStorage: true,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

export interface FirstRunSurfaceMountProps {
  /**
   * Namespacing key for the per-install flag / transport (workspaceId). Used to
   * re-key the IpcTransport so a workspace switch rebuilds it.
   */
  readonly workspaceId: string;
  /**
   * Called when the user finishes setup (BYOK connect handoff) or skips. The
   * gate persists the flag and swaps to the workspace shell. Both skip and
   * complete resolve to the same host action in P1 (flag set + reveal), matching
   * journeys J2/J4; P3 will do run-create before completing.
   */
  readonly onComplete: () => void;
}

/**
 * Desktop binder for the shared `FirstRunSurface` (P1 + P2). Builds an
 * IpcTransport from `window.bridge`, derives the reused `ProviderKeysPort` /
 * `ModelsPort` and the P2 `FirstRunLocalModelsPort` from it, and mounts the
 * surface. BYOK save flows through the facade `/v1/settings/provider-keys` via
 * the transport; skip/complete both call the gate's `onComplete` (persist the
 * first-run flag + reveal the shell).
 *
 * P2 wiring: `useFirstRunLocalModel` drives the real `/v1/local-models/*` SSE
 * pull; its `localModelPct` feeds P1's shared model-ready signal (ready at
 * 100), `start` is the `onStartLocalDownload` seam, and `FirstRunLocalCard`
 * fills the gate's `renderLocalCard` slot. The real composer/acknowledgment
 * (P3) remain the surface's internal placeholders here.
 */
export function FirstRunSurfaceMount({
  workspaceId,
  onComplete,
}: FirstRunSurfaceMountProps): ReactElement {
  const transport = useMemo(
    () =>
      new IpcTransport({
        bridge: window.bridge,
        bootstrapSession: { bearer: null },
        bootstrapCapabilities: FIRST_RUN_CAPABILITIES,
      }),
    // Re-key on workspace change (see ChatShellForSession); the bearer is
    // attached in main, so the handle is otherwise stable.
    [workspaceId],
  );
  const providerKeys = useMemo(
    () => createProviderKeysPort(transport),
    [transport],
  );
  const models = useMemo(() => createModelsPort(transport), [transport]);
  const localModelsPort = useMemo(
    () => createFirstRunLocalModelsPort(transport),
    [transport],
  );
  const local = useFirstRunLocalModel({
    port: localModelsPort,
    preset: QWEN3_4B_PRESET,
  });

  return (
    <FirstRunSurface
      providerKeys={providerKeys}
      models={models}
      onSkip={onComplete}
      onComplete={onComplete}
      onStartLocalDownload={local.start}
      localModelPct={local.localModelPct}
      localDownloadDisabled={local.disabled}
      renderLocalCard={(ctx) => (
        <FirstRunLocalCard
          state={local}
          preset={QWEN3_4B_PRESET}
          onStartDownload={ctx.onStartDownload}
        />
      )}
    />
  );
}

/**
 * P0 interim onboarding body — a minimal branded welcome with the two exits
 * (Get started / skip), both of which complete the gate. P1 replaces this with
 * the full 3-state FirstRunSurface (gate → composer → ack) rendered from the
 * shared chat-surface package via `renderFirstRun`.
 */
export function FirstRunPlaceholder({
  onComplete,
}: {
  readonly onComplete: () => void;
}): ReactElement {
  return (
    <div className="fr" data-testid="first-run-surface">
      <div className="fr-top">
        <span className="fr-brand">
          <BrandMark size={18} />
          <span className="fr-brand__name">
            <span className="fr-zx">0x</span>Copilot
          </span>
        </span>
        <span className="fr-top__sp" />
        <button
          type="button"
          className="fr-skip"
          onClick={onComplete}
          data-testid="first-run-skip"
        >
          skip — open the workspace →
        </button>
      </div>

      <div className="fr-main">
        <h1 className="fr-h1">Welcome to 0xCopilot</h1>
        <p className="fr-sub">
          Let&rsquo;s get you set up — pick a model and run your first task. The
          full onboarding steps land next.
        </p>
        <button
          type="button"
          className="fr-cta"
          onClick={onComplete}
          data-testid="first-run-get-started"
        >
          Get started
        </button>
      </div>

      <div className="fr-foot">
        <span>v0.1.0 · local build</span>
        <span>nothing leaves this machine</span>
      </div>
    </div>
  );
}
