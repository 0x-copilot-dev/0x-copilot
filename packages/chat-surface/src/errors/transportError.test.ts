import { describe, expect, it } from "vitest";

import { parseTransportError } from "./transportError";

// The real missing-provider-key body the facade returns (HTTP 400): the
// ai-backend emits a FLAT { code, safe_message, ... } and the facade re-wraps it
// under `detail` via HTTPException.
const FACADE_ENVELOPE = JSON.stringify({
  detail: {
    code: "configuration_error",
    safe_message:
      "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
    retryable: false,
    correlation_id: "935a40d5ff6943e09ce5bf9f908c0fcc",
    details: {},
  },
});

describe("parseTransportError", () => {
  it("extracts safe_message/code/correlation_id from a desktop (Electron-prefixed) envelope", () => {
    // Exactly what the user saw: the facade JSON wrapped by Electron's
    // "Error invoking remote method 'transport.request': Error: {json}".
    const err = new Error(
      `Error invoking remote method 'transport.request': Error: ${FACADE_ENVELOPE}`,
    );
    const parsed = parseTransportError(err);
    expect(parsed.safeMessage).toBe(
      "Missing API key for model provider 'openai'. Add one in Settings -> Provider keys.",
    );
    expect(parsed.code).toBe("configuration_error");
    expect(parsed.correlationId).toBe("935a40d5ff6943e09ce5bf9f908c0fcc");
    // The raw envelope is preserved for the demoted "Show details" affordance.
    expect(parsed.raw).toContain("Error invoking remote method");
  });

  it("extracts the same fields from a web (bare-JSON) facade envelope", () => {
    const parsed = parseTransportError(new Error(FACADE_ENVELOPE));
    expect(parsed.safeMessage).toContain("Missing API key for model provider");
    expect(parsed.code).toBe("configuration_error");
    expect(parsed.correlationId).toBe("935a40d5ff6943e09ce5bf9f908c0fcc");
  });

  it("tolerates the flat ai-backend body (no `detail` wrapper)", () => {
    const flat = new Error(
      JSON.stringify({
        code: "configuration_error",
        safe_message: "Missing API key for model provider 'anthropic'.",
        correlation_id: "abc",
      }),
    );
    const parsed = parseTransportError(flat);
    expect(parsed.safeMessage).toBe(
      "Missing API key for model provider 'anthropic'.",
    );
    expect(parsed.code).toBe("configuration_error");
    expect(parsed.correlationId).toBe("abc");
  });

  it("reads a plain string `detail` as the message", () => {
    const parsed = parseTransportError(new Error('{"detail":"Not found"}'));
    expect(parsed.safeMessage).toBe("Not found");
    expect(parsed.code).toBeUndefined();
  });

  it("returns only raw for a plain (non-JSON) error message", () => {
    const parsed = parseTransportError(new Error("network offline"));
    expect(parsed.safeMessage).toBeUndefined();
    expect(parsed.code).toBeUndefined();
    expect(parsed.raw).toBe("network offline");
  });

  it("is safe on non-error inputs", () => {
    expect(parseTransportError(undefined).raw).toBe("");
    expect(parseTransportError(null).raw).toBe("");
    expect(parseTransportError("boom").raw).toBe("boom");
  });
});
