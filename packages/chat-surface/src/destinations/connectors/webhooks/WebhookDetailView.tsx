// <WebhookDetailView /> — detail for one Webhook (sub-route of
// /connectors/webhooks).
//
// Source: connectors-prd §7.3 (webhook detail surface) + §9.2 (rotate
// flow with 14-day grace window) + §9.5 (test-fire).
//
// Two mutating callbacks; both return their canonical envelope so the
// view can render the copy-once reveal locally:
//
//   onRotate()   → WebhookRotateResponse | null  (new secret + grace)
//   onTestFire() → WebhookTestFireResponse | null
//
// `RevealOnce` is reused verbatim — no second implementation.

import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  Webhook,
  WebhookRotateResponse,
  WebhookTestFireResponse,
} from "@enterprise-search/api-types";

import { StatusPill, type StatusTone } from "../../../shell/StatusPill";
import { formatRelativeTime } from "../../../util/time";

import { RevealOnce } from "../RevealOnce";

const STATUS_TONE: Readonly<Record<Webhook["status"], StatusTone>> = {
  active: "ok",
  paused: "muted",
};

const STATUS_LABEL: Readonly<Record<Webhook["status"], string>> = {
  active: "Active",
  paused: "Paused",
};

export interface WebhookDetailViewProps {
  readonly webhook: Webhook;
  /** Clipboard port (used inside RevealOnce). */
  readonly onCopy: (text: string) => Promise<void>;
  /** Rotate the secret; resolves to the rotate envelope (carries
   *  plaintext) or `null` on host-side failure. */
  readonly onRotate?: () => Promise<WebhookRotateResponse | null>;
  /** Test-fire the webhook; resolves to the response envelope or `null`. */
  readonly onTestFire?: () => Promise<WebhookTestFireResponse | null>;
  /** Pause / resume the webhook (PATCH status). */
  readonly onPauseToggle?: () => void;
  /** Delete the webhook. */
  readonly onDelete?: () => void;
  /** Test seam for relative-time formatting. */
  readonly now?: number;
}

