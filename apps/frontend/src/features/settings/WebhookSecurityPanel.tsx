// WebhookSecurityPanel — Phase 12 admin webhook-security defaults
// (sub-PRD §7.4 / §U-S3 — Routines §9.7 Q6 HMAC-of-payload UX).
// Admin-only; non-admin callers get a friendly empty state.

import {
  useEffect,
  useState,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type { WebhookSecurityDefaults } from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  getWebhookSecurityDefaults,
  patchWebhookSecurityDefaults,
} from "../../api/settingsApi";
import { errorMessage } from "../../utils/errors";

interface WebhookSecurityPanelProps {
  readonly identity: RequestIdentity;
  readonly isAdmin: boolean;
}

type State =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly value: WebhookSecurityDefaults };

export function WebhookSecurityPanel({
  identity,
  isAdmin,
}: WebhookSecurityPanelProps): ReactElement {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [pendingError, setPendingError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) {
      setState({
        kind: "error",
        message: "Webhook security defaults are admin-only.",
      });
      return;
    }
    let cancelled = false;
    getWebhookSecurityDefaults(identity)
      .then((value) => {
        if (!cancelled) setState({ kind: "ready", value });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            kind: "error",
            message: errorMessage(err, "Could not load webhook security."),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [identity, isAdmin]);

  async function patch(
    body: Parameters<typeof patchWebhookSecurityDefaults>[1],
  ): Promise<void> {
    setPendingError(null);
    if (state.kind !== "ready") return;
    try {
      const value = await patchWebhookSecurityDefaults(identity, body);
      setState({ kind: "ready", value });
    } catch (err) {
      setPendingError(errorMessage(err, "Could not save webhook security."));
    }
  }

  function onHmacChange(e: ChangeEvent<HTMLInputElement>): void {
    void patch({ default_hmac_on: e.target.checked });
  }

  function onIpAllowlistChange(e: ChangeEvent<HTMLInputElement>): void {
    void patch({ require_ip_allowlist: e.target.checked });
  }

  function onMaxAgeChange(e: ChangeEvent<HTMLInputElement>): void {
    const days = Number.parseInt(e.target.value, 10);
    if (!Number.isFinite(days) || days < 0) return;
    void patch({ max_secret_age_days: days });
  }

  return (
    <section
      aria-label="Webhook security defaults"
      data-testid="webhook-security-panel"
      data-state={state.kind}
    >
      <h2 style={{ margin: "0 0 12px 0" }}>
        Webhook security {isAdmin ? "" : " (admin-only)"}
      </h2>
      {pendingError !== null && (
        <div
          role="status"
          data-testid="webhook-security-pending-error"
          style={pendingErrorStyle}
        >
          {pendingError}
        </div>
      )}
      {state.kind === "loading" ? (
        <div data-testid="webhook-security-loading">Loading…</div>
      ) : state.kind === "error" ? (
        <div role="alert" data-testid="webhook-security-error">
          {state.message}
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          <li style={rowStyle}>
            <label style={{ display: "flex", gap: 8, fontSize: 13 }}>
              <input
                type="checkbox"
                data-testid="webhook-security-hmac-toggle"
                checked={state.value.default_hmac_on}
                disabled={!isAdmin}
                onChange={onHmacChange}
              />
              <span>Default HMAC signing on for new webhooks</span>
            </label>
          </li>
          <li style={rowStyle}>
            <label style={{ display: "flex", gap: 8, fontSize: 13 }}>
              <input
                type="checkbox"
                data-testid="webhook-security-ip-allowlist-toggle"
                checked={state.value.require_ip_allowlist}
                disabled={!isAdmin}
                onChange={onIpAllowlistChange}
              />
              <span>Require IP allowlist on webhook create</span>
            </label>
          </li>
          <li style={rowStyle}>
            <label
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                fontSize: 13,
              }}
            >
              <span>Max secret age (days)</span>
              <input
                type="number"
                data-testid="webhook-security-max-age"
                value={state.value.max_secret_age_days}
                min={0}
                step={1}
                disabled={!isAdmin}
                onChange={onMaxAgeChange}
                style={{
                  width: 80,
                  height: 28,
                  padding: "0 8px",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  background: "var(--color-surface)",
                  color: "inherit",
                }}
              />
              <span style={{ color: "var(--color-text-muted)" }}>
                (0 = never expire)
              </span>
            </label>
          </li>
        </ul>
      )}
    </section>
  );
}

const rowStyle = {
  padding: "8px 0",
  borderBottom: "1px solid var(--color-border)",
} as const;

const pendingErrorStyle = {
  marginBottom: 12,
  padding: 12,
  border: "1px solid var(--color-border-strong)",
  borderRadius: 8,
  background: "var(--color-surface)",
  fontSize: 13,
} as const;
