// Model catalog for the FTUE onboarding composer's model pill (web).
//
// The FTUE composer reads the LIVE catalog — `GET /v1/agent/models` via
// `api/agentApi.listModels` — never a hardcoded list (SPEC / FirstRunSurface's
// `models` contract). Each catalog row carries a server-computed `configured`
// flag (true only when the user has that provider's BYOK key), so unusable
// models are shown disabled rather than hidden. This intentionally differs
// from the Run cockpit's composer, which still uses a static curated list.
//
// Local-engine honesty (mirrors the desktop `useOnboardingComposerModels`):
// when the user picked the on-device model (a download was started →
// `localModelPct !== null`), the just-pulled model may not yet be in the
// `/v1/agent/models` catalog, so this injects a stable on-device entry as the
// selectable lead. Its wire `model_name` tracks the resolved Ollama tag as it
// lands, while its id stays stable so the selection never churns.

import { useCallback, useEffect, useMemo, useState } from "react";

import { QWEN3_4B_PRESET } from "@0x-copilot/chat-surface";
import type {
  ModelCatalogModel,
  ModelSelectionRequest,
} from "@0x-copilot/api-types";

import { listModels } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";

/** Stable id for the injected on-device engine row — keeps the selection from
 *  churning as the resolved Ollama tag lands mid-download. */
export const LOCAL_ENGINE_MODEL_ID = "first-run-local";

export type OnboardingCatalogModel = ModelCatalogModel & {
  disabled?: boolean;
};

export interface OnboardingComposerModels {
  readonly models: OnboardingCatalogModel[];
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
}

export interface UseOnboardingComposerModelsArgs {
  readonly identity: RequestIdentity;
  /** P2 download progress; `null` until a local pull starts (→ key engine). */
  readonly localModelPct: number | null;
  /** Resolved Ollama tag once the pull completes (the run `model_name`). */
  readonly modelName: string | null;
}

export function useOnboardingComposerModels(
  args: UseOnboardingComposerModelsArgs,
): OnboardingComposerModels {
  const { identity, localModelPct, modelName } = args;
  const [catalog, setCatalog] = useState<OnboardingCatalogModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    listModels(identity)
      .then((res) => {
        if (cancelled) return;
        // The catalog's per-item `configured` reflects the user's BYOK keys;
        // surface unusable rows disabled rather than hidden (honest picker).
        setCatalog(
          res.models.map((model) => ({
            ...model,
            disabled: model.configured === false,
          })),
        );
      })
      .catch(() => {
        // Catalog probe failed → empty list; the run-start error path is the
        // backstop if the user sends without a usable model.
        if (!cancelled) setCatalog([]);
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  const isLocalEngine = localModelPct !== null;

  const models = useMemo<OnboardingCatalogModel[]>(() => {
    if (!isLocalEngine) {
      return catalog;
    }
    // Local engine — surface the on-device model as the honest, selectable lead
    // even before `/v1/agent/models` reflects the fresh pull.
    const localEntry: OnboardingCatalogModel = {
      id: LOCAL_ENGINE_MODEL_ID,
      provider: "ollama",
      model_name: modelName ?? QWEN3_4B_PRESET.name,
      name: QWEN3_4B_PRESET.name,
      description: "On-device model",
      configured: true,
      supports_streaming: true,
    };
    return [
      localEntry,
      ...catalog.filter((m) => m.id !== LOCAL_ENGINE_MODEL_ID),
    ];
  }, [catalog, isLocalEngine, modelName]);

  // Keep a valid selection: preserve the user's pick when still present, else
  // fall back to the first usable model (the on-device entry leads on the local
  // path).
  useEffect(() => {
    setSelectedModel((current) =>
      current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models),
    );
  }, [models]);

  const onModelChange = useCallback((id: string): void => {
    setSelectedModel(id);
  }, []);

  return { models, selectedModel, onModelChange };
}

/** First selectable (configured, enabled, non-disabled) model, else the first
 *  entry. */
export function defaultSelectedModelId(
  models: readonly OnboardingCatalogModel[],
): string {
  const usable = models.find(
    (m) => m.configured && m.disabled !== true && m.enabled !== false,
  );
  return (usable ?? models[0])?.id ?? "";
}

/** Wire `model` selection for a run-create body, resolved from the picked id.
 *  Mirrors the desktop `modelSelectionForId`. */
export function modelSelectionForId(
  models: readonly OnboardingCatalogModel[],
  id: string,
): ModelSelectionRequest | null {
  if (id === "") {
    return null;
  }
  const model = models.find((m) => m.id === id);
  if (!model) {
    // Unknown id — send the bare model name so the runtime can still resolve it.
    return { model_name: id };
  }
  return {
    provider: model.provider,
    model_name: model.model_name,
    reasoning: model.reasoning ?? null,
  };
}
