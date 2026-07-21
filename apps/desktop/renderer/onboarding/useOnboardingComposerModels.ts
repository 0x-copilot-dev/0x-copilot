// Model catalog for the FTUE onboarding composer's model pill.
//
// A focused subset of the Run cockpit's catalog wiring (RunComposer.tsx): the
// same curated-cloud + BYOK-gating + local-model helpers from
// `desktopModelCatalog.ts`, but without skills / MCP servers / workspace
// defaults (the FTUE composer keeps Tools minimal — P4). Two probes drive it:
// `/v1/settings/provider-keys` (which curated cloud rows are selectable) and
// `/v1/local-models` (installed Ollama tags).
//
// Local-engine honesty: when the user picked the on-device model (a download was
// started → `localModelPct !== null`), the just-pulled model may not yet be in
// the `/v1/local-models` list, so this injects a stable on-device entry as the
// selectable lead. Its wire `model_name` tracks the resolved Ollama tag as it
// lands, while its id stays stable so the selection never churns.

import { useCallback, useEffect, useMemo, useState } from "react";

import { QWEN3_4B_PRESET } from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";

import {
  buildModelCatalog,
  defaultSelectedModelId,
  type CatalogModel,
} from "../composer/desktopModelCatalog";

/** Stable id for the injected on-device engine row — keeps the selection from
 *  churning as the resolved Ollama tag lands mid-download. */
export const LOCAL_ENGINE_MODEL_ID = "first-run-local";

interface ProviderKeysResponseLite {
  readonly keys?: readonly { readonly provider?: string }[];
}
interface LocalModelsResponseLite {
  readonly models?: readonly { readonly name?: string }[];
}

export interface OnboardingComposerModels {
  readonly models: CatalogModel[];
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
}

export interface OnboardingLocalEngine {
  /** P2 download progress; `null` until a local pull starts (→ key engine). */
  readonly localModelPct: number | null;
  /** Resolved Ollama tag once the pull completes (the run `model_name`). */
  readonly modelName: string | null;
}

export function useOnboardingComposerModels(
  transport: Transport,
  local: OnboardingLocalEngine,
): OnboardingComposerModels {
  const [configuredProviders, setConfiguredProviders] = useState<
    ReadonlySet<string>
  >(new Set());
  const [providersKnown, setProvidersKnown] = useState(false);
  const [localModelNames, setLocalModelNames] = useState<readonly string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<ProviderKeysResponseLite>({
        method: "GET",
        path: "/v1/settings/provider-keys",
      })
      .then((res) => {
        if (cancelled) return;
        const providers = new Set<string>();
        for (const key of res.keys ?? []) {
          if (key.provider) providers.add(key.provider);
          // The key store speaks `google`; the curated catalog speaks `gemini`.
          if (key.provider === "google") providers.add("gemini");
        }
        setConfiguredProviders(providers);
        setProvidersKnown(true);
      })
      .catch(() => {
        // Probe failed → leave `providersKnown` false so the catalog fails open.
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<LocalModelsResponseLite>({
        method: "GET",
        path: "/v1/local-models",
      })
      .then((res) => {
        if (cancelled) return;
        const names = (res.models ?? [])
          .map((m) => m.name)
          .filter((n): n is string => typeof n === "string" && n.length > 0);
        setLocalModelNames(names);
      })
      .catch(() => {
        // Local models are optional/server-gated (404 when off) → empty list.
        if (!cancelled) setLocalModelNames([]);
      });
    return () => {
      cancelled = true;
    };
  }, [transport]);

  const isLocalEngine = local.localModelPct !== null;

  const models = useMemo<CatalogModel[]>(() => {
    const base = buildModelCatalog({
      configuredProviders,
      providersKnown,
      localModelNames,
    });
    if (!isLocalEngine) {
      return base;
    }
    // Local engine — surface the on-device model as the honest, selectable lead
    // even before `/v1/local-models` reflects the fresh pull.
    const localEntry: CatalogModel = {
      id: LOCAL_ENGINE_MODEL_ID,
      provider: "ollama",
      model_name: local.modelName ?? QWEN3_4B_PRESET.name,
      name: QWEN3_4B_PRESET.name,
      description: "On-device model",
      configured: true,
      supports_streaming: true,
    };
    return [localEntry, ...base.filter((m) => m.id !== LOCAL_ENGINE_MODEL_ID)];
  }, [
    configuredProviders,
    providersKnown,
    localModelNames,
    isLocalEngine,
    local.modelName,
  ]);

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