export function WebhookDetailView(props: WebhookDetailViewProps): ReactElement {
  const {
    webhook,
    onCopy,
    onRotate,
    onTestFire,
    onPauseToggle,
    onDelete,
    now,
  } = props;

  const [rotated, setRotated] = useState<WebhookRotateResponse | null>(null);
  const [rotating, setRotating] = useState(false);
  const [testFire, setTestFire] = useState<WebhookTestFireResponse | null>(
    null,
  );
  const [testing, setTesting] = useState(false);

  const handleRotate = useCallback(async () => {
    if (onRotate === undefined || rotating) return;
    setRotating(true);
    try {
      const response = await onRotate();
      setRotated(response);
    } finally {
      setRotating(false);
    }
  }, [onRotate, rotating]);

  const handleTestFire = useCallback(async () => {
    if (onTestFire === undefined || testing) return;
    setTesting(true);
    try {
      const response = await onTestFire();
      setTestFire(response);
    } finally {
      setTesting(false);
    }
  }, [onTestFire, testing]);

  const dismissSecret = useCallback(() => setRotated(null), []);

  const lastFireLabel =
    webhook.last_fire_at === null
      ? "Never fired"
      : `Last fire ${formatRelativeTime(webhook.last_fire_at, now)}`;
  const rotatesAtLabel =
    webhook.rotates_at === null
      ? "Manual rotation"
      : `Rotates ${formatRelativeTime(webhook.rotates_at, now)}`;

  return (
    <article
      data-testid="webhook-detail-view"
      data-webhook-id={webhook.id}
      data-status={webhook.status}
      style={containerStyle}
    >
      {/* Header --------------------------------------------------------- */}
      <header style={headerStyle}>
        <code style={urlStyle} data-testid="webhook-detail-url">
          {webhook.url}
        </code>
        <StatusPill
          status={STATUS_TONE[webhook.status]}
          label={STATUS_LABEL[webhook.status]}
        />
      </header>

      {/* Facts ----------------------------------------------------------- */}
      <dl style={factsGridStyle} data-testid="webhook-detail-facts">
        <Fact
          label="HMAC"
          value={webhook.hmac_algo}
          testId="webhook-detail-fact-hmac"
        />
        <Fact
          label="Secret strategy"
          value={webhook.secret_strategy}
          testId="webhook-detail-fact-strategy"
        />
        <Fact
          label="IP allowlist"
          value={
            webhook.ip_allowlist.length === 0
              ? "Any IP"
              : webhook.ip_allowlist.join(", ")
          }
          testId="webhook-detail-fact-ip"
        />
        <Fact
          label="Last fire"
          value={lastFireLabel}
          testId="webhook-detail-fact-last-fire"
        />
        <Fact
          label="Next rotation"
          value={rotatesAtLabel}
          testId="webhook-detail-fact-rotates"
        />
        {webhook.last_status_code !== undefined ? (
          <Fact
            label="Last HTTP"
            value={String(webhook.last_status_code)}
            testId="webhook-detail-fact-status-code"
          />
        ) : null}
      </dl>

      {/* Rotate window -------------------------------------------------- */}
      <section style={sectionStyle} data-testid="webhook-detail-rotate-section">
        <h3 style={sectionTitleStyle}>Secret</h3>
        <RevealOnce
          value={rotated?.secret_plaintext ?? null}
          maskedPlaceholder="••••••••"
          label="webhook secret"
          onCopy={onCopy}
          onDismiss={dismissSecret}
          testId="webhook-detail-secret"
        />
        {rotated !== null && rotated.grace_secret_plaintext !== null ? (
          <div data-testid="webhook-detail-grace">
            <p style={hintStyle}>
              The previous secret is still valid for the 14-day grace window so
              receivers can roll over.
            </p>
            <RevealOnce
              value={rotated.grace_secret_plaintext}
              maskedPlaceholder="••••••••"
              label="grace secret"
              onCopy={onCopy}
              onDismiss={dismissSecret}
              testId="webhook-detail-grace-secret"
            />
          </div>
        ) : null}
        <div style={actionRowStyle}>
          {onRotate !== undefined ? (
            <button
              type="button"
              onClick={handleRotate}
              disabled={rotating}
              style={secondaryButtonStyle}
              data-testid="webhook-detail-rotate"
            >
              {rotating ? "Rotating…" : "Rotate secret"}
            </button>
          ) : null}
        </div>
      </section>

      {/* Test fire ------------------------------------------------------ */}
      <section
        style={sectionStyle}
        data-testid="webhook-detail-test-fire-section"
      >
        <h3 style={sectionTitleStyle}>Test fire</h3>
        <p style={hintStyle}>
          Send a sample signed payload and surface the upstream HTTP status. No
          routine activity is recorded.
        </p>
        <div style={actionRowStyle}>
          {onTestFire !== undefined ? (
            <button
              type="button"
              onClick={handleTestFire}
              disabled={testing}
              style={secondaryButtonStyle}
              data-testid="webhook-detail-test-fire"
            >
              {testing ? "Sending…" : "Send test fire"}
            </button>
          ) : null}
        </div>
        {testFire !== null ? <TestFireResult result={testFire} /> : null}
      </section>

      {/* Lifecycle ------------------------------------------------------ */}
      <section style={sectionStyle} data-testid="webhook-detail-lifecycle">
        <h3 style={sectionTitleStyle}>Lifecycle</h3>
        <div style={actionRowStyle}>
          {onPauseToggle !== undefined ? (
            <button
              type="button"
              onClick={onPauseToggle}
              style={secondaryButtonStyle}
              data-testid="webhook-detail-pause-toggle"
            >
              {webhook.status === "active" ? "Pause" : "Resume"}
            </button>
          ) : null}
          {onDelete !== undefined ? (
            <button
              type="button"
              onClick={onDelete}
              style={dangerButtonStyle}
              data-testid="webhook-detail-delete"
            >
              Delete
            </button>
          ) : null}
        </div>
      </section>
    </article>
  );
}

// === Subcomponents ======================================================

interface TestFireResultProps {
  readonly result: WebhookTestFireResponse;
}

function TestFireResult({ result }: TestFireResultProps): ReactElement {
  const tone: StatusTone = result.response_ok
    ? "ok"
    : result.response_status === null
      ? "error"
      : "warning";
  const label = result.response_ok
    ? `Receiver accepted (HTTP ${result.response_status ?? "—"})`
    : result.response_status === null
      ? `Transport failure${result.error !== undefined ? ` (${result.error})` : ""}`
      : `Receiver rejected (HTTP ${result.response_status})`;
  return (
    <div
      data-testid="webhook-detail-test-fire-result"
      data-response-ok={result.response_ok ? "true" : "false"}
      style={testFireResultStyle}
    >
      <StatusPill status={tone} label={label} />
    </div>
  );
}

interface FactProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function Fact(props: FactProps): ReactElement {
  return (
    <div style={factStyle} data-testid={props.testId}>
      <dt style={factLabelStyle}>{props.label}</dt>
      <dd style={factValueStyle}>{props.value}</dd>
    </div>
  );
}

// === Styles ============================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  justifyContent: "space-between",
  flexWrap: "wrap",
};

const urlStyle: CSSProperties = {
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  wordBreak: "break-all",
  flex: 1,
  minWidth: 0,
};

const factsGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 10,
  margin: 0,
  padding: 0,
};

const factStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "8px 10px",
  background: "var(--color-bg-elevated, #18181b)",
  borderRadius: "var(--radius-sm, 6px)",
};

const factLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const factValueStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  margin: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  paddingTop: 8,
  borderTop: "1px solid var(--color-border, #232325)",
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "var(--color-text-muted, #b4b4b8)",
};

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const secondaryButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const dangerButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-danger, #d97777)",
  background: "transparent",
  color: "var(--color-danger, #d97777)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const testFireResultStyle: CSSProperties = {
  marginTop: 4,
  display: "flex",
};
