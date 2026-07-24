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
//
// PRD-P8 §8 (web host) wires the two card seams the package deliberately leaves
// optional — omitted means NO button, because the card never renders a control
// that cannot work:
//   • `onGetOllama` — state ① `Get Ollama ↗`. Web has no main process to broker
//     through, so it is an ordinary external open of a MODULE CONSTANT url.
//   • `onContinue` — D4a's "Continue →" on state ③. Advances to the composer
//     WITHOUT restarting the pull the hook auto-started on runtime detection.
// It also threads §7's `modelBlocked` (the hook's `blocked` / stopped runtime)
// into `useFirstRunLaunch` + the surface, which is what lets the queued launch
// phase exit instead of hanging on "Queued — starts when the model lands".
//
// What web deliberately does NOT get: a Restart-Ollama path. `runtime_managed`
// is false on every web deployment (`RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME`
// defaults false and both web compose files pin it "false"; only
// `tools/desktop-runtime` + `apps/desktop` set it true), so the card degrades ④
// to its instructional foot and `POST /v1/local-models/runtime/start` stays a
// 404 no host should be calling. PRD-P8 D2 keeps process control off web.

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
  firstRunAckAction,
  firstRunAckLines,
  firstRunAckNote,
  firstRunAckStateForPhase,
  useFirstRunLaunch,
  useFirstRunLocalModel,
  type AcknowledgmentVariant,
  type FirstRunAckCtx,
  type FirstRunAckEngine,
  type FirstRunComposerCtx,
  type FirstRunLaunchResult,
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

/**
 * PRD-P8 §8 — the `Get Ollama ↗` destination. A module CONSTANT, never a value
 * that arrives from the card, the server, or a prop: the card hands the host a
 * bare `() => void` precisely so no layer below can choose the origin (the
 * desktop expresses the same invariant as an IPC channel that takes no URL).
 */
export const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";

/**
 * Web's external open. `noopener,noreferrer` so the new tab gets no
 * `window.opener` handle back into the signed-in app and sends no referrer.
 * Module-scoped (not a `useCallback`) — it closes over nothing, so it is
 * referentially stable for free.
 */
function openOllamaDownload(): void {
  window.open(OLLAMA_DOWNLOAD_URL, "_blank", "noopener,noreferrer");
}

export interface FirstRunSurfaceMountProps {
  /**
   * Called at the handoff (after the two-step run-create + ~1.5s ack hold) with
   * the created `FirstRunLaunchResult` (conversation + run id), or on skip with
   * nothing. The host (App.tsx) navigates the router to the created conversation
   * so the shell binds the first run rather than an empty standby, then persists
   * the first-run flag and swaps to the workspace shell.
   */
  readonly onComplete: (result?: FirstRunLaunchResult) => void;
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
  //   • no local download started (`localModelPct === null`) ⇒ a BYOK engine, or
  //     P8 §6's already-installed short-circuit (which issues no pull, so the
  //     pct legitimately stays null) — ready immediately either way.
  const modelReady =
    local.localModelPct === null || local.localModelPct === 100;

  // PRD-P8 §7 — the awaited local model demonstrably is NOT coming: a terminal
  // pull failure (`blocked`), or a daemon that stopped answering
  // (`runtime === "stopped"`). `modelReady` alone cannot express this — a dead
  // download simply stops moving, and a frozen pct is indistinguishable from a
  // slow one. Threading this is what lets the queued launch phase EXIT; without
  // it a runtime that dies after the user sent their first prompt parks them on
  // "Queued — starts when the model lands" permanently. Both hosts derive it
  // identically (desktop `FirstRunGate`).
  const modelBlocked = local.blocked !== null || local.runtime === "stopped";

  const model = useMemo(
    () => modelSelectionForId(composerModels, selectedModel),
    [composerModels, selectedModel],
  );

  // The surface's `onSent` (flips to the ack), captured from the composer slot.
  const onSentRef = useRef<(() => void) | null>(null);

  const launch = useFirstRunLaunch({
    runs,
    modelReady,
    modelBlocked,
    model,
    // The hook is the single owner of the ~1.5s handoff timer and hands us the
    // created identity; forward it verbatim so the host can bind the shell to
    // the first run's conversation (the surface's own `onComplete(engine)` slot
    // carries the engine, not this run-create result, so we thread it here).
    onComplete: (result) => {
      onComplete(result);
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
      const isLocal = ctx.engine?.kind === "local";
      const ackEngine: FirstRunAckEngine = isLocal
        ? {
            kind: "local",
            name: QWEN3_4B_PRESET.name,
            // Rounded HERE because `firstRunAckLines` interpolates the raw
            // number (unlike `firstRunModelPillLabel`, which rounds): a real
            // byte-progress pct is fractional, so passing it straight through
            // prints "· downloading 46.72897196261682%".
            //
            // FLOOR, not round, and that is load-bearing: `modelSuffix` treats
            // `pct >= 100` as "· on-device", while `modelReady` above only
            // flips at an EXACT 100. `Math.round(99.6)` would make the ack
            // announce the model as on-device while the launch is still queued
            // waiting for it — and Ollama holds the last byte-carrying pct
            // across its "verifying sha256" / "writing manifest" frames, so
            // that window is seconds long on a 4.3 GB pull, not one frame.
            // Flooring can only ever under-claim, which is the safe direction.
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
        webOn: true,
        connectors: [],
      });
      // Variant maps off the launch phase through the package's single
      // derivation (`firstRunAckStateForPhase`): queued → "queued",
      // starting|handoff → "starting", P8's `blocked` → "stalled". The stalled
      // title is the point — "Queued — starts when the model lands" is a
      // promise the model line directly contradicts once it reads "· download
      // paused at 40%", and an ack with a lie and no control is where the FTUE
      // used to end. `note` says what to do; `onBack` (ctx) un-sends the
      // surface so the composer returns and `launch()` accepts the re-submit.
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
    <FirstRunSurface
      providerKeys={providerKeys}
      // Skip / the surface's own engine-carrying `onComplete` slot both mean
      // "reveal the shell with NO created run"; the real first-run handoff
      // (carrying the conversation id) is fired by `useFirstRunLaunch` above.
      onSkip={() => onComplete()}
      onComplete={() => onComplete()}
      initialStage={initialStage}
      onStartLocalDownload={local.start}
      localModelPct={local.localModelPct}
      // P8 §6 — the preset was already on disk, so no pull ever runs and
      // `localModelPct` stays null. Without this the surface would call a local
      // engine "not ready" forever and queue the send behind a download that is
      // never going to happen.
      localModelInstalled={local.modelInstalled}
      // P8 §7 — same signal the launch hook gets, so the composer/ack ctx stops
      // claiming a download is in flight.
      localModelBlocked={modelBlocked}
      localDownloadDisabled={local.disabled}
      renderLocalCard={(ctx) => (
        <FirstRunLocalCard
          state={local}
          preset={QWEN3_4B_PRESET}
          onStartDownload={ctx.onStartDownload}
          // D4a-1 — advance WITHOUT restarting the auto-started pull. Distinct
          // from `onStartDownload`, which also fires `local.start`.
          onContinue={ctx.onContinue}
          // §8 web: no broker, so an ordinary external open of the constant.
          onGetOllama={openOllamaDownload}
        />
      )}
      renderComposer={renderComposer}
      renderAcknowledgment={renderAcknowledgment}
    />
  );
}
