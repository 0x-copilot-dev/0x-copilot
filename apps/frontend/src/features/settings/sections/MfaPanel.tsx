/**
 * Settings → Profile → Sign-in & security: TOTP enrollment panel
 * (PR 8.2 Phase 2).
 *
 * State machine:
 *   list → enroll-pending (QR + recovery codes shown once) → confirm
 *   list → confirmed (back to list with the new factor enabled)
 *   list → disable (DELETE on a factor; removes the row)
 *
 * The panel is intentionally self-contained — it does its own fetch and
 * caches in-memory. Reusing the global preferences hook would couple
 * MFA's lifecycle to the settings shell's hydration, which is wrong:
 * MFA reads should refetch on every mount (factor state can change
 * server-side via SCIM provisioning).
 */

import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@enterprise-search/design-system";
import type {
  MfaFactorSummary,
  TotpEnrollResponse,
} from "@enterprise-search/api-types";
import { QRCodeSVG } from "qrcode.react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  confirmTotpFactor,
  disableMfaFactor,
  enrollTotpFactor,
  listMyMfaFactors,
  webauthnRegisterFinish,
  webauthnRegisterStart,
} from "../../../api/mfaApi";
import { decodeCreationOptions, encodeAttestation } from "./webauthnCodec";

type Phase =
  | { kind: "idle" }
  | { kind: "enrolling" }
  | { kind: "confirm"; pending: TotpEnrollResponse };

export function MfaPanel(): ReactElement {
  const [factors, setFactors] = useState<MfaFactorSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [displayName, setDisplayName] = useState("Authenticator app");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listMyMfaFactors();
      setFactors(response.factors);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load factors.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onEnroll(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const result = await enrollTotpFactor({
        display_name: displayName.trim() || "Authenticator app",
      });
      setPhase({ kind: "confirm", pending: result });
      setCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Enrollment failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm(): Promise<void> {
    if (phase.kind !== "confirm") return;
    setBusy(true);
    setError(null);
    try {
      await confirmTotpFactor({
        factor_id: phase.pending.factor_id,
        code: code.trim(),
      });
      setPhase({ kind: "idle" });
      setCode("");
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Code rejected. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onDisable(factorId: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await disableMfaFactor(factorId);
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not disable factor.",
      );
    } finally {
      setBusy(false);
    }
  }

  /**
   * Drive the WebAuthn enrollment ceremony end-to-end. The browser API
   * is gated behind a user gesture, so we run the whole thing on a
   * single click — no two-step "preview the QR" UI like TOTP needs.
   */
  async function onEnrollWebauthn(): Promise<void> {
    if (typeof window === "undefined" || !window.PublicKeyCredential) {
      setError("This browser does not support security keys (WebAuthn).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const start = await webauthnRegisterStart({
        display_name: displayName.trim() || "Security key",
        rp_id: window.location.hostname,
        rp_name: "Enterprise Search",
        user_name: factors[0]?.display_name ?? displayName.trim() ?? "user",
        user_display_name: displayName.trim() || null,
      });
      const publicKey = decodeCreationOptions(start.options);
      const credential = (await navigator.credentials.create({
        publicKey,
      })) as PublicKeyCredential | null;
      if (credential === null) {
        throw new Error("No credential returned by the authenticator.");
      }
      await webauthnRegisterFinish({
        factor_id: start.factor_id,
        challenge_id: start.challenge_id,
        rp_id: window.location.hostname,
        expected_origin: window.location.origin,
        attestation: encodeAttestation(credential),
      });
      setPhase({ kind: "idle" });
      await refresh();
    } catch (err) {
      // ``DOMException`` covers user-cancel, timeout, security errors.
      const message =
        err instanceof DOMException || err instanceof Error
          ? err.message
          : "Security key enrollment failed.";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="me-form__mfa-head">
        <div>
          <h3 className="me-form__card-title" style={{ margin: 0 }}>
            Two-step verification
          </h3>
          <p className="settings-meta">
            Add an authenticator app (Google Authenticator, 1Password, Authy)
            for stronger sign-in.
          </p>
        </div>
        {phase.kind === "idle" && factors.length === 0 ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => setPhase({ kind: "enrolling" })}
            disabled={busy || loading}
            title="Add an authenticator app"
          >
            Add authenticator
          </Button>
        ) : null}
      </div>

      {error ? <p className="app-error">{error}</p> : null}

      {phase.kind === "idle" ? (
        <FactorList
          factors={factors}
          loading={loading}
          busy={busy}
          onAdd={() => setPhase({ kind: "enrolling" })}
          onDisable={(id) => void onDisable(id)}
        />
      ) : null}

      {phase.kind === "enrolling" ? (
        <div className="me-form__mfa-step">
          <Field
            label="Device label"
            hint="So you can tell factors apart later."
          >
            <TextInput
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="iPhone 15"
            />
          </Field>
          <div className="me-form__mfa-actions">
            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={() => void onEnroll()}
              disabled={busy}
              title="Use a TOTP authenticator app"
            >
              {busy ? "Generating…" : "Use authenticator app"}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void onEnrollWebauthn()}
              disabled={busy}
              title="Use a hardware security key (WebAuthn)"
            >
              {busy ? "Waiting for key…" : "Use a security key"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setPhase({ kind: "idle" })}
              disabled={busy}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {phase.kind === "confirm" ? (
        <ConfirmStep
          pending={phase.pending}
          code={code}
          onCode={setCode}
          onConfirm={() => void onConfirm()}
          onCancel={() => {
            setPhase({ kind: "idle" });
            setCode("");
          }}
          busy={busy}
        />
      ) : null}
    </Card>
  );
}

