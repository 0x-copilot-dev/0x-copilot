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
  firstRunAckAction,
  firstRunAckLines,
  firstRunAckNote,
  firstRunAckStateForPhase,
  useFirstRunLaunch,
  useFirstRunLocalModel,
  type AcknowledgmentVariant,
  type AssistantComposerPlusMenuSlotArgs,
  type FirstRunAckCtx,
  type FirstRunAckEngine,
  type FirstRunComposerCtx,
  type FirstRunInstallableConnector,
  type FirstRunStage,
} from "@0x-copilot/chat-surface";
import { IpcTransport } from "@0x-copilot/chat-transport";
import type { ConversationConnectorScopes } from "@0x-copilot/api-types";

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
import { createFirstRunConnectorsPort } from "./onboarding/firstRunConnectorsPort";
import { createFirstRunProfilePort } from "./onboarding/firstRunProfilePort";
import { createFirstRunRunsPort } from "./onboarding/firstRunRunsPort";
import { useOnboardingComposerModels } from "./onboarding/useOnboardingComposerModels";

import { CONNECTOR_CHANNELS } from "../main/connectors/channels";
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

/** The first-run composer's `+`-menu / skills-settings navigations are
 *  intentional no-ops — the FTUE's connector affordance is the P4 Tools popover
 *  (wired via `connectorsPort` below), not these deep-links into Settings. */
