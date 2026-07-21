// Web FirstRun ProviderKeysPort — thin adapter over api/providerKeysApi.

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProviderKeySummary } from "@0x-copilot/api-types";

vi.mock("../../api/providerKeysApi", () => ({
  listProviderKeys: vi.fn(),
  putProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import {
  deleteProviderKey,
  listProviderKeys,
  putProviderKey,
} from "../../api/providerKeysApi";
import { createFirstRunProviderKeysPort } from "./firstRunProviderKeysPort";

const SUMMARY: ProviderKeySummary = {
  provider: "anthropic",
  key_hint: "…key",
  updated_at: "2026-07-22T00:00:00Z",
};

describe("createFirstRunProviderKeysPort", () => {
  beforeEach(() => {
    vi.mocked(listProviderKeys).mockReset();
    vi.mocked(putProviderKey).mockReset();
    vi.mocked(deleteProviderKey).mockReset();
  });

  it("list() returns the masked summaries from the list response", async () => {
    vi.mocked(listProviderKeys).mockResolvedValue({ keys: [SUMMARY] });
    const port = createFirstRunProviderKeysPort();
    await expect(port.list()).resolves.toEqual([SUMMARY]);
  });

  it("save() PUTs only the api_key when no default model is given", async () => {
    vi.mocked(putProviderKey).mockResolvedValue(SUMMARY);
    const port = createFirstRunProviderKeysPort();
    await port.save("anthropic", "sk-ant-abcdefghijklmnop");
    expect(putProviderKey).toHaveBeenCalledWith("anthropic", {
      api_key: "sk-ant-abcdefghijklmnop",
    });
  });

  it("save() includes default_model when provided", async () => {
    vi.mocked(putProviderKey).mockResolvedValue(SUMMARY);
    const port = createFirstRunProviderKeysPort();
    await port.save("openai", "sk-abcdefghijklmnop", "gpt-5.2");
    expect(putProviderKey).toHaveBeenCalledWith("openai", {
      api_key: "sk-abcdefghijklmnop",
      default_model: "gpt-5.2",
    });
  });

  it("save() ignores an empty-string default_model", async () => {
    vi.mocked(putProviderKey).mockResolvedValue(SUMMARY);
    const port = createFirstRunProviderKeysPort();
    await port.save("openai", "sk-abcdefghijklmnop", "");
    expect(putProviderKey).toHaveBeenCalledWith("openai", {
      api_key: "sk-abcdefghijklmnop",
    });
  });

  it("remove() deletes the provider key", async () => {
    vi.mocked(deleteProviderKey).mockResolvedValue(undefined);
    const port = createFirstRunProviderKeysPort();
    await port.remove("openrouter");
    expect(deleteProviderKey).toHaveBeenCalledWith("openrouter");
  });
});
