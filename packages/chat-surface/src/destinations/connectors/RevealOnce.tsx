// RevealOnce — copy-once-then-redact reveal primitive.
//
// Source: connectors-prd §7.3 ("Copy-once-reveal pattern is lifted from
// Routines (P5-B3) — same `RevealOnce` component") + routines-prd §9.7
// Q6. The Routines P5-B3 implementation (`RoutineDetail.tsx` →
// `WebhookTriggerCard`) inlined this behavior; this file extracts the
// pure primitive so Connectors (P11-B) and any future destination can
// reuse it without duplicating the secret-window state machine.
//
// Semantics:
//
//   * `value` is a one-shot plaintext payload (typically a webhook
//     secret returned from create/rotate). `null` = no reveal window
//     active; the component renders a stable masked placeholder.
//   * `onDismiss` — called when the user clicks "I've copied it",
//     which the host wires to drop the plaintext from state. Once the
//     host clears `value`, the masked placeholder returns.
//   * The component owns NO timer: the parent decides when the reveal
//     window closes (typically: on dismiss, on tab change, on
//     unmount). Time-based auto-redact is a destination-level concern.
//
// Reused props pattern (DRY): same `onCopy` shape as
// `WebhookTriggerCard` in Routines — a `(text) => Promise<void>` port
// so the host can pick its own clipboard adapter (web vs. desktop) and
// surface clipboard errors as it sees fit.

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

export interface RevealOnceProps {
  /** Plaintext to reveal. `null` = no window active; render masked
   *  placeholder + helper hint. */
  readonly value: string | null;
  /** Stable masked placeholder shown when no window is active (e.g.
   *  `"••••••••"` or `"wh_••••abcd"`). */
  readonly maskedPlaceholder: string;
  /** Accessible label for the value (e.g. "webhook secret"). Used on
   *  the copy button's `aria-label`. */
  readonly label: string;
  /** Clipboard port. Returns a promise so hosts can surface failure /
   *  permission prompts. */
  readonly onCopy: (text: string) => Promise<void>;
  /** Called when the user clicks "I've copied it". Host MUST drop the
   *  plaintext from state in response. */
  readonly onDismiss?: () => void;
  /** Optional test-id namespace. Defaults to `"reveal-once"`. */
  readonly testId?: string;
}

export function RevealOnce({
  value,
  maskedPlaceholder,
  label,
  onCopy,
  onDismiss,
  testId = "reveal-once",
}: RevealOnceProps): ReactElement {
  // "Copied" hint state, scoped to the current reveal window. Resets on
  // window change (value identity changes) so the next rotation starts
  // clean.
  const [copied, setCopied] = useState(false);
  const previousValueRef = useRef<string | null>(value);
  useEffect(() => {
    if (previousValueRef.current !== value) {
      setCopied(false);
      previousValueRef.current = value;
    }
  }, [value]);

  const isActive = value !== null;

  const handleCopy = async (): Promise<void> => {
    if (value === null) return;
    await onCopy(value);
    setCopied(true);
  };

  return (
    <div
      style={rootStyle}
      data-testid={testId}
      data-reveal-active={isActive ? "true" : "false"}
    >
      <div style={valueRowStyle}>
        <code style={codeStyle}>
          {isActive ? (
            <span data-testid={`${testId}-clear`}>{value}</span>
          ) : (
            <span data-testid={`${testId}-masked`}>{maskedPlaceholder}</span>
          )}
        </code>
        {isActive ? (
          <button
            type="button"
            onClick={handleCopy}
            style={copyButtonStyle}
            data-testid={`${testId}-copy`}
            aria-label={`Copy ${label}`}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        ) : null}
      </div>
      {isActive ? (
        <div role="alert" style={bannerStyle} data-testid={`${testId}-banner`}>
          <strong>Copy the {label} now.</strong> It will not be shown again.{" "}
          {onDismiss !== undefined ? (
            <button
              type="button"
              onClick={onDismiss}
              style={dismissButtonStyle}
              data-testid={`${testId}-dismiss`}
            >
              I've copied it
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// === Styles ============================================================

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const valueRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
};

const codeStyle: CSSProperties = {
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-sm, 13px)",
  padding: "4px 8px",
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-surface-muted, #222224)",
  color: "var(--color-text, #ededee)",
  wordBreak: "break-all",
};

const copyButtonStyle: CSSProperties = {
  height: 26,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};

const bannerStyle: CSSProperties = {
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-warning, #d9a857)",
  background: "var(--color-warning-bg, #322615)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-xs, 12px)",
};

const dismissButtonStyle: CSSProperties = {
  marginLeft: 6,
  height: 22,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  cursor: "pointer",
};
