// <WebhookCreateWizard /> — 5-step machine that creates a Webhook.
//
// Source: connectors-prd §7.3 (wizard) + §9.4 (verification snippet) +
// §3.1 wire (`Webhook`, `WebhookCreateResponse`). Reuses
// `useStepMachine` from `../tools/onboarding` — no new step machine.
//
// Steps:
//   1. URL              — paste the receiver URL.
//   2. Secret strategy  — rotating vs static.
//   3. HMAC algo        — hmac-sha256 only (informational; future-proof).
//   4. IP allowlist     — one CIDR per line.
//   5. Test fire        — fire, reveal `secret_plaintext` once,
//                         render §9.4 verification snippet (Python)
//                         with copy button.
//
// The host owns transport: `onCreate(req)` resolves to the create
// envelope (which carries the plaintext), or `null` on failure. The
// wizard renders the copy-once reveal locally.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  WebhookCreateResponse,
  WebhookHmacAlgo,
  WebhookSecretStrategy,
} from "@0x-copilot/api-types";

import { useStepMachine } from "../../tools/onboarding/useStepMachine";

import { RevealOnce } from "../RevealOnce";

// Verbatim from connectors-prd §9.4 — the wizard renders this with a
// copy button. Any change here MUST come from the sub-PRD; the test
// pins this string byte-for-byte.
export const WEBHOOK_VERIFICATION_SNIPPET = `# Receiver-side verification (Python)
import hmac, hashlib, time
def verify(body: bytes, sig_header: str, ts_header: str, secret: bytes) -> bool:
    if abs(time.time() - int(ts_header)) > 300:
        return False
    algo, signature = sig_header.split("=", 1)
    if algo != "hmac-sha256":
        return False
    expected = hmac.new(secret, body + ts_header.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)`;

export interface WebhookCreateWizardRequest {
  readonly url: string;
  readonly secret_strategy: WebhookSecretStrategy;
  readonly hmac_algo: WebhookHmacAlgo;
  readonly ip_allowlist: ReadonlyArray<string>;
}

export interface WebhookCreateWizardProps {
  /**
   * Host transport. Receives the assembled request once the user
   * lands on the test-fire step and clicks "Create & test fire".
   * Resolves to the create envelope on success, `null` on failure.
   */
  readonly onCreate: (
    req: WebhookCreateWizardRequest,
  ) => Promise<WebhookCreateResponse | null>;
  /** Clipboard port — used by RevealOnce + the snippet copy button. */
  readonly onCopy: (text: string) => Promise<void>;
  /** Cancel — host owns the dirty-prompt confirm guard. */
  readonly onCancel?: () => void;
  /** Called once the user clicks "Done" on the final step. */
  readonly onDone?: (created: WebhookCreateResponse) => void;
}

const STEP_LABELS: ReadonlyArray<string> = [
  "URL",
  "Secret strategy",
  "HMAC algo",
  "IP allowlist",
  "Test fire",
];

