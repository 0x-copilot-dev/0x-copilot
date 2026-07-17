import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ListProviderKeysResponse,
  ProviderKeySummary,
} from "@enterprise-search/api-types";

import { configureAuthBearerProvider } from "./http";
import {
  deleteProviderKey,
  listProviderKeys,
  putProviderKey,
} from "./providerKeysApi";

// Clearly-fake placeholder — never commit real-looking provider secrets.
const FAKE_KEY = "sk-unit-test-placeholder-not-a-real-key";

function summaryFixture(): ProviderKeySummary {
  return {
    provider: "openai",
    key_hint: "…l-key",
    updated_at: "2026-07-17T09:00:00Z",
  };
}

function listFixture(): ListProviderKeysResponse {
  return { keys: [summaryFixture()] };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fetchMockReturning(
  responder: () => Response,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    responder(),
  );
}

describe("provider keys api", () => {
  beforeEach(() => configureAuthBearerProvider(() => "test-bearer"));
  afterEach(() => {
    configureAuthBearerProvider(() => null);
    vi.unstubAllGlobals();
  });

  it("GETs /v1/settings/provider-keys", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(listFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await listProviderKeys();
    expect(res.keys[0].provider).toBe("openai");
    expect(res.keys[0].key_hint).toBe("…l-key");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/settings/provider-keys");
    // Facade only — never a direct backend port.
    expect(url).not.toMatch(/^https?:\/\/(127\.0\.0\.1|localhost):(8000|8100)/);
  });

  it("PUTs /v1/settings/provider-keys/{provider} with the plaintext body", async () => {
    const fetchMock = fetchMockReturning(() => jsonResponse(summaryFixture()));
    vi.stubGlobal("fetch", fetchMock);

    const res = await putProviderKey("openai", { api_key: FAKE_KEY });
    expect(res.key_hint).toBe("…l-key");
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/settings/provider-keys/openai");
    expect((call[1] as RequestInit).method).toBe("PUT");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      api_key: FAKE_KEY,
    });
  });

  it("DELETEs /v1/settings/provider-keys/{provider} and tolerates 204", async () => {
    const fetchMock = fetchMockReturning(
      () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteProviderKey("anthropic")).resolves.toBeUndefined();
    const call = fetchMock.mock.calls[0];
    expect(String(call[0])).toContain("/v1/settings/provider-keys/anthropic");
    expect((call[1] as RequestInit).method).toBe("DELETE");
  });

  it("propagates the server's format 400 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse(
          { detail: "API key format doesn't match provider openai." },
          400,
        ),
      ),
    );
    await expect(
      putProviderKey("openai", { api_key: "bad-prefix-0000000000" }),
    ).rejects.toThrow("API key format doesn't match provider openai.");
  });

  it("propagates the unknown-provider 422 as an Error", async () => {
    vi.stubGlobal(
      "fetch",
      fetchMockReturning(() =>
        jsonResponse({ detail: "unknown provider" }, 422),
      ),
    );
    await expect(
      putProviderKey("openai", { api_key: FAKE_KEY }),
    ).rejects.toThrow("unknown provider");
  });
});
