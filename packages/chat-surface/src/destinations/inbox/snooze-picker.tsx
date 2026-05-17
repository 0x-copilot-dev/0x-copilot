// <SnoozePicker /> — quick-action popover for "snooze this inbox item until …".
//
// Source: inbox-prd.md §3.6 (snooze options surfaced on the detail action row)
// + cross-audit.md §1.6 (every status chip flows through StatusPill).
//
// Presentation-only. The host owns the side-effect (the PATCH /v1/inbox/{id}
// with `status="snoozed"` + `snoozed_until`); this component just emits an
// ISO-8601 string through `onSnooze`.
//
// Preset options come from inbox-prd.md §3.6 ("1 hour / Tomorrow /
// Next Monday / Custom datetime"). The "custom" branch lets the host
// render a native `<input type="datetime-local">` and parse it on submit
// — kept here for cohesion with the other three presets.

import { useState, type CSSProperties, type ReactElement } from "react";

import { StatusPill } from "../../shell/StatusPill";

/**
 * Reference time the presets are computed relative to. Defaulted to
 * `Date.now()` inside the component but exposed as a prop so tests can
 * pin the clock without monkey-patching globals (substrate boundary —
 * the chat-surface package can never reach for `Date` in a way the
 * desktop webview can't replicate deterministically).
 */
export interface SnoozePickerProps {
  /** Called with an ISO-8601 datetime string when the user picks an option. */
  readonly onSnooze: (isoDatetime: string) => void;
  /** Optional cancel/close — host renders the popover container. */
  readonly onCancel?: () => void;
  /** Reference time for preset computation. Defaults to now. */
  readonly now?: Date;
  /** Disabled state — host can disable while a previous snooze is in flight. */
  readonly disabled?: boolean;
}

/**
 * Built-in preset slugs. Kept stable so telemetry can correlate
 * (`mark_snoozed` span attribute `snooze_minutes` in §11).
 */
export type SnoozePresetSlug =
  | "one_hour"
  | "tomorrow"
  | "next_monday"
  | "custom";

interface PresetDescriptor {
  readonly slug: Exclude<SnoozePresetSlug, "custom">;
  readonly label: string;
  readonly compute: (now: Date) => Date;
}

const PRESETS: ReadonlyArray<PresetDescriptor> = [
  {
    slug: "one_hour",
    label: "1 hour",
    compute: (now) => new Date(now.getTime() + 60 * 60 * 1000),
  },
  {
    slug: "tomorrow",
    // 9am local the next calendar day (mirrors common email-snooze defaults).
    label: "Tomorrow",
    compute: (now) => {
      const next = new Date(now);
      next.setDate(next.getDate() + 1);
      next.setHours(9, 0, 0, 0);
      return next;
    },
  },
  {
    slug: "next_monday",
    label: "Next Monday",
    // 9am local on the next Monday strictly *after* `now` (so a Monday
    // picks "the Monday after this one", matching Gmail/Linear).
    compute: (now) => {
      const next = new Date(now);
      const day = next.getDay(); // 0=Sun..6=Sat
      // Days until *next* Monday, never zero.
      const delta = (1 - day + 7) % 7 || 7;
      next.setDate(next.getDate() + delta);
      next.setHours(9, 0, 0, 0);
      return next;
    },
  },
];

export function SnoozePicker({
  onSnooze,
  onCancel,
  now,
  disabled = false,
}: SnoozePickerProps): ReactElement {
  /* The "custom" branch is just a controlled `datetime-local` input. We
   * stash its raw string and convert only on submit so a half-typed
   * value doesn't fire a snooze. */
  const [customValue, setCustomValue] = useState("");

  const reference = now ?? new Date();

  const handlePreset = (preset: PresetDescriptor): void => {
    if (disabled) return;
    const target = preset.compute(reference);
    onSnooze(target.toISOString());
  };

  const handleCustomSubmit = (): void => {
    if (disabled) return;
    const trimmed = customValue.trim();
    if (trimmed === "") return;
    // `datetime-local` returns "YYYY-MM-DDTHH:mm" with no timezone — the
    // Date constructor interprets that as local time, which is the user's
    // intent. We then serialize back to ISO-8601 (UTC).
    const parsed = new Date(trimmed);
    if (Number.isNaN(parsed.getTime())) return;
    onSnooze(parsed.toISOString());
  };

  return (
    <div
      role="dialog"
      aria-label="Snooze inbox item"
      data-testid="inbox-snooze-picker"
      style={containerStyle}
    >
      <div style={headerStyle}>
        <StatusPill status="warning" label="Snooze" />
        <span style={hintStyle}>Until when?</span>
      </div>
      <div style={presetGridStyle} role="group" aria-label="Snooze presets">
        {PRESETS.map((preset) => (
          <button
            key={preset.slug}
            type="button"
            disabled={disabled}
            onClick={() => handlePreset(preset)}
            style={presetButtonStyle(disabled)}
            data-testid={`inbox-snooze-preset-${preset.slug}`}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div style={customRowStyle}>
        <label htmlFor="inbox-snooze-custom" style={customLabelStyle}>
          Custom
        </label>
        <input
          id="inbox-snooze-custom"
          type="datetime-local"
          value={customValue}
          disabled={disabled}
          onChange={(event) => setCustomValue(event.target.value)}
          style={customInputStyle}
          data-testid="inbox-snooze-custom-input"
        />
        <button
          type="button"
          onClick={handleCustomSubmit}
          disabled={disabled || customValue.trim() === ""}
          style={customSubmitStyle(disabled || customValue.trim() === "")}
          data-testid="inbox-snooze-custom-submit"
        >
          Set
        </button>
      </div>
      {onCancel !== undefined ? (
        <div style={footerStyle}>
          <button
            type="button"
            onClick={onCancel}
            style={cancelStyle}
            data-testid="inbox-snooze-cancel"
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 12,
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  minWidth: 240,
  color: "var(--color-text)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const hintStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--color-text-muted)",
};

const presetGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 6,
};

const presetButtonStyle = (disabled: boolean): CSSProperties => ({
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-text)",
  fontSize: 12,
  fontWeight: 600,
  cursor: disabled ? "not-allowed" : "pointer",
  opacity: disabled ? 0.6 : 1,
});

const customRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const customLabelStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--color-text-muted)",
  fontWeight: 500,
  flexShrink: 0,
};

const customInputStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  height: 28,
  padding: "0 8px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: 12,
  fontFamily: "inherit",
};

const customSubmitStyle = (disabled: boolean): CSSProperties => ({
  height: 28,
  padding: "0 10px",
  borderRadius: 6,
  border: "none",
  background: disabled ? "var(--color-surface-muted)" : "var(--color-accent)",
  color: disabled ? "var(--color-text-subtle)" : "var(--color-accent-contrast)",
  fontSize: 12,
  fontWeight: 600,
  cursor: disabled ? "not-allowed" : "pointer",
});

const footerStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};

const cancelStyle: CSSProperties = {
  height: 28,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "transparent",
  color: "var(--color-text-muted)",
  fontSize: 12,
  cursor: "pointer",
};