function FactorList({
  factors,
  loading,
  busy,
  onAdd,
  onDisable,
}: {
  factors: MfaFactorSummary[];
  loading: boolean;
  busy: boolean;
  onAdd: () => void;
  onDisable: (factorId: string) => void;
}): ReactElement {
  if (loading) {
    return <p className="settings-meta">Loading factors…</p>;
  }
  if (factors.length === 0) {
    return (
      <p className="settings-meta">
        No authenticator app set up yet.{" "}
        <button
          type="button"
          className="me-form__inline-link"
          onClick={onAdd}
          disabled={busy}
        >
          Add one
        </button>
      </p>
    );
  }
  return (
    <ul className="me-form__mfa-list">
      {factors.map((f) => (
        <li key={f.factor_id} className="me-form__mfa-row">
          <div className="me-form__mfa-meta">
            <strong>{f.display_name}</strong>
            <Badge tone={f.enabled ? "success" : "warning"}>
              {f.enabled ? "Active" : "Pending"}
            </Badge>
            <span className="settings-meta">
              {f.kind.toUpperCase()} ·{" "}
              {f.last_used_at
                ? `Last used ${new Date(f.last_used_at).toLocaleDateString()}`
                : "Never used"}
            </span>
          </div>
          <Button
            type="button"
            variant="danger"
            size="sm"
            onClick={() => onDisable(f.factor_id)}
            disabled={busy}
            title={`Remove ${f.display_name}`}
          >
            Remove
          </Button>
        </li>
      ))}
    </ul>
  );
}

function ConfirmStep({
  pending,
  code,
  onCode,
  onConfirm,
  onCancel,
  busy,
}: {
  pending: TotpEnrollResponse;
  code: string;
  onCode: (next: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
}): ReactElement {
  return (
    <div className="me-form__mfa-step">
      <p className="settings-meta">
        Scan this QR with your authenticator app, then enter the 6-digit code to
        verify.
      </p>
      <div className="me-form__mfa-confirm">
        <div className="me-form__mfa-qr">
          <QRCodeSVG value={pending.otpauth_url} size={168} includeMargin />
        </div>
        <div className="me-form__mfa-secret">
          <Field
            label="Or paste this secret manually"
            hint="If your app can't scan the QR."
          >
            <code className="me-form__mfa-secret-code">
              {pending.secret_b32}
            </code>
          </Field>
          <Field label="6-digit code">
            <TextInput
              value={code}
              onChange={(e) => onCode(e.target.value.replace(/\D/g, ""))}
              placeholder="123456"
              autoComplete="one-time-code"
              inputMode="numeric"
            />
          </Field>
        </div>
      </div>

      <details className="me-form__mfa-recovery">
        <summary>
          <strong>Recovery codes</strong> — save these now. They won't be shown
          again.
        </summary>
        <ul className="me-form__mfa-recovery-list">
          {pending.recovery_codes.map((c) => (
            <li key={c}>
              <code>{c}</code>
            </li>
          ))}
        </ul>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() =>
            void navigator.clipboard.writeText(
              pending.recovery_codes.join("\n"),
            )
          }
          title="Copy recovery codes"
        >
          Copy codes
        </Button>
      </details>

      <div className="me-form__mfa-actions">
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onConfirm}
          disabled={busy || code.length < 4}
        >
          {busy ? "Verifying…" : "Verify and enable"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
