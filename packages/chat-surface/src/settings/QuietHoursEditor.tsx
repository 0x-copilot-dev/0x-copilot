// <QuietHoursEditor /> — sub-component for NotificationsPage.
//
// Source: team-memory-cmdk-prd.md §U-S1 (quiet-hours editor) + api-types
// `NotificationQuietHoursBlob`. Pure presentation: receives a blob and
// emits a new blob through `onChange`. Inline validation: the window
// must wrap forward — `from_local === to_local` is rejected (a zero-
// length window is meaningless); midnight-wrap (e.g. 22:00 → 06:00) is
// allowed.
//
// The host owns the timezone source. By default this editor surfaces
// the browser's IANA tz id (`Intl.DateTimeFormat().resolvedOptions().
// timeZone`); the host can override via the `tzOptions` prop when it
// wants to pin a workspace-wide list.

import {
  useCallback,
  useId,
  useMemo,
  type CSSProperties,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type { NotificationQuietHoursBlob } from "@enterprise-search/api-types";

export interface QuietHoursEditorProps {
  readonly value: NotificationQuietHoursBlob;
  readonly onChange: (next: NotificationQuietHoursBlob) => void;
  /**
   * IANA tz ids the host wants to surface. Falls back to a small set
   * anchored on the browser's resolved tz.
   */
  readonly tzOptions?: ReadonlyArray<string>;
  readonly disabled?: boolean;
}

const HH_MM_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

function browserTz(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof tz === "string" && tz.length > 0 ? tz : "UTC";
  } catch {
    return "UTC";
  }
}

function defaultTzOptions(currentTz: string): ReadonlyArray<string> {
  const baseline = [
    "UTC",
    "America/Los_Angeles",
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Australia/Sydney",
  ];
  return baseline.includes(currentTz) ? baseline : [currentTz, ...baseline];
}

/**
 * Returns an inline-error string when `from_local`/`to_local` form an
 * invalid window. `null` means "valid".
 *
 * Rules (sub-PRD §U-S1):
 *   * Both endpoints must match `HH:MM` 24-hour.
 *   * `from === to` is rejected — zero-length window is meaningless.
 *   * `from > to` (midnight-wrap) is allowed and means "ends tomorrow".
 */
export function validateQuietHoursWindow(
  fromLocal: string,
  toLocal: string,
): string | null {
  if (!HH_MM_RE.test(fromLocal)) {
    return "Start time must be HH:MM (24-hour).";
  }
  if (!HH_MM_RE.test(toLocal)) {
    return "End time must be HH:MM (24-hour).";
  }
  if (fromLocal === toLocal) {
    return "Start and end must differ.";
  }
  return null;
}

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
  gap: 12,
  flexWrap: "wrap",
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};

const inputStyle: CSSProperties = {
  height: 30,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-surface, #18181a)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const errorStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-danger, #f5826b)",
};

export function QuietHoursEditor({
  value,
  onChange,
  tzOptions,
  disabled,
}: QuietHoursEditorProps): ReactElement {
  const reactId = useId();
  const enabledId = `${reactId}-enabled`;
  const fromId = `${reactId}-from`;
  const toId = `${reactId}-to`;
  const tzId = `${reactId}-tz`;
  const errorId = `${reactId}-error`;

  const inlineError = useMemo(
    () => validateQuietHoursWindow(value.from_local, value.to_local),
    [value.from_local, value.to_local],
  );

  const currentBrowserTz = useMemo(browserTz, []);
  const resolvedTzOptions = useMemo(() => {
    const opts = tzOptions ?? defaultTzOptions(currentBrowserTz);
    // Always include the current value's tz so the <select> can show it.
    return opts.includes(value.tz) ? opts : [value.tz, ...opts];
  }, [tzOptions, currentBrowserTz, value.tz]);

  const handleEnabled = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      onChange({ ...value, enabled: e.target.checked });
    },
    [onChange, value],
  );

  const handleFrom = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      onChange({ ...value, from_local: e.target.value });
    },
    [onChange, value],
  );

  const handleTo = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      onChange({ ...value, to_local: e.target.value });
    },
    [onChange, value],
  );

  const handleTz = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      onChange({ ...value, tz: e.target.value });
    },
    [onChange, value],
  );

  return (
    <fieldset style={fieldsetStyle} data-testid="quiet-hours-editor">
      <legend style={legendStyle}>Quiet hours</legend>
      <div style={rowStyle}>
        <label htmlFor={enabledId} style={labelStyle}>
          <input
            id={enabledId}
            type="checkbox"
            checked={value.enabled}
            onChange={handleEnabled}
            disabled={disabled}
            data-testid="quiet-hours-enabled"
          />
          <span>Mute notifications during this window</span>
        </label>
      </div>
      <div style={rowStyle}>
        <label htmlFor={fromId} style={labelStyle}>
          <span>From</span>
          <input
            id={fromId}
            type="time"
            value={value.from_local}
            onChange={handleFrom}
            disabled={disabled || !value.enabled}
            aria-invalid={inlineError !== null}
            aria-describedby={inlineError !== null ? errorId : undefined}
            style={inputStyle}
            data-testid="quiet-hours-from"
          />
        </label>
        <label htmlFor={toId} style={labelStyle}>
          <span>To</span>
          <input
            id={toId}
            type="time"
            value={value.to_local}
            onChange={handleTo}
            disabled={disabled || !value.enabled}
            aria-invalid={inlineError !== null}
            aria-describedby={inlineError !== null ? errorId : undefined}
            style={inputStyle}
            data-testid="quiet-hours-to"
          />
        </label>
        <label htmlFor={tzId} style={labelStyle}>
          <span>Timezone</span>
          <select
            id={tzId}
            value={value.tz}
            onChange={handleTz}
            disabled={disabled || !value.enabled}
            style={inputStyle}
            data-testid="quiet-hours-tz"
          >
            {resolvedTzOptions.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
        </label>
      </div>
      {inlineError !== null ? (
        <div
          id={errorId}
          role="alert"
          style={errorStyle}
          data-testid="quiet-hours-error"
        >
          {inlineError}
        </div>
      ) : null}
    </fieldset>
  );
}
