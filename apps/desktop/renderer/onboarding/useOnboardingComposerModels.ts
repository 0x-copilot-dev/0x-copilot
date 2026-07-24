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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { QWEN3_4B_PRESET } from "@0x-copilot/chat-surface";
import type { Transport } from "@0x-copilot/chat-transport";
import type { ModelCatalogModel } from "@0x-copilot/api-types";

import {
  defaultSelectedModelId,
  mergeCatalog,
  type CatalogModel,
} from "../composer/desktopModelCatalog";

/** Stable id for the injected on-device engine row — keeps the selection from
 *  churning as the resolved Ollama tag lands mid-download. */
export const LOCAL_ENGINE_MODEL_ID = "first-run-local";

interface ModelCatalogResponseLite {
  readonly models?: readonly ModelCatalogModel[];
  readonly default_model_id?: string;
}
interface LocalModelsResponseLite {
  readonly models?: readonly { readonly name?: string }[];
}

export interface OnboardingComposerModels {
  readonly models: CatalogModel[];
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
  /**
   * Refetch the backend catalog and, when `preferProvider` is given, auto-select
   * that provider's first usable model. Called after a provider key is saved so
   * the just-configured provider's model is picked (and its rows stop reading
   * "needs key") without a surface remount.
   */
  readonly refresh: (preferProvider?: string) => void;
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
  const [cloudModels, setCloudModels] = useState<readonly ModelCatalogModel[]>(
    [],
  );
  const [defaultModelId, setDefaultModelId] = useState<string>("");
  const [localModelNames, setLocalModelNames] = useState<readonly string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  // Bumping this re-runs the catalog fetch. `refresh()` bumps it after a key is
  // saved so `configured` reflects the new BYOK key — the catalog was otherwise
  // fetched once at mount (before any key existed), so every cloud row stayed
  // "needs key" forever and the just-added model could never be selected.
  const [reloadToken, setReloadToken] = useState(0);
  // The provider whose key was just added — steers the NEXT selection to that
  // provider's model (add OpenAI → GPT-5.4 Mini, not a leftover keyless pick).
  // Consumed by the selection effect once the refetched models land.
  const preferProviderRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void transport
      .request<ModelCatalogResponseLite>({
        method: "GET",
        path: "/v1/agent/models",
      })
      .then((res) => {
        if (cancelled) return;
        setCloudModels(res.models ?? []);
        setDefaultModelId(res.default_model_id ?? "");
      })
      .catch(() => {
        // Catalog probe failed → empty cloud list; the run-start gate is the
        // backstop if the user sends without a usable model.
        if (!cancelled) setCloudModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, reloadToken]);

  const refresh = useCallback((preferProvider?: string): void => {
    if (preferProvider !== undefined && preferProvider !== "") {
      preferProviderRef.current = preferProvider;
    }
    setReloadToken((token) => token + 1);
  }, []);

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
    const base = mergeCatalog({ cloudModels, localModelNames });
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
  }, [cloudModels, localModelNames, isLocalEngine, local.modelName]);

  // Selection policy:
  //   • a provider key was JUST added (`preferProviderRef`) → jump to that
  //     provider's model, overriding a stale keyless / wrong-provider pick;
  //   • otherwise preserve the user's pick when still present;
  //   • else fall back to the provider-aware default (backend `default_model_id`
  //     when usable, else first usable; the on-device entry leads on local).
  useEffect(() => {
    const prefer = preferProviderRef.current;
    if (prefer !== null) {
      const picked = defaultSelectedModelId(models, {
        preferProvider: prefer,
        defaultModelId,
      });
      if (picked === "") {
        // Preferred provider not usable yet — a refetch after the key save may
        // still be in flight. Keep the hint and wait for the next catalog update
        // rather than consuming it against a stale list.
        return;
      }
      preferProviderRef.current = null;
      setSelectedModel(picked);
      return;
    }
    setSelectedModel((current) =>
      current !== "" && models.some((m) => m.id === current)
        ? current
        : defaultSelectedModelId(models, { defaultModelId }),
    );
  }, [models, defaultModelId]);

  const onModelChange = useCallback((id: string): void => {
    setSelectedModel(id);
  }, []);

  return { models, selectedModel, onModelChange, refresh };
}
