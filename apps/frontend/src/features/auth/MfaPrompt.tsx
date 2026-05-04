/**
 * MFA prompt (A9): TOTP code input + WebAuthn ceremony + recovery
 * fallback. Renders after a login response with ``requires_mfa=true``.
 *
 * The component owns the verify HTTP round-trip; on success it calls
 * ``AuthContext.completeMfa()`` which refreshes the canonical session.
 */

import type { FormEvent, ReactElement } from "react";
import { useEffect, useState } from "react";

import {
  consumeRecoveryCode,
  issueMfaChallenge,
  verifyMfaChallenge,
} from "../../api/authApi";
import { useAuth } from "./AuthContext";

type Step = "choose" | "totp" | "webauthn" | "recovery";

export interface MfaPromptProps {
  /** RP ID for WebAuthn (production: same as the navigation hostname).
   * Single-tenant deploys hardcode at build time; SaaS reads from
   * ``window.location.hostname``. */
  rpId?: string;
  onComplete?(): void;
}

export function MfaPrompt({ rpId, onComplete }: MfaPromptProps): ReactElement {
  const auth = useAuth();
  const [step, setStep] = useState<Step>("choose");
  const [totpCode, setTotpCode] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Forward the completion signal to the parent once AuthContext flips.
  useEffect(() => {
    if (auth.status === "authenticated" && onComplete) {
      onComplete();
    }
  }, [auth.status, onComplete]);

  if (auth.status !== "mfa_pending" || !auth.mfaPending) {
    // The parent should not render this when MFA is not pending; if it
    // does we render an idle placeholder rather than crash.
    return (
      <main className="auth-mfa" data-testid="mfa-prompt-idle">
        Waiting for login…
      </main>
    );
  }

  const submitTotp = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const challenge = await issueMfaChallenge({ kind: "totp" });
      await verifyMfaChallenge({
        challenge_id: challenge.challenge_id,
        code: totpCode,
      });
      await auth.completeMfa();
    } catch (err) {
      setError(err instanceof Error ? err.message : "verify failed");
    } finally {
      setSubmitting(false);
    }
  };

  const submitRecovery = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await consumeRecoveryCode(recoveryCode);
      await auth.completeMfa();
    } catch (err) {
      setError(err instanceof Error ? err.message : "recovery failed");
    } finally {
      setSubmitting(false);
    }
  };

  const startWebAuthn = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const challenge = await issueMfaChallenge({ kind: "webauthn" });
      if (!challenge.webauthn_options) {
        throw new Error("backend returned no WebAuthn options");
      }
      // The backend already returns the publicKey options as JSON; the
      // navigator credential API needs the binary fields decoded. We
      // delegate that to an inline helper to avoid pulling in
      // @simplewebauthn/browser as a build-time dep.
      const decoded = _decodeWebAuthnAssertionOptions(
        challenge.webauthn_options as Record<string, unknown>,
      );
      const credential = await navigator.credentials.get({
        publicKey: decoded as unknown as PublicKeyCredentialRequestOptions,
      });
      if (!(credential instanceof PublicKeyCredential)) {
        throw new Error("WebAuthn cancelled");
      }
      await verifyMfaChallenge({
        challenge_id: challenge.challenge_id,
        assertion: _serializeWebAuthnAssertion(credential),
        expected_origin: window.location.origin,
      });
      await auth.completeMfa();
    } catch (err) {
      setError(err instanceof Error ? err.message : "WebAuthn failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-mfa" data-testid="mfa-prompt">
      <h1>Two-factor required</h1>
      {step === "choose" && (
        <section className="auth-mfa__choose">
          <button
            type="button"
            onClick={() => setStep("totp")}
            data-testid="mfa-choose-totp"
          >
            Use authenticator app code
          </button>
          <button
            type="button"
            onClick={() => {
              setStep("webauthn");
              void startWebAuthn();
            }}
            data-testid="mfa-choose-webauthn"
          >
            Use security key
          </button>
          <button
            type="button"
            onClick={() => setStep("recovery")}
            data-testid="mfa-choose-recovery"
          >
            Use recovery code
          </button>
        </section>
      )}

      {step === "totp" && (
        <form onSubmit={submitTotp} data-testid="mfa-totp-form">
          <label>
            <span>6-digit code</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              required
              data-testid="mfa-totp-input"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || totpCode.length === 0}
            data-testid="mfa-totp-submit"
          >
            Verify
          </button>
        </form>
      )}

      {step === "webauthn" && (
        <p data-testid="mfa-webauthn-status">
          {submitting
            ? "Tap your security key…"
            : (error ?? "WebAuthn complete")}
        </p>
      )}

      {step === "recovery" && (
        <form onSubmit={submitRecovery} data-testid="mfa-recovery-form">
          <label>
            <span>Recovery code</span>
            <input
              type="text"
              autoComplete="off"
              value={recoveryCode}
              onChange={(e) => setRecoveryCode(e.target.value)}
              required
              data-testid="mfa-recovery-input"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || recoveryCode.length === 0}
            data-testid="mfa-recovery-submit"
          >
            Verify
          </button>
        </form>
      )}

      {rpId && (
        <p className="auth-mfa__rp-id-hint" hidden>
          rp_id={rpId}
        </p>
      )}

      {error && step !== "choose" && (
        <p className="auth-mfa__error" role="alert" data-testid="mfa-error">
          {error}
        </p>
      )}
    </main>
  );
}

function _decodeWebAuthnAssertionOptions(
  options: Record<string, unknown>,
): Record<string, unknown> {
  // py_webauthn returns base64url-encoded ``challenge``,
  // ``allowCredentials[].id`` etc. Decode to ``ArrayBuffer`` for the
  // navigator credential API. Anything we don't recognise is passed
  // through unchanged so the SDK can extend without a frontend bump.
  const decoded: Record<string, unknown> = { ...options };
  if (typeof decoded.challenge === "string") {
    decoded.challenge = _b64urlToBuffer(decoded.challenge as string);
  }
  if (Array.isArray(decoded.allowCredentials)) {
    decoded.allowCredentials = (
      decoded.allowCredentials as Array<{ id: string; type?: string }>
    ).map((descriptor) => ({
      ...descriptor,
      id: _b64urlToBuffer(descriptor.id),
      type: descriptor.type ?? "public-key",
    }));
  }
  return decoded;
}

function _serializeWebAuthnAssertion(
  credential: PublicKeyCredential,
): Record<string, unknown> {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: _bufferToB64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: _bufferToB64url(response.clientDataJSON),
      authenticatorData: _bufferToB64url(response.authenticatorData),
      signature: _bufferToB64url(response.signature),
      userHandle: response.userHandle
        ? _bufferToB64url(response.userHandle)
        : null,
    },
  };
}

function _b64urlToBuffer(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base64);
  const buffer = new ArrayBuffer(binary.length);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i += 1) {
    view[i] = binary.charCodeAt(i);
  }
  return buffer;
}

function _bufferToB64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