function noop(): void {
  /* intentional no-op — FTUE has no in-composer Settings navigation */
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
  // P4 — wallet-chip identity + the Tools popover's MCP connector surface.
  const profilePort = useMemo(
    () => createFirstRunProfilePort(transport),
    [transport],
  );
  const connectorsPort = useMemo(
    () => createFirstRunConnectorsPort(transport),
    [transport],
  );
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

  // PRD-P8 §7 — the awaited local model demonstrably is NOT coming: a terminal
  // pull failure (`blocked`), or a daemon that stopped answering
  // (`runtime === "stopped"`). `modelReady` alone cannot express this — a dead
  // download simply stops moving, and a frozen pct is indistinguishable from a
  // slow one. Threading this is what lets the queued launch phase EXIT; without
  // it a runtime that dies after the user sent their first prompt parks them on
  // "Queued — starts when the model lands" permanently. Both hosts derive it
  // identically (web `FirstRunSurfaceMount`).
  const modelBlocked = local.blocked !== null || local.runtime === "stopped";

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
  // P4 — the surface owns `webOn` + active-connector state; the composer ctx
  // carries the latest values each render. We mirror them into refs (like
  // `onSentRef`) so `handleSubmit` reads the value live at send time and the
  // acknowledgment's tools line reflects the real toggle.
  const webSearchRef = useRef(true);
  const connectorScopesRef = useRef<ConversationConnectorScopes | undefined>(
    undefined,
  );

  const launch = useFirstRunLaunch({
    runs,
    modelReady,
    modelBlocked,
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
        webSearchEnabled: webSearchRef.current,
        connectorScopes: connectorScopesRef.current,
      });
    },
    [launchPhase, resetLaunch, startLaunch],
  );

  // P4 — featured 1-click connect. The renderer cannot open an external URL on
  // desktop (main denies `window.open`); the connect flow is owned by MAIN,
  // which binds a loopback + opens the system browser for the catalog slug
  // (mirrors `ConnectorsBinder.connect`). No token crosses the bridge.
  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      void window.bridge.ipc
        .invoke(CONNECTOR_CHANNELS.connect, { slug: entry.slug })
        .catch(() => {
          // Workspace-authorize is best-effort here; first-use tool consent
          // still lands as the run-time `mcp_auth_required` HITL card.
        });
    },
    [],
  );

  // P8 §8 — state ①'s "Get Ollama ↗". Same shape as the connector open above:
  // the renderer cannot open an external URL (main denies `window.open`), so it
  // asks MAIN for the INTENT and main owns the destination. The channel takes
  // no argument on purpose — nothing here can name a URL, so nothing that
  // reaches this renderer can turn the FTUE into an arbitrary-origin opener.
  const handleGetOllama = useCallback((): void => {
    void window.bridge.ipc
      .invoke(FIRST_RUN_CHANNELS.openOllamaDownload, {})
      .catch(() => {
        // Best-effort: the card's watch line already tells the user what to do
        // and the hook keeps polling, so a failed open must not raise in the
        // FTUE. (Main resolves `{ ok:false }` rather than rejecting anyway.)
      });
  }, []);

  const renderComposer = useCallback(
    (ctx: FirstRunComposerCtx): ReactNode => {
      onSentRef.current = ctx.onSent;
      // Capture the surface-owned Tools state for `handleSubmit` (live at send)
      // and the acknowledgment's tools line.
      webSearchRef.current = ctx.webSearchEnabled;
      connectorScopesRef.current = ctx.connectorScopes;
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
          toolsTrigger={ctx.toolsTrigger}
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
            // FLOORED, matching web exactly: `firstRunAckLines` interpolates
            // this number verbatim, and a real byte-progress pct is fractional
            // (`pullPercent` = got/total*100), so the raw value prints
            // "· downloading 46.72897196261682%". Floor rather than round
            // because `modelSuffix` reads `pct >= 100` as "· on-device" while
            // `modelReady` flips only at an EXACT 100 — rounding 99.6 up would
            // announce a model as on-device while the launch is still queued
            // waiting for it.
            pct:
              local.localModelPct === null
                ? undefined
                : Math.floor(local.localModelPct),
            // P8 §7 — a stalled pull must not keep echoing "· downloading 40%".
            // `firstRunAckLines` swaps it for "· download paused at 40%".
            blocked: ctx.modelBlocked,
          }
        : { kind: "key", name: selectedModelName };
      const lines = firstRunAckLines(ackEngine, {
        webOn: webSearchRef.current,
        connectors: [],
      });
      // Variant maps off the launch phase (PRD-P3 §3.5) through the package's
      // single derivation: queued → "queued", starting|handoff → "starting",
      // P8 §7's `blocked` → "stalled". The stalled title is the point —
      // "Queued — starts when the model lands" is a promise the model line
      // directly contradicts once it reads "· download paused at 40%", and an
      // ack with a lie and no control is where the FTUE used to end. `note`
      // says what to do; `onBack` (ctx) un-sends the surface so the composer
      // returns and `launch()` accepts the re-submit.
      const variant: AcknowledgmentVariant =
        firstRunAckStateForPhase(launchPhase);
      return (
        <Acknowledgment
          variant={variant}
          modelLine={lines.modelLine}
          toolsLine={lines.toolsLine}
          privacyLine={lines.privacyLine}
          error={launch.error?.message ?? null}
          note={firstRunAckNote(variant)}
          actionLabel={firstRunAckAction(variant)}
          onAction={ctx.onBack}
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
        profilePort={profilePort}
        connectorsPort={connectorsPort}
        onConnectCatalog={handleConnectCatalog}
        onSkip={onComplete}
        onComplete={onComplete}
        initialStage={initialStage}
        onStartLocalDownload={local.start}
        localModelPct={local.localModelPct}
        // P8 §6 — the preset was already on disk, so no pull ever runs and
        // `localModelPct` stays null. Without this the surface would call a
        // local engine "not ready" forever and queue the send behind a download
        // that is never going to happen.
        localModelInstalled={local.modelInstalled}
        // P8 §7 — same signal the launch hook gets, so the composer/ack ctx
        // stops claiming a download is in flight.
        localModelBlocked={modelBlocked}
        localDownloadDisabled={local.disabled}
        // P8: all three card seams are wired here. The card renders a control
        // ONLY when its callback is supplied (omitted ⇒ no button, by design),
        // so an unwired seam is a dead end, not a degraded one:
        //   • onContinue  — D4a-1 "Continue →": state ③'s only way forward now
        //     that an auto-started pull deliberately keeps the gate mounted.
        //   • onGetOllama — state ①'s "Get Ollama ↗" (main-brokered open).
        // `restartRuntime` (state ④) needs no host seam: the card calls it on
        // `state` and it goes through the facade
        // (`POST /v1/local-models/runtime/start`) over the same transport. The
        // supervisor sets RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME=true
        // (`main/services/service-env.ts`), so on a supervised desktop boot the
        // status carries `runtime_managed: true` and the button renders.
        renderLocalCard={(ctx) => (
          <FirstRunLocalCard
            state={local}
            preset={QWEN3_4B_PRESET}
            onStartDownload={ctx.onStartDownload}
            onContinue={ctx.onContinue}
            onGetOllama={handleGetOllama}
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
