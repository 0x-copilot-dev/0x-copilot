import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Acknowledgment,
  BrandMark,
  FIRST_RUN_SUGGESTIONS,
  FirstRunLocalCard,
  FirstRunSurface,
  OnboardingComposer,
  QWEN3_4B_PRESET,
  TransportProvider,
  createFirstRunLocalModelsPort,
  createModelsPort,
  createProviderKeysPort,
  firstRunAckLines,
  useFirstRunLaunch,
  useFirstRunLocalModel,
  type AcknowledgmentVariant,
  type AssistantComposerPlusMenuSlotArgs,
  type FirstRunAckCtx,
  type FirstRunAckEngine,
  type FirstRunComposerCtx,
  type FirstRunStage,
} from "@0x-copilot/chat-surface";
import { IpcTransport } from "@0x-copilot/chat-transport";

import { DesktopAnchoredPlusMenu } from "./composer/DesktopAnchoredPlusMenu";
import { DesktopComposerFilePicker } from "./composer/DesktopComposerFilePicker";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "./composer/composerPrompts";
import { createDesktopAttachmentAdapter } from "./composer/desktopAttachmentAdapter";
import { modelSelectionForId } from "./composer/desktopModelCatalog";
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  resolveAirdropClaimsCsv,
} from "./onboarding/airdropClaimsFixture";
import { toReadableRunAttachments } from "./onboarding/firstRunAttachments";
import { createFirstRunRunsPort } from "./onboarding/firstRunRunsPort";
import { useOnboardingComposerModels } from "./onboarding/useOnboardingComposerModels";

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

// Substrate-bound singletons — one hidden-input file picker + one single-stage
// attachment adapter for the onboarding composer (mirrors RunComposer's module
// singletons; both are stateless). Kept local so we don't reach into
// RunComposer's module-private instances.
const onboardingFilePicker = new DesktopComposerFilePicker();
const onboardingAttachmentAdapter = createDesktopAttachmentAdapter();

/** P4 fills the real Tools popover + navigations; here they're intentional
 *  no-ops so the first-run composer stays minimal. */
function noop(): void {
  /* intentional no-op — P4 wires connectors/skills navigation */
}

export interface FirstRunSurfaceMountProps {
  /**
   * Namespacing key for the per-install flag / transport (workspaceId). Used to
   * re-key the IpcTransport so a workspace switch rebuilds it.
   */
  readonly workspaceId: string;
  /**
   * Called at the handoff (P3: after the two-step run-create + ~1.5s ack hold)
   * or on skip. The gate persists the first-run flag and swaps to the workspace
   * shell. (Deep-linking the shell straight to the created run is a follow-up —
   * PRD-P3 §4.4's `initialConversationId`/`initialRunId` seam into `RunBinder`.)
   */
  readonly onComplete: () => void;
  /** Tests only — seed the surface stage (forwarded to `FirstRunSurface`). */
  readonly initialStage?: FirstRunStage;
}

/**
 * Desktop binder for the shared `FirstRunSurface` (P1–P3). Builds an
 * IpcTransport from `window.bridge`, derives the reused `ProviderKeysPort` /
 * `ModelsPort` and the P2 `FirstRunLocalModelsPort` from it, and mounts the
 * surface. BYOK save flows through the facade `/v1/settings/provider-keys` via
 * the transport; skip/complete both call the gate's `onComplete` (persist the
 * first-run flag + reveal the shell).
 *
 * P2: `useFirstRunLocalModel` drives the real `/v1/local-models/*` SSE pull; its
 * `localModelPct` feeds the shared model-ready signal (ready at 100), `start` is
 * the `onStartLocalDownload` seam, and `FirstRunLocalCard` fills the gate's
 * `renderLocalCard` slot.
 *
 * P3: the `renderComposer` slot mounts the shared `OnboardingComposer` bound to
 * the desktop composer helpers (model catalog, attachment adapter, file picker,
 * `+` menu); `renderAcknowledgment` mounts the `Acknowledgment`. `useFirstRunLaunch`
 * owns the two-step create (`FirstRunRunsPort`), the "Queued — starts when the
 * model lands" deferral, and the single ~1.5s handoff. Composition (no double
 * handoff): submit → `launch()`; a phase-watching effect flips the surface to
 * the ack once the run is queued/created (NOT on a create error — that stays on
 * the composer with the actionable notice); the hook fires the bound handoff
 * (`ctx.onComplete`) exactly once after the ~1.5s hold.
 */
