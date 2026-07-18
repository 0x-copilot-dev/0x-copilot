// Settings save/feedback chrome (DESIGN-SPEC §4).
//
//   <SaveBar>  `.savebar` — a dirty section surfaces "Unsaved changes" +
//              Discard / Save. role="region" (an action bar, not a status).
//   <Toast>    one-shot action feedback (export queued, key rotated, …).
//              role="status" + aria-live="polite".
//
// FR-5.7 / FR-5.11: the dirty savebar and the one-shot toast are DIFFERENT
// surfaces and MUST NOT be conflated — hence two components, two ARIA roles.
//
// Substrate-agnostic; buttons reuse the design-system <Button>. Colors resolve
// ONLY to design-system v2 tokens.

import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import { Button } from "@0x-copilot/design-system";

// ---------------------------------------------------------------------------
// SaveBar
// ---------------------------------------------------------------------------

export interface SaveBarProps {
  /** Whether the section has unsaved edits. When false the bar renders nothing. */
  readonly dirty: boolean;
  readonly onDiscard: () => void;
  readonly onSave: () => void;
  /** Dirty message. Defaults to "Unsaved changes". */
  readonly message?: ReactNode;
  readonly discardLabel?: string;
  readonly saveLabel?: string;
  /** Disables Save + shows the saving label while a write is in flight. */
  readonly saving?: boolean;
  readonly savingLabel?: string;
}

const saveBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-md)",
  padding: "10px 14px",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border-strong)",
  backgroundColor: "var(--color-surface-muted)",
  boxShadow: "var(--shadow-subtle)",
};

const saveBarMessageStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

export function SaveBar({
  dirty,
  onDiscard,
  onSave,
  message = "Unsaved changes",
  discardLabel = "Discard",
  saveLabel = "Save",
  saving = false,
  savingLabel = "Saving…",
}: SaveBarProps): ReactElement | null {
  if (!dirty) return null;
  return (
    <div
      role="region"
      aria-label="Unsaved changes"
      data-testid="settings-savebar"
      style={saveBarStyle}
    >
      <span style={saveBarMessageStyle}>{message}</span>
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-sm)",
        }}
      >
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onDiscard}
          disabled={saving}
          data-testid="settings-savebar-discard"
        >
          {discardLabel}
        </Button>
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onSave}
          disabled={saving}
          aria-disabled={saving}
          data-testid="settings-savebar-save"
        >
          {saving ? savingLabel : saveLabel}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast — one-shot confirmation for actions (export, rotate, delete). Distinct
// from SaveBar: role="status" so assistive tech announces it politely without
// implying a pending decision.
// ---------------------------------------------------------------------------

export type ToastTone = "success" | "info" | "danger";

export interface ToastProps {
  /** When false the toast renders nothing. */
  readonly open: boolean;
  readonly message: ReactNode;
  readonly tone?: ToastTone;
  /** Optional dismiss affordance. When omitted no close button renders. */
  readonly onDismiss?: () => void;
  readonly dismissLabel?: string;
}

function toastAccent(tone: ToastTone): string {
  switch (tone) {
    case "success":
      return "var(--color-success)";
    case "danger":
      return "var(--color-danger)";
    default:
      return "var(--color-accent)";
  }
}

export function Toast({
  open,
  message,
  tone = "success",
  onDismiss,
  dismissLabel = "Dismiss",
}: ToastProps): ReactElement | null {
  if (!open) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="settings-toast"
      data-tone={tone}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-sm)",
        padding: "8px 12px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-border-strong)",
        backgroundColor: "var(--color-surface)",
        color: "var(--color-text)",
        fontSize: "var(--font-size-sm)",
        boxShadow: "var(--shadow-subtle)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          flex: "0 0 auto",
          width: 6,
          height: 6,
          borderRadius: "var(--radius-full)",
          backgroundColor: toastAccent(tone),
        }}
      />
      <span style={{ minWidth: 0 }}>{message}</span>
      {onDismiss !== undefined ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={dismissLabel}
          title={dismissLabel}
          data-testid="settings-toast-dismiss"
          style={{
            marginLeft: "var(--space-xs)",
            border: "none",
            background: "transparent",
            color: "var(--color-text-muted)",
            cursor: "pointer",
            font: "inherit",
            lineHeight: 1,
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
