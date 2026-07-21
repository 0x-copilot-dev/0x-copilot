// PR-3D — ModelsPort adapter + pure view helpers.

import { describe, expect, it, vi } from "vitest";

import type {
  ModelCatalogModel,
  ModelCatalogResponse,
  WorkspaceDefaultsResponse,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";
import {
  contextLabel,
  createModelsPort,
  filterModels,
  groupModelsByProvider,
  priceLabel,
  providerLabel,
} from "./models";

function model(
  id: string,
  provider: string,
  extra: Partial<ModelCatalogModel> = {},
): ModelCatalogModel {
  return {
    id,
    provider,
    model_name: id,
    name: id,
    configured: true,
    enabled: true,
    ...extra,
  };
}

const DEFAULTS: WorkspaceDefaultsResponse = {
  default_model: { provider: "openai", model_name: "gpt-5.4-mini" },
  default_connectors: {},
  retention_days: 90,
  behavior_overrides: {},
  enabled_models: null,
  updated_at: null,
  updated_by_user_id: null,
};

describe("groupModelsByProvider", () => {
  it("orders known providers by priority, local last, unknown alphabetical", () => {
    const groups = groupModelsByProvider([
      model("z", "zebra"),
      model("l", "ollama"),
      model("a", "anthropic"),
      model("o", "openai"),
    ]);
    expect(groups.map((g) => g.provider)).toEqual([
      "openai",
      "anthropic",
      "ollama",
      "zebra",
    ]);
    expect(groups[0].label).toBe("OpenAI");
  });
});

describe("filterModels", () => {
  const models = [model("gpt-4o", "openai"), model("claude", "anthropic")];
  it("matches id / provider / name case-insensitively", () => {
    expect(filterModels(models, "GPT").map((m) => m.id)).toEqual(["gpt-4o"]);
    expect(filterModels(models, "anthropic").map((m) => m.id)).toEqual([
      "claude",
    ]);
  });
  it("returns all on empty query", () => {
    expect(filterModels(models, "  ")).toHaveLength(2);
  });
});

describe("label helpers", () => {
  it("formats price, context, and provider labels", () => {
    expect(priceLabel(model("m", "openai", { input_cost_per_mtok: 2.5 }))).toBe(
      "$2.50/M in",
    );
    expect(priceLabel(model("m", "openai", { input_cost_per_mtok: 0 }))).toBe(
      "Free",
    );
    expect(priceLabel(model("m", "openai"))).toBeNull();
    expect(
      contextLabel(model("m", "openai", { context_window: 128_000 })),
    ).toBe("128K ctx");
    expect(
      contextLabel(model("m", "openai", { context_window: 1_000_000 })),
    ).toBe("1M ctx");
    expect(providerLabel("gemini")).toBe("Google Gemini");
  });
});

describe("createModelsPort", () => {
  it("lists models from /v1/agent/models", async () => {
    const request = vi.fn().mockResolvedValue({
      default_model_id: "gpt-5.4-mini",
      models: [model("gpt-4o", "openai")],
    } satisfies ModelCatalogResponse);
    const port = createModelsPort({ request } as unknown as Transport);
    const models = await port.list();
    expect(models).toHaveLength(1);
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "GET", path: "/v1/agent/models" }),
    );
  });

  it("read-merge-writes enabled_models onto workspace defaults, then re-lists", async () => {
    const request = vi
      .fn()
      // 1. GET workspace defaults (read side of the merge)
      .mockResolvedValueOnce(DEFAULTS)
      // 2. PUT workspace defaults
      .mockResolvedValueOnce({ ...DEFAULTS, enabled_models: ["gpt-4o"] })
      // 3. re-list catalog
      .mockResolvedValueOnce({
        default_model_id: "gpt-5.4-mini",
        models: [model("gpt-4o", "openai")],
      });
    const port = createModelsPort({ request } as unknown as Transport);
    await port.setEnabled(["gpt-4o"]);

    const put = request.mock.calls.find((c) => c[0].method === "PUT");
    expect(put?.[0].path).toBe("/v1/agent/workspace/defaults");
    // The full document is replaced, with only enabled_models swapped.
    expect(put?.[0].body).toMatchObject({
      default_model: DEFAULTS.default_model,
      retention_days: 90,
      enabled_models: ["gpt-4o"],
    });
  });

  it("clears curation with null", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(DEFAULTS)
      .mockResolvedValueOnce(DEFAULTS)
      .mockResolvedValueOnce({ default_model_id: "x", models: [] });
    const port = createModelsPort({ request } as unknown as Transport);
    await port.setEnabled(null);
    const put = request.mock.calls.find((c) => c[0].method === "PUT");
    expect(put?.[0].body.enabled_models).toBeNull();
  });
});