function isValidUrl(value: string): boolean {
  if (value.trim().length === 0) return false;
  try {
    const u = new URL(value);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

function parseCidrLines(raw: string): ReadonlyArray<string> {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function WebhookCreateWizard(
  props: WebhookCreateWizardProps,
): ReactElement {
  const { onCreate, onCopy, onCancel, onDone } = props;

  const stepper = useStepMachine({ totalSteps: STEP_LABELS.length });

  // -- Form state ---------------------------------------------------------
  const [url, setUrl] = useState("");
  const [secretStrategy, setSecretStrategy] =
    useState<WebhookSecretStrategy>("rotating");
  const [hmacAlgo] = useState<WebhookHmacAlgo>("hmac-sha256");
  const [ipText, setIpText] = useState("");

  // -- Test-fire state ----------------------------------------------------
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<WebhookCreateResponse | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const ipAllowlist = useMemo(() => parseCidrLines(ipText), [ipText]);

  const canAdvance = useMemo(() => {
    switch (stepper.currentStep) {
      case 0:
        return isValidUrl(url);
      case 1:
        return secretStrategy === "rotating" || secretStrategy === "static";
      case 2:
        return hmacAlgo === "hmac-sha256";
      case 3:
        return true; // empty allowlist is allowed (= any IP)
      default:
        return false;
    }
  }, [stepper.currentStep, url, secretStrategy, hmacAlgo]);

  const handleCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const response = await onCreate({
        url,
        secret_strategy: secretStrategy,
        hmac_algo: hmacAlgo,
        ip_allowlist: ipAllowlist,
      });
      if (response === null) {
        setCreateError("Webhook creation failed. Try again.");
      } else {
        setCreated(response);
      }
    } finally {
      setCreating(false);
    }
  }, [creating, onCreate, url, secretStrategy, hmacAlgo, ipAllowlist]);

  const handleCopySnippet = useCallback(
    () => onCopy(WEBHOOK_VERIFICATION_SNIPPET),
    [onCopy],
  );

  const handleDismissSecret = useCallback(() => {
    setCreated(
      (prev) => (prev === null ? null : { ...prev, secret_plaintext: "" }), // closes the reveal window
    );
  }, []);

  const handleDone = useCallback(() => {
    if (created === null) return;
    onDone?.(created);
  }, [created, onDone]);

  return (
    <section
      aria-labelledby="webhook-wizard-heading"
      data-testid="webhook-create-wizard"
      data-current-step={stepper.currentStep}
      style={containerStyle}
    >
      <header style={headerStyle}>
        <h2 id="webhook-wizard-heading" style={titleStyle}>
          Add webhook
        </h2>
        <p style={subtitleStyle}>
          5 steps · HMAC-signed receiver for routine fires.
        </p>
      </header>

      <nav aria-label="Webhook wizard steps" style={stepperStyle}>
        <ol style={stepperListStyle}>
          {STEP_LABELS.map((label, idx) => {
            const isCurrent = idx === stepper.currentStep;
            const done = idx < stepper.currentStep;
            return (
              <li
                key={label}
                style={stepperItemStyle(isCurrent, done)}
                aria-current={isCurrent ? "step" : undefined}
                data-testid={`webhook-wizard-step-${idx}`}
              >
                <span
                  aria-hidden="true"
                  style={stepperIndexStyle(isCurrent, done)}
                >
                  {idx + 1}
                </span>
                <span>{label}</span>
              </li>
            );
          })}
        </ol>
      </nav>

      <div data-testid="webhook-wizard-body" style={bodyStyle}>
        {stepper.currentStep === 0 ? (
          <UrlStep value={url} onChange={setUrl} />
        ) : null}
        {stepper.currentStep === 1 ? (
          <SecretStrategyStep
            value={secretStrategy}
            onChange={setSecretStrategy}
          />
        ) : null}
        {stepper.currentStep === 2 ? <HmacAlgoStep value={hmacAlgo} /> : null}
        {stepper.currentStep === 3 ? (
          <IpAllowlistStep value={ipText} onChange={setIpText} />
        ) : null}
        {stepper.currentStep === 4 ? (
          <TestFireStep
            url={url}
            created={created}
            creating={creating}
            createError={createError}
            onCreate={handleCreate}
            onCopy={onCopy}
            onCopySnippet={handleCopySnippet}
            onDismissSecret={handleDismissSecret}
          />
        ) : null}
      </div>

      <footer style={footerStyle}>
        <div>
          {onCancel !== undefined && created === null ? (
            <button
              type="button"
              onClick={onCancel}
              style={ghostButtonStyle}
              data-testid="webhook-wizard-cancel"
            >
              Cancel
            </button>
          ) : null}
        </div>
        <div style={footerRightStyle}>
          {!stepper.isFirst && created === null ? (
            <button
              type="button"
              onClick={stepper.back}
              style={secondaryButtonStyle}
              data-testid="webhook-wizard-back"
            >
              Back
            </button>
          ) : null}
          {!stepper.isLast ? (
            <button
              type="button"
              onClick={stepper.next}
              disabled={!canAdvance}
              style={primaryButtonStyle}
              data-testid="webhook-wizard-next"
            >
              Next
            </button>
          ) : created === null ? (
            <button
              type="button"
              onClick={handleCreate}
              disabled={creating}
              style={primaryButtonStyle}
              data-testid="webhook-wizard-create"
            >
              {creating ? "Creating…" : "Create & test fire"}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleDone}
              style={primaryButtonStyle}
              data-testid="webhook-wizard-done"
            >
              Done
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}

// === Steps ==============================================================

interface UrlStepProps {
  readonly value: string;
  readonly onChange: (next: string) => void;
}

function UrlStep({ value, onChange }: UrlStepProps): ReactElement {
  return (
    <div data-testid="webhook-wizard-url-step">
      <label style={labelStyle}>
        <span style={labelTextStyle}>Receiver URL</span>
        <input
          type="url"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://example.com/atlas/webhook"
          style={inputStyle}
          data-testid="webhook-wizard-url-input"
          autoFocus
        />
      </label>
      <p style={hintStyle}>
        We send signed POSTs here. HTTPS is required in production.
      </p>
    </div>
  );
}

interface SecretStrategyStepProps {
  readonly value: WebhookSecretStrategy;
  readonly onChange: (next: WebhookSecretStrategy) => void;
}

function SecretStrategyStep({
  value,
  onChange,
}: SecretStrategyStepProps): ReactElement {
  return (
    <fieldset
      style={fieldsetStyle}
      data-testid="webhook-wizard-secret-strategy"
    >
      <legend style={legendStyle}>Secret strategy</legend>
      <RadioOption
        name="secret-strategy"
        value="rotating"
        currentValue={value}
        label="Rotating (90-day cycle with 14-day grace)"
        description="Atlas rotates the secret automatically. The previous secret remains valid during the grace window so receivers can roll without a hard cutover."
        onChange={onChange}
      />
      <RadioOption
        name="secret-strategy"
        value="static"
        currentValue={value}
        label="Static (manual rotation only)"
        description="The secret never expires. Rotate manually from the webhook detail page."
        onChange={onChange}
      />
    </fieldset>
  );
}

interface HmacAlgoStepProps {
  readonly value: WebhookHmacAlgo;
}

function HmacAlgoStep({ value }: HmacAlgoStepProps): ReactElement {
  return (
    <div data-testid="webhook-wizard-hmac-step">
      <p style={hintStyle}>
        Atlas signs the payload with{" "}
        <code style={codeInlineStyle} data-testid="webhook-wizard-hmac-algo">
          {value}
        </code>
        . The signature header is{" "}
        <code style={codeInlineStyle}>X-Atlas-Routine-Signature</code>; the
        timestamp header is{" "}
        <code style={codeInlineStyle}>X-Atlas-Routine-Timestamp</code>. Only one
        algorithm is supported in v1.
      </p>
    </div>
  );
}

interface IpAllowlistStepProps {
  readonly value: string;
  readonly onChange: (next: string) => void;
}

function IpAllowlistStep({
  value,
  onChange,
}: IpAllowlistStepProps): ReactElement {
  return (
    <div data-testid="webhook-wizard-ip-step">
      <label style={labelStyle}>
        <span style={labelTextStyle}>IP allowlist (CIDR per line)</span>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={"10.0.0.0/8\n203.0.113.5/32"}
          rows={5}
          style={textareaStyle}
          data-testid="webhook-wizard-ip-textarea"
        />
      </label>
      <p style={hintStyle}>
        Leave empty to allow any IP. Atlas refuses to send to URLs whose DNS
        resolution falls outside the listed CIDRs.
      </p>
    </div>
  );
}

interface TestFireStepProps {
  readonly url: string;
  readonly created: WebhookCreateResponse | null;
  readonly creating: boolean;
  readonly createError: string | null;
  readonly onCreate: () => void;
  readonly onCopy: (text: string) => Promise<void>;
  readonly onCopySnippet: () => Promise<void>;
  readonly onDismissSecret: () => void;
}

function TestFireStep(props: TestFireStepProps): ReactElement {
  const {
    url,
    created,
    creating,
    createError,
    onCopy,
    onCopySnippet,
    onDismissSecret,
  } = props;

  return (
    <div data-testid="webhook-wizard-test-fire-step">
      <p style={hintStyle}>
        We&apos;ll create the webhook at{" "}
        <code style={codeInlineStyle}>{url}</code> and reveal the signing secret{" "}
        <strong>exactly once</strong>. Copy it now — Atlas never shows it again.
      </p>

      {createError !== null ? (
        <p role="alert" style={errorStyle} data-testid="webhook-wizard-error">
          {createError}
        </p>
      ) : null}

      {created !== null ? (
        <>
          <RevealOnce
            value={
              created.secret_plaintext.length > 0
                ? created.secret_plaintext
                : null
            }
            maskedPlaceholder="wh_••••••••"
            label="webhook secret"
            onCopy={onCopy}
            onDismiss={onDismissSecret}
            testId="webhook-wizard-secret"
          />
          <section style={snippetSectionStyle}>
            <div style={snippetHeaderStyle}>
              <span style={snippetLabelStyle}>Receiver verification</span>
              <button
                type="button"
                onClick={() => {
                  void onCopySnippet();
                }}
                style={ghostButtonStyle}
                data-testid="webhook-wizard-snippet-copy"
                aria-label="Copy verification snippet"
              >
                Copy
              </button>
            </div>
            <pre
              style={snippetStyle}
              data-testid="webhook-wizard-verification-snippet"
            >
              {WEBHOOK_VERIFICATION_SNIPPET}
            </pre>
          </section>
        </>
      ) : (
        <p style={hintStyle} data-testid="webhook-wizard-pre-create-hint">
          {creating
            ? "Creating webhook…"
            : 'Click "Create & test fire" to reveal the secret.'}
        </p>
      )}
    </div>
  );
}

interface RadioOptionProps<T extends string> {
  readonly name: string;
  readonly value: T;
  readonly currentValue: T;
  readonly label: string;
  readonly description: string;
  readonly onChange: (next: T) => void;
}

function RadioOption<T extends string>(
  props: RadioOptionProps<T>,
): ReactElement {
  const { name, value, currentValue, label, description, onChange } = props;
  const checked = value === currentValue;
  return (
    <label
      style={radioLabelStyle}
      data-testid={`webhook-wizard-radio-${value}`}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
      />
      <span style={radioTextStyle}>
        <span style={radioLabelTextStyle}>{label}</span>
        <span style={radioDescriptionStyle}>{description}</span>
      </span>
    </label>
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
  flexDirection: "column",
  gap: 4,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 18px)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const stepperStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border, #232325)",
  borderBottom: "1px solid var(--color-border, #232325)",
  padding: "10px 0",
};

const stepperListStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const stepperItemStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 10px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: isCurrent ? 600 : 400,
  color:
    isCurrent || done
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
  border: `1px solid ${
    isCurrent ? "var(--color-accent, #d97757)" : "var(--color-border, #232325)"
  }`,
  borderRadius: 999,
  background: isCurrent ? "var(--color-bg-elevated, #18181b)" : "transparent",
});