export function FirstRunSurfaceMount({
  workspaceId,
  onComplete,
  initialStage,
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

  // --- P3 composer wiring ---
  const runs = useMemo(() => createFirstRunRunsPort(transport), [transport]);
  const catalog = useOnboardingComposerModels(transport, {
    localModelPct: local.localModelPct,
    modelName: local.modelName,
  });
  const { models: composerModels, selectedModel, onModelChange } = catalog;

  // Binder-authoritative model-ready + resolved run model (no parent/child lag):
  //   • a local download in flight ⇒ ready only at pct===100 (`localModelPct` is
  //     binder-owned, so the queued→fire flip has no render lag);
  //   • no local download started (`localModelPct === null`) ⇒ a BYOK engine,
  //     ready immediately.
  // This matches the surface's own `modelReady` exactly (a key engine never
  // starts a local pull, so `localModelPct` stays null on that path).
  const modelReady =
    local.localModelPct === null || local.localModelPct === 100;
  const model = useMemo(
    () => modelSelectionForId(composerModels, selectedModel),
    [composerModels, selectedModel],
  );

  // Bound handoff (`ctx.onComplete`) captured from the ack slot so the hook —
  // the single owner of the ~1.5s timer — fires it exactly once. Falls back to
  // the raw `onComplete` if the ack hasn't rendered yet (it always has by the
  // time the timer elapses).
  const ackHandoffRef = useRef<(() => void) | null>(null);
  // The surface's `onSent` (flips to the ack), captured from the composer slot.
  const onSentRef = useRef<(() => void) | null>(null);

  const launch = useFirstRunLaunch({
    runs,
    modelReady,
    model,
    onComplete: () => {
      (ackHandoffRef.current ?? onComplete)();
    },
  });

  // The "right beat" for `onSent`: flip to the acknowledgment once the run is
  // QUEUED (deferred local download) or CREATED (handoff) — never on `starting`
  // (create in flight, stay on the composer) and never on `error` (surface the
  // notice on the composer with the Add-key CTA, per PRD-P3 §4.5).
  const launchPhase = launch.phase;
  useEffect(() => {
    if (launchPhase === "queued" || launchPhase === "handoff") {
      onSentRef.current?.();
    }
  }, [launchPhase]);

  const renderPlusMenu = useCallback(
    ({
      open,
      anchorRef,
      onDismiss,
      children,
    }: AssistantComposerPlusMenuSlotArgs): ReactElement => (
      <DesktopAnchoredPlusMenu
        open={open}
        anchorRef={anchorRef}
        onDismiss={onDismiss}
      >
        {children}
      </DesktopAnchoredPlusMenu>
    ),
    [],
  );

  const resolveAttachment = useCallback(
    (attachmentId: string): Promise<File | null> =>
      attachmentId === AIRDROP_CLAIMS_CSV_ATTACHMENT_ID
        ? resolveAirdropClaimsCsv()
        : Promise.resolve(null),
    [],
  );

  const { launch: startLaunch, reset: resetLaunch } = launch;
  const handleSubmit = useCallback(
    (payload: {
      readonly text: string;
      readonly attachments: ReadonlyArray<unknown>;
    }): void => {
      // Seamless retry: a prior create error parked the hook in `error` (where
      // `launch` is a no-op); reset back to `composing` first (reset updates the
      // phase ref synchronously, so the following launch proceeds).
      if (launchPhase === "error") {
        resetLaunch();
      }
      startLaunch({
        text: payload.text,
        attachments: toReadableRunAttachments(payload.attachments),
      });
    },
    [launchPhase, resetLaunch, startLaunch],
  );

  const renderComposer = useCallback(
    (ctx: FirstRunComposerCtx): ReactNode => {
      onSentRef.current = ctx.onSent;
      return (
        <OnboardingComposer
          connectors={{ servers: [], loading: false }}
          skills={{ skills: [], loading: false }}
          attachmentAdapter={onboardingAttachmentAdapter}
          filePicker={onboardingFilePicker}
          renderPlusMenu={renderPlusMenu}
          skillInstructionPrompt={skillInstructionPrompt}
          mcpServerInstructionPrompt={mcpServerInstructionPrompt}
          onShowConnectors={noop}
          onOpenSkillsSettings={noop}
          onOpenMcpSettings={noop}
          models={composerModels}
          selectedModel={selectedModel}
          onModelChange={onModelChange}
          suggestions={FIRST_RUN_SUGGESTIONS}
          resolveAttachment={resolveAttachment}
          onSubmit={handleSubmit}
          startError={launchPhase === "error" ? launch.error : null}
          onDismissError={resetLaunch}
          disabled={launchPhase === "starting"}
        />
      );
    },
    [
      renderPlusMenu,
      composerModels,
      selectedModel,
      onModelChange,
      resolveAttachment,
      handleSubmit,
      launchPhase,
      launch.error,
      resetLaunch,
    ],
  );

  const selectedModelName = useMemo(() => {
    const picked = composerModels.find((m) => m.id === selectedModel);
    return picked?.name ?? "your model";
  }, [composerModels, selectedModel]);

  const renderAcknowledgment = useCallback(
    (ctx: FirstRunAckCtx): ReactNode => {
      ackHandoffRef.current = ctx.onComplete;
      const isLocal = ctx.engine?.kind === "local";
      const ackEngine: FirstRunAckEngine = isLocal
        ? {
            kind: "local",
            name: QWEN3_4B_PRESET.name,
            pct: local.localModelPct ?? undefined,
          }
        : { kind: "key", name: selectedModelName };
      const lines = firstRunAckLines(ackEngine, {
        webOn: true,
        connectors: [],
      });
      // Variant maps off the launch phase (PRD-P3 §3.5): queued → "queued",
      // starting|handoff → "starting".
      const variant: AcknowledgmentVariant =
        launchPhase === "queued" ? "queued" : "starting";
      return (
        <Acknowledgment
          variant={variant}
          modelLine={lines.modelLine}
          toolsLine={lines.toolsLine}
          privacyLine={lines.privacyLine}
          error={launch.error?.message ?? null}
        />
      );
    },
    [local.localModelPct, selectedModelName, launchPhase, launch.error],
  );

  return (
    <TransportProvider transport={transport}>
      <FirstRunSurface
        providerKeys={providerKeys}
        models={models}
        onSkip={onComplete}
        onComplete={onComplete}
        initialStage={initialStage}
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
        renderComposer={renderComposer}
        renderAcknowledgment={renderAcknowledgment}
      />
    </TransportProvider>
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
