// Model catalog for the FTUE onboarding composer's model pill (web).
//
// The FTUE composer reads the LIVE catalog — `GET /v1/agent/models` via
// `api/agentApi.listModels` — never a hardcoded list (SPEC / FirstRunSurface's
// `models` contract). Each catalog row carries a server-computed `configured`
// flag (true only when the user has that provider's BYOK key), so unusable
// models are shown disabled rather than hidden.
//
// The PURE half — folding the fetched catalog into the picker shape, ranking
// which row the pill opens on, and resolving a pick to the run-create wire
// selection — is `@0x-copilot/chat-surface`'s `composer/modelCatalog`, shared
// with the desktop host. This file owns only the impure half: the fetch, the
// local-engine injection, and the React state. It used to carry its own copy of
// `defaultSelectedModelId` that was just "first configured row in catalog
// order", which is how an Anthropic-only user opened the FTUE on Claude Fable 5
// — the dearest model Anthropic sells.
//
// Local-engine honesty (mirrors the desktop `useOnboardingComposerModels`):
// when the user picked the on-device model (a download was started →
// `localModelPct !== null`), the just-pulled model may not yet be in the
// `/v1/agent/models` catalog, so this injects a stable on-device entry as the
// selectable lead. Its wire `model_name` tracks the resolved Ollama tag as it
// lands, while its id stays stable so the selection never churns.

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  QWEN3_4B_PRESET,
  defaultSelectedModelId,
  mergeCatalog,
  type PickerCatalogModel,
} from "@0x-copilot/chat-surface";

import { listModels } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";

/** Stable id for the injected on-device engine row — keeps the selection from
 *  churning as the resolved Ollama tag lands mid-download. */
export const LOCAL_ENGINE_MODEL_ID = "first-run-local";

export interface OnboardingComposerModels {
  readonly models: PickerCatalogModel[];
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
  const [catalog, setCatalog] = useState<PickerCatalogModel[]>([]);
  // The deployment's declared default — an explicit choice, so it outranks the
  // tier heuristic within the winning provider (see `defaultSelectedModelId`).
  const [defaultModelId, setDefaultModelId] = useState<string>("");
  const [selectedModel, setSelectedModel] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    listModels(identity)
      .then((res) => {
        if (cancelled) return;
        // The catalog's per-item `configured` reflects the user's BYOK keys;
        // `mergeCatalog` surfaces unusable rows disabled rather than hidden
        // (honest picker). No local models on the web host — the on-device
        // engine is injected below when a pull is in flight.
        setCatalog(
          mergeCatalog({ cloudModels: res.models, localModelNames: [] }),
        );
        setDefaultModelId(res.default_model_id ?? "");
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

  const models = useMemo<PickerCatalogModel[]>(() => {
    if (!isLocalEngine) {
      return catalog;
    }
    // Local engine — surface the on-device model as the honest, selectable lead
    // even before `/v1/agent/models` reflects the fresh pull.
    const localEntry: PickerCatalogModel = {
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
  // fall back to the shared provider-aware default — the backend
  // `default_model_id` when usable, else the winning provider's preferred rung
  // (never an off-ladder specialty row, never a keyless one). The on-device
  // entry leads on the local path and is the only usable row on the first pass,
  // so it wins that fallback outright.
  useEffect(() => {
    setSelectedModel((current) =>
      current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models, { defaultModelId }),
    );
  }, [models, defaultModelId]);

  const onModelChange = useCallback((id: string): void => {
    setSelectedModel(id);
  }, []);

  return { models, selectedModel, onModelChange };
}
