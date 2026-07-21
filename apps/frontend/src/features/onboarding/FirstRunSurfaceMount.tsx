// Web binder for the shared `FirstRunSurface` — the web counterpart of the
// desktop `FirstRunSurfaceMount` (renderer/FirstRunGate.tsx). It derives the
// reused `ProviderKeysPort` / `ModelsPort` and the P2 `FirstRunLocalModelsPort`
// from the app's `WebTransport` (via `getAppTransport()`, exactly as
// `SettingsBinder` builds `createProviderKeysPort(transport)`), drives the
// on-device download hook, and mounts the surface.
//
// The three data ports are the SHARED, substrate-agnostic Transport-backed
// factories from chat-surface — the same ones the desktop binder uses (there
// with an `IpcTransport`, here with the web `WebTransport`). Reusing them keeps
// the SSE-pull bridging + read-merge-write logic in ONE place rather than
// re-wrapping the frontend's `providerKeysApi` / `localModelsApi` clients (which
// are themselves thin `getAppTransport()` wrappers). BYOK save flows through the
// facade `/v1/settings/provider-keys`; skip/complete both call the gate's
// `onComplete` (persist the first-run flag + reveal the shell).
//
// P2: `useFirstRunLocalModel` drives the real `/v1/local-models/*` SSE pull; its
// `localModelPct` feeds the shared model-ready signal (ready at 100), `start` is
// the `onStartLocalDownload` seam, and `FirstRunLocalCard` fills the gate's
// `renderLocalCard` slot. On the browser (Ollama not running / feature gated
// off) the hook reports `disabled` and the card shows honest setup steps — the
// user picks a BYOK key instead.
//
// The onboarding composer + acknowledgment (State B/C) are a follow-up: this
// binder mounts the model-choice gate; the surface renders its own P1 composer/
// ack placeholders after an engine is chosen (see `OnboardingComposerMount`).

import { useMemo, type ReactElement } from "react";

import {
  FirstRunLocalCard,
  FirstRunSurface,
  QWEN3_4B_PRESET,
  TransportProvider,
  createFirstRunLocalModelsPort,
  createModelsPort,
  createProviderKeysPort,
  useFirstRunLocalModel,
  type FirstRunStage,
} from "@0x-copilot/chat-surface";

import { getAppTransport } from "../../api/transport";

// The shared FTUE styles (top bar + gate cards + composer + ack). The desktop
// imports these once at bootstrap; the web app imports them here so they load
// only with the onboarding chunk.
import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";

export interface FirstRunSurfaceMountProps {
  /**
   * Called on skip or when onboarding completes. The gate persists the
   * first-run flag and swaps to the workspace shell.
   */
  readonly onComplete: () => void;
  /** Tests only — seed the surface stage (forwarded to `FirstRunSurface`). */
  readonly initialStage?: FirstRunStage;
}

export function FirstRunSurfaceMount({
  onComplete,
  initialStage,
}: FirstRunSurfaceMountProps): ReactElement {
  // The app's single `WebTransport` (bearer + 401-handling configured by
  // AuthContext). A stable module singleton, so the ports below are stable too.
  const transport = getAppTransport();
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
      />
    </TransportProvider>
  );
}
