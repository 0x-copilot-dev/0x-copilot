// FR-5.26 — the provider-keys data seam builds the expected TypedRequest
// (method / path / body) and calls the injected Transport, proving there is no
// bare `fetch`. Plus the pure `checkProviderKeyFormat` gate.

import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "../../ports/Transport";
import {
  PROVIDER_CATALOG,
  checkProviderKeyFormat,
  createProviderKeysPort,
  providerCatalogEntry,
} from "./providerKeys";

function fakeTransport(handler: (req: TypedRequest) => unknown): {
  readonly transport: Transport;
  readonly calls: TypedRequest[];
} {
  const calls: TypedRequest[] = [];
  const request = (async (req: TypedRequest) => {
    calls.push(req);
    return handler(req);
  }) as Transport["request"];
  const transport: Transport = {
    request,
    subscribeServerSentEvents: vi.fn(() => ({ close: () => undefined })),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, calls };
}

// Clearly-fake placeholder — passes the client format check only.
const FAKE_OPENAI = "sk-unit-test-placeholder-not-a-real-key";

describe("checkProviderKeyFormat", () => {
  const openai = providerCatalogEntry("openai");

  it("rejects an empty key", () => {
    expect(openai).toBeDefined();
    expect(checkProviderKeyFormat(openai!, "   ").ok).toBe(false);
  });

  it("rejects a wrong prefix with a helpful message", () => {
    const result = checkProviderKeyFormat(
      openai!,
      "nope-0000000000000000000000",
    );
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/start with/i);
  });

  it("rejects an implausibly short key", () => {
    const result = checkProviderKeyFormat(openai!, "sk-short");
    expect(result.ok).toBe(false);
  });

  it("accepts a well-formed key and returns the catalog models", () => {
    const result = checkProviderKeyFormat(openai!, FAKE_OPENAI);
    expect(result.ok).toBe(true);
    expect(result.models).toEqual(openai!.models);
  });
});

describe("PROVIDER_CATALOG", () => {
  it("carries the six DESIGN-SPEC §4 providers", () => {
    expect(PROVIDER_CATALOG.map((entry) => entry.id)).toEqual([
      "anthropic",
      "openai",
      "openrouter",
      "google",
      "groq",
      "xai",
    ]);
  });

  it("flags Groq/xAI as not-yet-contract-backed (PRD §5.5 drift)", () => {
    expect(providerCatalogEntry("groq")?.contractBacked).toBe(false);
    expect(providerCatalogEntry("xai")?.contractBacked).toBe(false);
    expect(providerCatalogEntry("openai")?.contractBacked).toBe(true);
  });
});

describe("createProviderKeysPort", () => {
  it("list GETs /v1/settings/provider-keys and unwraps keys", async () => {
    const { transport, calls } = fakeTransport(() => ({
      keys: [{ provider: "openai", key_hint: "…1234", updated_at: "x" }],
    }));
    const keys = await createProviderKeysPort(transport).list();
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/settings/provider-keys",
    });
    expect(keys).toHaveLength(1);
  });

  it("save PUTs the plaintext exactly once, in the body", async () => {
    const { transport, calls } = fakeTransport(() => ({
      provider: "openai",
      key_hint: "…real",
      updated_at: "x",
    }));
    await createProviderKeysPort(transport).save("openai", FAKE_OPENAI);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      method: "PUT",
      path: "/v1/settings/provider-keys/openai",
      body: { api_key: FAKE_OPENAI },
    });
  });

  it("remove DELETEs and url-encodes the provider slug", async () => {
    const { transport, calls } = fakeTransport(() => undefined);
    await createProviderKeysPort(transport).remove("open router");
    expect(calls[0]).toMatchObject({
      method: "DELETE",
      path: "/v1/settings/provider-keys/open%20router",
    });
  });

  it("ships no validate seam (format check is the default gate)", () => {
    const { transport } = fakeTransport(() => undefined);
    expect(createProviderKeysPort(transport).validate).toBeUndefined();
  });
});
