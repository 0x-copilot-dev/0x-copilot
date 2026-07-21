// Onboarding composer model catalog — live /v1/agent/models + local-engine
// honesty + pure selection helpers.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import type { RequestIdentity } from "../../api/config";

vi.mock("../../api/agentApi", () => ({ listModels: vi.fn() }));

import { listModels } from "../../api/agentApi";
import {
  LOCAL_ENGINE_MODEL_ID,
  defaultSelectedModelId,
  modelSelectionForId,
  useOnboardingComposerModels,
  type OnboardingCatalogModel,
} from "./useOnboardingComposerModels";

const IDENTITY: RequestIdentity = { orgId: "org_1", userId: "user_1" };

function model(
  overrides: Partial<OnboardingCatalogModel>,
): OnboardingCatalogModel {
  return {
    id: "m",
    provider: "openai",
    model_name: "m",
    name: "M",
    configured: true,
    ...overrides,
  };
}

describe("defaultSelectedModelId", () => {
  it("prefers the first configured, enabled, non-disabled model", () => {
    expect(
      defaultSelectedModelId([
        model({ id: "a", configured: false, disabled: true }),
        model({ id: "b", configured: true }),
      ]),
    ).toBe("b");
  });

  it("falls back to the first entry when none are usable", () => {
    expect(
      defaultSelectedModelId([
        model({ id: "a", configured: false, disabled: true }),
      ]),
    ).toBe("a");
  });

  it("is empty for an empty catalog", () => {
    expect(defaultSelectedModelId([])).toBe("");
  });

  it("skips models the workspace disabled via enabled:false", () => {
    expect(
      defaultSelectedModelId([
        model({ id: "a", configured: true, enabled: false }),
        model({ id: "b", configured: true, enabled: true }),
      ]),
    ).toBe("b");
  });
});

describe("modelSelectionForId", () => {
  const models = [
    model({
      id: "claude",
      provider: "anthropic",
      model_name: "claude-sonnet-4-6",
    }),
  ];

  it("returns null for the empty selection (runtime default)", () => {
    expect(modelSelectionForId(models, "")).toBeNull();
  });

  it("sends the bare model_name for an unknown id", () => {
    expect(modelSelectionForId(models, "unknown-slug")).toEqual({
      model_name: "unknown-slug",
    });
  });

  it("resolves a known id to provider + model_name + reasoning", () => {
    expect(modelSelectionForId(models, "claude")).toEqual({
      provider: "anthropic",
      model_name: "claude-sonnet-4-6",
      reasoning: null,
    });
  });
});

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
    // Default selection = first usable (configured) model.
    expect(result.current.selectedModel).toBe("gpt");
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
