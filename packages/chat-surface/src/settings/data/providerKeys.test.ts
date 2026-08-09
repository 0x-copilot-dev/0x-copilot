// FR-5.26 — the provider-keys data seam builds the expected TypedRequest
// (method / path / body) and calls the injected Transport, proving there is no
// bare `fetch`. Plus the pure `checkProviderKeyFormat` gate.

import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "../../ports/Transport";
import {
  CUSTOM_ENDPOINT_ENTRY,
  describeProviderKeyError,
  PROVIDER_CATALOG,
  checkProviderKeyFormat,
  createProviderKeysPort,
  providerCatalogEntry,
  providerKeyErrorMessage,
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
  it("carries the seven DESIGN-SPEC §4 providers, Virtuals first", () => {
    // Virtuals leads: it is a gateway to every other vendor here, so it is the
    // single key that unblocks the most models. Order is load-bearing — the
    // Add-key surfaces and the FTUE toggle both default to the first row.
    expect(PROVIDER_CATALOG.map((entry) => entry.id)).toEqual([
      "virtuals",
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

  it("keeps the custom endpoint OUT of the fixed catalog but resolvable (D-2)", () => {
    // Not one of the six fixed Add rows…
    expect(PROVIDER_CATALOG.map((entry) => entry.id)).not.toContain(
      "openai_compatible",
    );
    // …but resolvable by slug and marked isCustom.
    const custom = providerCatalogEntry("openai_compatible");
    expect(custom).toBe(CUSTOM_ENDPOINT_ENTRY);
    expect(custom?.isCustom).toBe(true);
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

  it("save carries base_url + label for the custom endpoint (D-2)", async () => {
    const { transport, calls } = fakeTransport(() => ({
      provider: "openai_compatible",
      key_hint: "…real",
      updated_at: "x",
      base_url: "https://my-host/v1",
      label: "My vLLM",
    }));
    await createProviderKeysPort(transport).save(
      "openai_compatible",
      FAKE_OPENAI,
      {
        defaultModel: "llama-3.1-70b",
        baseUrl: "https://my-host/v1",
        label: "My vLLM",
      },
    );
    expect(calls[0]).toMatchObject({
      method: "PUT",
      path: "/v1/settings/provider-keys/openai_compatible",
      body: {
        api_key: FAKE_OPENAI,
        default_model: "llama-3.1-70b",
        base_url: "https://my-host/v1",
        label: "My vLLM",
      },
    });
  });

  it("save omits empty base_url/label so a rotation preserves them", async () => {
    const { transport, calls } = fakeTransport(() => ({
      provider: "openai",
      key_hint: "…real",
      updated_at: "x",
    }));
    await createProviderKeysPort(transport).save("openai", FAKE_OPENAI, {
      baseUrl: "",
      label: null,
    });
    expect(calls[0]?.body).toEqual({ api_key: FAKE_OPENAI });
  });

  it("validate carries base_url for the custom endpoint probe (D-2)", async () => {
    const { transport, calls } = fakeTransport(() => ({
      valid: true,
      models: ["llama-3.1-70b"],
      reason: null,
    }));
    const port = createProviderKeysPort(transport);
    await port.validate?.("openai_compatible", FAKE_OPENAI, {
      baseUrl: "https://my-host/v1",
    });
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/v1/settings/provider-keys/openai_compatible/validate",
      body: { api_key: FAKE_OPENAI, base_url: "https://my-host/v1" },
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

  it("validate POSTs to the /validate route and maps a valid verdict to models (PRD-F FR-F.4)", async () => {
    const { transport, calls } = fakeTransport(() => ({
      valid: true,
      models: ["gpt-4o", "o3"],
      reason: null,
    }));
    const result = await createProviderKeysPort(transport).validate!(
      "openai",
      FAKE_OPENAI,
    );
    expect(calls[0]).toMatchObject({
      method: "POST",
      path: "/v1/settings/provider-keys/openai/validate",
      body: { api_key: FAKE_OPENAI },
    });
    expect(result).toEqual({ ok: true, models: ["gpt-4o", "o3"] });
  });

  it("validate maps an invalid_key verdict to ok:false", async () => {
    const { transport } = fakeTransport(() => ({
      valid: false,
      models: null,
      reason: "invalid_key",
    }));
    const result = await createProviderKeysPort(transport).validate!(
      "openai",
      FAKE_OPENAI,
    );
    expect(result.ok).toBe(false);
    expect(result.error).toBeDefined();
  });

  it("validate treats provider_unreachable as ok (verify skipped, not failed)", async () => {
    const { transport } = fakeTransport(() => ({
      valid: null,
      models: null,
      reason: "provider_unreachable",
    }));
    const result = await createProviderKeysPort(transport).validate!(
      "openai",
      FAKE_OPENAI,
    );
    expect(result).toEqual({ ok: true });
  });
});

// ---------------------------------------------------------------------------
// Reason-code copy. The shipped bug: a rejected key showed the user the
// literal string `api_key_rejected_by_provider`, because the backend's 400
// `detail` IS the reason code, every host lifts `detail` into `Error.message`,
// and both add-key surfaces rendered `err.message` verbatim.
// ---------------------------------------------------------------------------

// Every code the backend can put in that `detail`, verbatim from
// services/backend/src/backend_app/provider_keys/routes.py + service.py, plus
// one entry per `SsrfBlockReason` in ssrf_guard.py (the `base_url_rejected:`
// suffix). When the backend gains a code, add it here first — a code missing
// from the map is a raw code on a user's screen.
const BACKEND_REASON_CODES = [
  "api_key_rejected_by_provider",
  "api_key_too_short",
  "api_key_too_long",
  "api_key_contains_whitespace",
  "api_key_provider_mismatch",
  "base_url_required",
  "custom_endpoint_unavailable",
  "base_url_rejected:unsupported_scheme",
  "base_url_rejected:credentials_in_url",
  "base_url_rejected:missing_host",
  "base_url_rejected:https_required",
  "base_url_rejected:blocked_address",
  "base_url_rejected:unresolvable_host",
] as const;

describe("providerKeyErrorMessage", () => {
  it.each(BACKEND_REASON_CODES)(
    "maps %s to a sentence rather than the raw code",
    (code) => {
      const message = providerKeyErrorMessage(code, {
        providerLabel: "OpenAI",
        detectedProvider: "anthropic",
      });
      expect(message).not.toBe(code);
      // No snake_case survivor: a code spliced into the copy would carry one.
      expect(message).not.toMatch(/_/);
      // Says what happened AND what to do — never a bare fragment.
      expect(message.length).toBeGreaterThan(30);
      expect(message.trimEnd().endsWith(".")).toBe(true);
    },
  );

  it("never surfaces the literal api_key_rejected_by_provider", () => {
    // The exact string screenshotted on the packaged desktop app.
    expect(providerKeyErrorMessage("api_key_rejected_by_provider")).not.toBe(
      "api_key_rejected_by_provider",
    );
  });

  it("names the provider that did the rejecting when the label is known", () => {
    expect(
      providerKeyErrorMessage("api_key_rejected_by_provider", {
        providerLabel: "Anthropic",
      }),
    ).toMatch(/^Anthropic rejected that key\./);
  });

  it("falls back to 'the provider' when no label is supplied", () => {
    expect(providerKeyErrorMessage("api_key_rejected_by_provider")).toMatch(
      /^The provider rejected that key\./,
    );
  });

  it("resolves the detected slug to a label, so the fix is one click", () => {
    expect(
      providerKeyErrorMessage("api_key_provider_mismatch", {
        providerLabel: "OpenAI",
        detectedProvider: "anthropic",
      }),
    ).toBe(
      "That key looks like it belongs to Anthropic. Add it under Anthropic, " +
        "or paste your OpenAI key here.",
    );
  });

  it("degrades a mismatch to neutral copy when nothing was detected", () => {
    expect(
      providerKeyErrorMessage("api_key_provider_mismatch", {
        providerLabel: "OpenAI",
        detectedProvider: null,
      }),
    ).toBe(
      "That key carries another provider's prefix. Add it under the provider " +
        "it belongs to, or paste your OpenAI key here.",
    );
    expect(providerKeyErrorMessage("api_key_provider_mismatch")).toMatch(
      /a different provider/,
    );
  });

  it("never tells the user to swap a provider for itself", () => {
    // Detected === selected would otherwise read "belongs to OpenAI … paste
    // your OpenAI key", which is nonsense the user cannot act on.
    const message = providerKeyErrorMessage("api_key_provider_mismatch", {
      providerLabel: "OpenAI",
      detectedProvider: "openai",
    });
    expect(message).not.toMatch(/Add it under OpenAI/);
    expect(message).toMatch(/another provider's prefix/);
  });

  it("ignores a detected slug the catalog does not know", () => {
    // No label to name, so it must not print the slug or an empty gap.
    const message = providerKeyErrorMessage("api_key_provider_mismatch", {
      providerLabel: "OpenAI",
      detectedProvider: "not-a-provider",
    });
    expect(message).not.toMatch(/not-a-provider/);
    expect(message).toMatch(/another provider's prefix/);
  });

  it("returns an unmapped code verbatim (today's behaviour, not an empty alert)", () => {
    expect(providerKeyErrorMessage("api_key_haunted_by_ghosts")).toBe(
      "api_key_haunted_by_ghosts",
    );
    // A base_url sub-reason the guard has not shipped yet degrades the same way.
    expect(providerKeyErrorMessage("base_url_rejected:teapot")).toBe(
      "base_url_rejected:teapot",
    );
  });

  it("does not resolve inherited Object.prototype keys as copy", () => {
    expect(providerKeyErrorMessage("constructor")).toBe("constructor");
    expect(providerKeyErrorMessage("toString")).toBe("toString");
  });
});

describe("describeProviderKeyError", () => {
  it("maps a reason code carried on a rejected Error", () => {
    expect(
      describeProviderKeyError(
        new Error("api_key_rejected_by_provider"),
        "fallback",
        { providerLabel: "OpenAI" },
      ),
    ).toMatch(/^OpenAI rejected that key\./);
  });

  it("maps a bare string rejection too", () => {
    expect(describeProviderKeyError("api_key_too_short", "fallback")).toMatch(
      /too short/,
    );
  });

  it("passes a non-code message through unchanged", () => {
    // Proxy timeouts and network failures are not reason codes and must not be
    // rewritten into a provider-shaped sentence.
    expect(describeProviderKeyError(new Error("Failed to fetch"), "fb")).toBe(
      "Failed to fetch",
    );
  });

  it("uses the fallback only when the throw carried no message", () => {
    expect(describeProviderKeyError(new Error(""), "fb")).toBe("fb");
    expect(describeProviderKeyError(undefined, "fb")).toBe("fb");
    expect(describeProviderKeyError(null, "fb")).toBe("fb");
    expect(describeProviderKeyError({ detail: "nope" }, "fb")).toBe("fb");
    expect(describeProviderKeyError("", "fb")).toBe("fb");
  });
});
