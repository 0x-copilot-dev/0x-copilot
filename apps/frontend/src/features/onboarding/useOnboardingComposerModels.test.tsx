// Onboarding composer model catalog — live /v1/agent/models + local-engine
// honesty. The PURE selection helpers (`defaultSelectedModelId`,
// `mergeCatalog`, `modelSelectionForId`) now live in `@0x-copilot/chat-surface`
// and are pinned by `packages/chat-surface/src/composer/modelCatalog.test.ts`;
// what this file owns is that the web FTUE actually BINDS them.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import type { PickerCatalogModel } from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";

vi.mock("../../api/agentApi", () => ({ listModels: vi.fn() }));

import { listModels } from "../../api/agentApi";
import {
  LOCAL_ENGINE_MODEL_ID,
  useOnboardingComposerModels,
} from "./useOnboardingComposerModels";

const IDENTITY: RequestIdentity = { orgId: "org_1", userId: "user_1" };

function model(overrides: Partial<PickerCatalogModel>): PickerCatalogModel {
  return {
    id: "m",
    provider: "openai",
    model_name: "m",
    name: "M",
    configured: true,
    ...overrides,
  };
}

describe("useOnboardingComposerModels", () => {
  beforeEach(() => {
    vi.mocked(listModels).mockReset();
  });

  it("fetches /v1/agent/models and marks unconfigured rows disabled", async () => {
    vi.mocked(listModels).mockResolvedValue({
      models: [
        model({ id: "gpt", configured: true }),
        model({ id: "claude", provider: "anthropic", configured: false }),
      ],
    } as never);

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: null,
        modelName: null,
      }),
    );

    await waitFor(() => expect(result.current.models).toHaveLength(2));
    expect(listModels).toHaveBeenCalledWith(IDENTITY);
    expect(result.current.models[0].disabled).toBe(false);
    expect(result.current.models[1].disabled).toBe(true);
    // Default selection = the usable (configured) model.
    expect(result.current.selectedModel).toBe("gpt");
  });

  it("opens on the provider's everyday rung, never the off-ladder priciest row", async () => {
    // The bug this binding fixes. The FTUE used to auto-select the first
    // configured row in CATALOG order, so an Anthropic-only user landed on
    // "Claude Fable 5" — off the size ladder (`tier: null`) and the dearest
    // model Anthropic sells — instead of the mid rung. Same ranking the desktop
    // composer uses; the shared helper is what makes them agree.
    vi.mocked(listModels).mockResolvedValue({
      models: [
        model({
          id: "claude-fable-5",
          provider: "anthropic",
          name: "Claude Fable 5",
          configured: true,
          tier: null,
          output_cost_per_mtok: 90,
        }),
        model({
          id: "claude-haiku-4-5",
          provider: "anthropic",
          name: "Claude Haiku 4.5",
          configured: true,
          tier: "small",
          output_cost_per_mtok: 5,
        }),
        model({
          id: "claude-sonnet-5",
          provider: "anthropic",
          name: "Claude Sonnet 5",
          configured: true,
          tier: "medium",
          output_cost_per_mtok: 15,
        }),
      ],
    } as never);

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: null,
        modelName: null,
      }),
    );

    await waitFor(() =>
      expect(result.current.selectedModel).toBe("claude-sonnet-5"),
    );
  });

  it("never auto-selects a keyless row, even when it leads the catalog", async () => {
    // The backend catalog puts the deployment default (an OpenAI row) FIRST, so
    // the old `usable ?? models[0]` fallback could preselect a model the user
    // has no key for. Nothing usable → "" and the run-start gate is the backstop.
    vi.mocked(listModels).mockResolvedValue({
      models: [
        model({ id: "gpt-5.4-mini", provider: "openai", configured: false }),
        model({ id: "claude", provider: "anthropic", configured: false }),
      ],
    } as never);

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: null,
        modelName: null,
      }),
    );

    await waitFor(() => expect(result.current.models).toHaveLength(2));
    expect(result.current.selectedModel).toBe("");
  });

  it("honors the backend default_model_id when it is usable", async () => {
    // The deployment's declared default is an explicit choice, so it outranks
    // the tier heuristic inside the winning provider.
    vi.mocked(listModels).mockResolvedValue({
      default_model_id: "gpt-big",
      models: [
        model({ id: "gpt-mid", configured: true, tier: "medium" }),
        model({ id: "gpt-big", configured: true, tier: "big" }),
      ],
    } as never);

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: null,
        modelName: null,
      }),
    );

    await waitFor(() => expect(result.current.selectedModel).toBe("gpt-big"));
  });

  it("injects the on-device engine as the selectable lead during a local pull", async () => {
    vi.mocked(listModels).mockResolvedValue({
      models: [model({ id: "gpt" })],
    } as never);

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: 42,
        modelName: "qwen3:4b",
      }),
    );

    // Wait for the async /v1/agent/models catalog to land behind the injected
    // on-device lead (the local entry appears on the first render, so we key on
    // the cloud row instead to know the fetch has resolved).
    await waitFor(() =>
      expect(result.current.models.map((m) => m.id)).toContain("gpt"),
    );
    // The on-device engine leads and is auto-selected.
    expect(result.current.models[0].id).toBe(LOCAL_ENGINE_MODEL_ID);
    // The wire model_name tracks the resolved Ollama tag as it lands.
    expect(result.current.models[0].model_name).toBe("qwen3:4b");
    expect(result.current.selectedModel).toBe(LOCAL_ENGINE_MODEL_ID);
  });

  it("degrades to an empty catalog when the models probe fails", async () => {
    vi.mocked(listModels).mockRejectedValue(new Error("no catalog"));

    const { result } = renderHook(() =>
      useOnboardingComposerModels({
        identity: IDENTITY,
        localModelPct: null,
        modelName: null,
      }),
    );

    // Nothing to select; the run-start error path is the backstop.
    await waitFor(() => expect(result.current.selectedModel).toBe(""));
    expect(result.current.models).toHaveLength(0);
  });
});
