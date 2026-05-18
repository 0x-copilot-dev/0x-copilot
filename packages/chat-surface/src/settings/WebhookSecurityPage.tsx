// <WebhookSecurityPage /> — Settings → Workspace security.
//
// Source: team-memory-cmdk-prd.md §7.4 + §U-S3 + api-types
// `WebhookSecurityDefaults` / `UpdateWebhookSecurityDefaultsRequest`.
// Admin-only. The HMAC algorithm name is owned by the backend (signer)
// and surfaced to the FE via the `hmacAlgo` prop — we do NOT hardcode
// "hmac-sha256" in this file.
//
// Pure presentation: receives the current `WebhookSecurityDefaults`,
// renders three toggles + a max-secret-age slider, and submits a
// diff-only PATCH via `onSave`. `max_secret_age_days` clamps to the
// allowed values (30 / 60 / 90 / 180 / 365); the host renders the page
// only when `isAdmin === true` — when `false` this component intentionally
// renders nothing (no admin chrome leak).

import {
  useCallback,
  useEffect,
  useId,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type {
  UpdateWebhookSecurityDefaultsRequest,
  WebhookSecurityDefaults,
} from "@enterprise-search/api-types";

import { PageHeader } from "../shell/PageHeader";

// ---------------------------------------------------------------------------
// Allowed rotation windows. Sub-PRD §U-S3 — the slider is restricted to
// these five values (plus the legacy `0` "never expire" which the wire
// allows but the slider does not surface).
// ---------------------------------------------------------------------------

export const MAX_SECRET_AGE_DAY_VALUES: ReadonlyArray<number> = [
  30, 60, 90, 180, 365,
];

/**
 * Snaps `requested` to the nearest allowed value in
 * `MAX_SECRET_AGE_DAY_VALUES`. Out-of-range requests clamp to the
 * nearest endpoint.
 */
export function clampMaxSecretAgeDays(requested: number): number {
  let best = MAX_SECRET_AGE_DAY_VALUES[0];
  let bestDelta = Math.abs(requested - best);
  for (const value of MAX_SECRET_AGE_DAY_VALUES) {
    const delta = Math.abs(requested - value);
    if (delta < bestDelta) {
      best = value;
      bestDelta = delta;
    }
  }
  return best;
}

export interface WebhookSecurityPageProps {
  readonly defaults: WebhookSecurityDefaults;
  readonly isAdmin: boolean;
  readonly onSave: (patch: UpdateWebhookSecurityDefaultsRequest) => void;
  /**
   * HMAC algorithm name (e.g. `hmac-sha256`) — owned by the backend
   * `webhooks/signer.py`. Rendered as informational text so admins know
   * what signature scheme will be used; defaults to "hmac-sha256" only
   * if the host doesn't pass one (preserves SP-1 contract — we don't
   * silently fall through to an empty string).
   */
  readonly hmacAlgo?: string;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const fieldsetStyle: CSSProperties = {
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  padding: "0 6px",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
};

const helpStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const saveBarStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
};

const saveButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const sliderRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function WebhookSecurityPage({
  defaults,
  isAdmin,
  onSave,
  hmacAlgo,
}: WebhookSecurityPageProps): ReactElement | null {
  if (!isAdmin) return null;

  const reactId = useId();
  const algoLabel = hmacAlgo ?? "hmac-sha256";

  const [hmacOn, setHmacOn] = useState<boolean>(defaults.default_hmac_on);
  const [requireIp, setRequireIp] = useState<boolean>(
    defaults.require_ip_allowlist,
  );
  const [age, setAge] = useState<number>(
    clampMaxSecretAgeDays(defaults.max_secret_age_days),
  );

  useEffect(() => {
    setHmacOn(defaults.default_hmac_on);
    setRequireIp(defaults.require_ip_allowlist);
    setAge(clampMaxSecretAgeDays(defaults.max_secret_age_days));
  }, [defaults]);

  const handleHmac = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setHmacOn(e.target.checked);
  }, []);

  const handleRequireIp = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setRequireIp(e.target.checked);
  }, []);

  const handleAge = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const raw = Number(e.target.value);
    setAge(clampMaxSecretAgeDays(Number.isFinite(raw) ? raw : 0));
  }, []);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const patch: UpdateWebhookSecurityDefaultsRequest = {};
      if (hmacOn !== defaults.default_hmac_on) {
        (patch as { default_hmac_on?: boolean }).default_hmac_on = hmacOn;
      }
      if (requireIp !== defaults.require_ip_allowlist) {
        (patch as { require_ip_allowlist?: boolean }).require_ip_allowlist =
          requireIp;
      }
      const clampedAge = clampMaxSecretAgeDays(age);
      if (clampedAge !== clampMaxSecretAgeDays(defaults.max_secret_age_days)) {
        (patch as { max_secret_age_days?: number }).max_secret_age_days =
          clampedAge;
      }
      if (
        patch.default_hmac_on === undefined &&
        patch.require_ip_allowlist === undefined &&
        patch.max_secret_age_days === undefined
      ) {
        return;
      }
      onSave(patch);
    },
    [hmacOn, requireIp, age, defaults, onSave],
  );

  const hmacId = `${reactId}-hmac`;
  const ipId = `${reactId}-ip`;
  const ageId = `${reactId}-age`;
  const ageIndex = MAX_SECRET_AGE_DAY_VALUES.indexOf(age);

  return (
    <div style={pageStyle} data-testid="webhook-security-page">
      <PageHeader
        title="Workspace security"
        subtitle="Defaults applied to every webhook created in this workspace."
      />
      <form style={formStyle} onSubmit={handleSubmit}>
        <fieldset style={fieldsetStyle}>
          <legend style={legendStyle}>Webhook signing</legend>
          <div style={rowStyle}>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label htmlFor={hmacId} style={labelStyle}>
                Sign every webhook by default
              </label>
              <span style={helpStyle}>
                New webhooks default to HMAC ({algoLabel}).
              </span>
            </div>
            <input
              id={hmacId}
              type="checkbox"
              checked={hmacOn}
              onChange={handleHmac}
              data-testid="webhook-security-hmac"
            />
          </div>
          <div style={rowStyle}>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label htmlFor={ipId} style={labelStyle}>
                Require IP allowlist on new webhooks
              </label>
              <span style={helpStyle}>
                Webhook create requests without an IP allowlist are rejected.
              </span>
            </div>
            <input
              id={ipId}
              type="checkbox"
              checked={requireIp}
              onChange={handleRequireIp}
              data-testid="webhook-security-require-ip"
            />
          </div>
        </fieldset>
        <fieldset style={fieldsetStyle}>
          <legend style={legendStyle}>Secret rotation</legend>
          <div style={sliderRowStyle}>
            <label htmlFor={ageId} style={labelStyle}>
              Max secret age:&nbsp;
              <strong data-testid="webhook-security-age-value">
                {age} days
              </strong>
            </label>
            <input
              id={ageId}
              type="range"
              min={0}
              max={MAX_SECRET_AGE_DAY_VALUES.length - 1}
              step={1}
              value={ageIndex >= 0 ? ageIndex : 0}
              onChange={(e) => {
                const idx = Number(e.target.value);
                const next =
                  MAX_SECRET_AGE_DAY_VALUES[idx] ??
                  MAX_SECRET_AGE_DAY_VALUES[0];
                setAge(next);
              }}
              aria-valuetext={`${age} days`}
              data-testid="webhook-security-age-slider"
            />
            <input
              type="number"
              value={age}
              onChange={handleAge}
              aria-label="Max secret age (days)"
              data-testid="webhook-security-age-number"
              style={{ width: 80 }}
            />
          </div>
          <span style={helpStyle}>
            Secrets older than this trigger a rotation warning. Allowed values:{" "}
            {MAX_SECRET_AGE_DAY_VALUES.join(" / ")} days.
          </span>
        </fieldset>
        <div style={saveBarStyle}>
          <button
            type="submit"
            style={saveButtonStyle}
            data-testid="webhook-security-save"
          >
            Save changes
          </button>
        </div>
      </form>
    </div>
  );
}