const stepperIndexStyle = (
  isCurrent: boolean,
  done: boolean,
): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  borderRadius: 999,
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  background:
    isCurrent || done
      ? "var(--color-accent, #d97757)"
      : "var(--color-bg-elevated, #18181b)",
  color:
    isCurrent || done
      ? "var(--color-accent-contrast, #1a0f0a)"
      : "var(--color-text-muted, #b4b4b8)",
});

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: "4px 0",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  paddingTop: 10,
  borderTop: "1px solid var(--color-border, #232325)",
};

const footerRightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const labelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const inputStyle: CSSProperties = {
  height: 34,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-sm, 13px)",
};

const textareaStyle: CSSProperties = {
  padding: 10,
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-sm, 13px)",
  resize: "vertical",
};

const fieldsetStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  border: "none",
  padding: 0,
  margin: 0,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  marginBottom: 4,
};

const radioLabelStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 10,
  cursor: "pointer",
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
};

const radioTextStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const radioLabelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const radioDescriptionStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const hintStyle: CSSProperties = {
  margin: "8px 0 0 0",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const errorStyle: CSSProperties = {
  margin: "8px 0",
  padding: "8px 10px",
  background: "var(--color-danger-bg, #321a1a)",
  border: "1px solid var(--color-danger, #d97777)",
  borderRadius: "var(--radius-sm, 6px)",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const codeInlineStyle: CSSProperties = {
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-xs, 12px)",
  padding: "1px 4px",
  borderRadius: 3,
  background: "var(--color-surface-muted, #222224)",
  color: "var(--color-text, #ededee)",
};

const snippetSectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginTop: 12,
  padding: 10,
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-bg-elevated, #18181b)",
  border: "1px solid var(--color-border, #232325)",
};

const snippetHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const snippetLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const snippetStyle: CSSProperties = {
  margin: 0,
  padding: 10,
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-surface-muted, #222224)",
  color: "var(--color-text, #ededee)",
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.55,
  whiteSpace: "pre",
  overflowX: "auto",
};

const primaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const ghostButtonStyle: CSSProperties = {
  height: 28,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid transparent",
  background: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};
