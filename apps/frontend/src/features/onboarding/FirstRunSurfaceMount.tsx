// Web binder for the shared `FirstRunSurface` — the web counterpart of the
// desktop `FirstRunSurfaceMount` (renderer/FirstRunGate.tsx). It builds the
// FTUE data ports from the frontend's typed `api/*` modules (the sanctioned
// substrate seam — features never touch the Transport singleton; see
// apps/frontend/eslint.config.js) and mounts the 3-state gate → composer → ack
// surface. The `TransportProvider` the deep composer subtree needs
// (ToolPicker / MentionPopover call `useTransport`) is injected by the app
// root in `App.tsx`, so this binder stays substrate-clean.
//
// Ports (mirrors the desktop binder's port set, bound to the web api layer):
//   • providerKeys — `api/providerKeysApi` (BYOK save flows through the facade
//     `/v1/settings/provider-keys`).
//   • local model — `api/localModelsApi` SSE (`useFirstRunLocalModel` drives the
//     real `/v1/local-models/*` pull; `localModelPct` feeds the model-ready
//     signal, ready at 100; `FirstRunLocalCard` fills the gate's local slot).
//   • runs — `api/agentApi` two-step create (`FirstRunRunsPort`).
//   • composer models — the live `/v1/agent/models` catalog (NEVER a hardcoded
//     list), with the on-device honesty entry injected during a local pull.
//
// Composition (mirrors the desktop, no double handoff): submit → `launch()`; a
// phase-watching effect flips the surface to the ack once the run is
// queued/created (NOT on a create error — that stays on the composer with the
// actionable notice); `useFirstRunLaunch` fires the bound handoff
// (`ctx.onComplete`) exactly once after the ~1.5s hold.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Acknowledgment,
  FIRST_RUN_SUGGESTIONS,
  FirstRunLocalCard,
  FirstRunSurface,
  OnboardingComposer,
  QWEN3_4B_PRESET,
  firstRunAckLines,
  useFirstRunLaunch,
  useFirstRunLocalModel,
  type AcknowledgmentVariant,
  type FirstRunAckCtx,
  type FirstRunAckEngine,
  type FirstRunComposerCtx,
  type FirstRunStage,
} from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  resolveAirdropClaimsCsv,
} from "./airdropClaimsAttachment";
import { toReadableRunAttachments } from "./firstRunAttachments";
import { createFirstRunLocalModelsPort } from "./firstRunLocalModelsPort";
import { createFirstRunProviderKeysPort } from "./firstRunProviderKeysPort";
import { createFirstRunRunsPort } from "./firstRunRunsPort";
import {
  createOnboardingChatSurfaceAttachmentAdapter,
  mcpServerInstructionPrompt,
  onboardingFilePicker,
  renderOnboardingPlusMenu,
  skillInstructionPrompt,
} from "./onboardingComposerAdapter";
import {
  modelSelectionForId,
  useOnboardingComposerModels,
} from "./useOnboardingComposerModels";

// The shared FTUE styles (top bar + gate cards + composer + ack). The desktop
// imports these once at bootstrap; the web app imports them here so they load
// only with the onboarding chunk.
import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";

// Substrate-bound singleton — one single-stage-bridged attachment adapter for
// the onboarding composer (mirrors the desktop binder's module singleton).
const onboardingAttachmentAdapter =
  createOnboardingChatSurfaceAttachmentAdapter();

/** P4 fills the real Tools popover + navigations; here they're intentional
 *  no-ops so the first-run composer stays minimal. */
function noop(): void {
  /* intentional no-op — P4 wires connectors/skills navigation */
}

export interface FirstRunSurfaceMountProps {
  /**
   * Called at the handoff (after the two-step run-create + ~1.5s ack hold) or
   * on skip. The gate persists the first-run flag and swaps to the workspace
   * shell.
   */
  readonly onComplete: () => void;
  /** Signed-in identity — threaded to the api-backed run + models ports. */
  readonly identity: RequestIdentity;
  /** Tests only — seed the surface stage (forwarded to `FirstRunSurface`). */
  readonly initialStage?: FirstRunStage;
}

export function FirstRunSurfaceMount({
  onComplete,
  identity,
  initialStage,
}: FirstRunSurfaceMountProps): ReactElement {
  const providerKeys = useMemo(() => createFirstRunProviderKeysPort(), []);
  const localModelsPort = useMemo(() => createFirstRunLocalModelsPort(), []);
  const local = useFirstRunLocalModel({
    port: localModelsPort,
    preset: QWEN3_4B_PRESET,
  });

  // --- P3 composer wiring ---
  const runs = useMemo(() => createFirstRunRunsPort(identity), [identity]);
  const catalog = useOnboardingComposerModels({
    identity,
    localModelPct: local.localModelPct,
    modelName: local.modelName,
  });
  const { models: composerModels, selectedModel, onModelChange } = catalog;

  // Binder-authoritative model-ready + resolved run model (no parent/child lag):
  //   • a local download in flight ⇒ ready only at pct===100 (`localModelPct` is
  //     binder-owned, so the queued→fire flip has no render lag);
  //   • no local download started (`localModelPct === null`) ⇒ a BYOK engine,
  //     ready immediately.
  const modelReady =
    local.localModelPct === null || local.localModelPct === 100;
  const model = useMemo(
    () => modelSelectionForId(composerModels, selectedModel),
    [composerModels, selectedModel],
  );

  // Bound handoff (`ctx.onComplete`) captured from the ack slot so the hook —
  // the single owner of the ~1.5s timer — fires it exactly once. Falls back to
  // the raw `onComplete` if the ack hasn't rendered yet.
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
  // notice on the composer with the Add-key CTA).
  const launchPhase = launch.phase;
  useEffect(() => {
    if (launchPhase === "queued" || launchPhase === "handoff") {
      onSentRef.current?.();
    }
  }, [launchPhase]);

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
        // Web-search default-on. The FTUE surface owns the Tools-popover `webOn`
        // toggle; threading it into this web launch is a follow-up (desktop
        // parity), so send the always-on default here.
        webSearchEnabled: true,
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
          renderPlusMenu={renderOnboardingPlusMenu}
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
      // Variant maps off the launch phase: queued → "queued",
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
    <FirstRunSurface
      providerKeys={providerKeys}
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
  );
}
