// FR-5.26 — the developer-tokens data seam builds the expected TypedRequest
// (method / path / body) and calls the injected Transport, proving there is no
// bare `fetch`. Plus the masked-identity / last-used presentation helpers.

import { describe, expect, it, vi } from "vitest";

import type { ApiKeySummary } from "@0x-copilot/api-types";

import type { Transport, TypedRequest } from "../../ports/Transport";
import {
  createDeveloperTokensPort,
  lastUsedLabel,
  maskDeveloperToken,
} from "./developerTokens";

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

function summary(overrides: Partial<ApiKeySummary> = {}): ApiKeySummary {
  return {
    id: "tok_1",
    label: "laptop CLI",
    key_prefix: "atlas_pk_abcd",
    scopes: [],
    last_used_at: null,
    created_at: "2026-07-01T10:00:00Z",
    rotated_from_id: null,
    kind: "personal",
    ...overrides,
  };
}

describe("createDeveloperTokensPort", () => {
  it("list GETs /v1/me/api-keys and unwraps keys", async () => {
    const { transport, calls } = fakeTransport(() => ({
      keys: [summary()],
    }));
    const keys = await createDeveloperTokensPort(transport).list();
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/v1/me/api-keys",
    });
    expect(keys).toHaveLength(1);
  });

  it("create POSTs the label and returns the plaintext response", async () => {
    const { transport, calls } = fakeTransport(() => ({
      key: summary({ id: "tok_new", label: "ci" }),
      plaintext: "atlas_pk_secret_value",
    }));
    const res = await createDeveloperTokensPort(transport).create("ci");
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/v1/me/api-keys",
      body: { label: "ci" },
    });
    expect(res.plaintext).toBe("atlas_pk_secret_value");
  });

  it("revoke DELETEs and url-encodes the token id", async () => {
    const { transport, calls } = fakeTransport(() => undefined);
    await createDeveloperTokensPort(transport).revoke("tok/1");
    expect(calls[0]).toMatchObject({
      method: "DELETE",
      path: "/v1/me/api-keys/tok%2F1",
    });
  });
});

describe("presentation helpers", () => {
  it("masks a token as its prefix plus an ellipsis", () => {
    expect(maskDeveloperToken(summary({ key_prefix: "atlas_pk_xyz" }))).toBe(
      "atlas_pk_xyz…",
    );
  });

  it("labels a never-used token", () => {
    expect(lastUsedLabel(summary({ last_used_at: null }))).toBe("Never used");
  });

  it("labels a used token with its date", () => {
    expect(
      lastUsedLabel(summary({ last_used_at: "2026-07-10T00:00:00Z" })),
    ).toMatch(/^Last used /);
  });
});
