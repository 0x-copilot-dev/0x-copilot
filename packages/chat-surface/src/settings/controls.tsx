// Settings low-level controls the design system does not already provide
// (DESIGN-SPEC §4 control types). The design system already ships `.ctog`
// (Toggle/Switch), `.csel` (Select) and `.cin` (TextInput) — reuse those; this
// file adds only:
//
//   <SegmentedControl>  `.seg`  — pill radiogroup (mode / density / policy)
//   <AccentSwatch>      `.swatch` — a single accent dot (role="radio")
//   <ThemeTile>         `.theme-tile` — a theme preview tile (role="radio")
//   <ProgressBar>       `.bar`  — determinate progress (download flow)
//
// SegmentedControl is a self-contained radiogroup. AccentSwatch / ThemeTile are
// atoms with `role="radio"` + `aria-checked`; the consuming section (PR-5.3
// Appearance) wraps them in a `role="radiogroup"` container. Active states use
// the accent ring per DESIGN-SPEC §0 focus/active discipline.
//
// Substrate-agnostic. Colors resolve ONLY to design-system v2 tokens, EXCEPT
// the accent swatch's own dot color, which is runtime data supplied by the
// caller (from design-system `ACCENT_SCHEMES`) — a swatch must render its
// actual accent, so it is a prop, not a hard-coded literal.

import {
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// SegmentedControl — `.seg`. Single-select pill group with radiogroup
// semantics (DESIGN-SPEC §9 a11y: role="radiogroup"/"radio" + aria-checked).
// ---------------------------------------------------------------------------

export interface SegmentedOption<V extends string> {
  readonly value: V;
  readonly label: ReactNode;
  readonly disabled?: boolean;
}

export interface SegmentedControlProps<V extends string> {
  readonly options: ReadonlyArray<SegmentedOption<V>>;
  readonly value: V;
  readonly onChange: (value: V) => void;
  /** Required accessible name for the radiogroup. */
  readonly ariaLabel: string;
  readonly className?: string;
  readonly style?: CSSProperties;
}

const segGroupStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 2,
  padding: 2,
  // Design .seg — 7px group radius over the --panel ground.
  borderRadius: "7px",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface)",
};

function segItemStyle(selected: boolean, disabled: boolean): CSSProperties {
  return {
    appearance: "none",
    border: "none",
    borderRadius: "var(--radius-sm)",
    padding: "5px 12px",
    font: "inherit",
    fontSize: "var(--font-size-xs)",
    fontWeight: selected
      ? "var(--font-weight-semibold)"
      : "var(--font-weight-medium)",
    cursor: disabled ? "not-allowed" : "pointer",
    // Design: selected pill lifts to --panel3; no accent ring (the fill is the
    // only affordance).
    backgroundColor: selected ? "var(--color-surface-elevated)" : "transparent",
    color: selected ? "var(--color-text)" : "var(--color-text-muted)",
    opacity: disabled ? 0.5 : 1,
    transition: "background-color var(--duration-fast) var(--ease-standard)",
  };
}

export function SegmentedControl<V extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
  style,
}: SegmentedControlProps<V>): ReactElement {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={className}
      style={{ ...segGroupStyle, ...style }}
      data-testid="segmented-control"
    >
      {options.map((opt) => {
        const selected = opt.value === value;
        const disabled = opt.disabled ?? false;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={typeof opt.label === "string" ? opt.label : undefined}
            disabled={disabled}
            tabIndex={selected ? 0 : -1}
            data-value={opt.value}
            onClick={() => {
              if (!disabled && !selected) onChange(opt.value);
            }}
            style={segItemStyle(selected, disabled)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AccentSwatch — `.swatch`. A single accent dot, selectable. role="radio";
// wrap a row of these in a role="radiogroup" (PR-5.3 Appearance). Selected =
// accent ring.
// ---------------------------------------------------------------------------

export interface AccentSwatchProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "onClick"
> {
  /** The dot color (runtime data from design-system ACCENT_SCHEMES). */
  readonly swatch: string;
  /** Accessible name (e.g. "Sky"). */
  readonly label: string;
  readonly selected: boolean;
  readonly onSelect: () => void;
}

export function AccentSwatch({
  swatch,
  label,
  selected,
  onSelect,
  style,
  ...rest
}: AccentSwatchProps): ReactElement {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={label}
      title={label}
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      data-testid="accent-swatch"
      style={{
        appearance: "none",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 26,
        height: 26,
        padding: 0,
        borderRadius: "var(--radius-full)",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        boxShadow: selected
          ? "0 0 0 2px var(--color-accent), 0 0 0 4px var(--color-surface)"
          : "none",
        ...style,
      }}
      {...rest}
    >
      <span
        aria-hidden="true"
        data-swatch={swatch}
        style={{
          width: 16,
          height: 16,
          borderRadius: "var(--radius-full)",
          // Runtime accent color supplied by the caller (see file header).
          backgroundColor: swatch,
          border: "1px solid var(--color-border-strong)",
        }}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// ThemeTile — `.theme-tile`. A selectable theme preview tile (Dark / Light /
// System). role="radio"; wrap a row in a role="radiogroup". Selected = accent
// ring. `preview` is an optional swatch/glyph rendered above the label.
// ---------------------------------------------------------------------------

export interface ThemeTileProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "onClick"
> {
  readonly label: ReactNode;
  /** Sub-label (e.g. "Match macOS" for System). */
  readonly caption?: ReactNode;
  readonly preview?: ReactNode;
  readonly selected: boolean;
  readonly onSelect: () => void;
}

export function ThemeTile({
  label,
  caption,
  preview,
  selected,
  onSelect,
  style,
  ...rest
}: ThemeTileProps): ReactElement {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      data-testid="theme-tile"
      style={{
        appearance: "none",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-xs)",
        padding: "var(--space-sm)",
        minWidth: 92,
        borderRadius: "var(--radius-md)",
        border: selected
          ? "1px solid var(--color-accent)"
          : "1px solid var(--color-border)",
        backgroundColor: selected
          ? "var(--color-accent-soft)"
          : "var(--color-surface-muted)",
        color: "var(--color-text)",
        cursor: "pointer",
        textAlign: "left",
        font: "inherit",
        boxShadow: selected ? "0 0 0 1px var(--color-accent)" : "none",
        transition:
          "background-color var(--duration-fast) var(--ease-standard)",
        ...style,
      }}
      {...rest}
    >
      {preview !== undefined ? (
        <span
          aria-hidden="true"
          style={{
            display: "block",
            height: 34,
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--color-border)",
            overflow: "hidden",
          }}
        >
          {preview}
        </span>
      ) : null}
      <span
        style={{
          fontSize: "var(--font-size-sm)",
          fontWeight: "var(--font-weight-medium)",
        }}
      >
        {label}
      </span>
      {caption !== undefined ? (
        <span
          style={{
            fontSize: "var(--font-size-2xs)",
            color: "var(--color-text-muted)",
          }}
        >
          {caption}
        </span>
      ) : null}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ProgressBar — `.bar`. Determinate progress for the download-local-model
// flow. role="progressbar" + aria-valuenow/min/max. `tone="danger"` recolors
// to ember on interruption (DESIGN-SPEC §4 "download interrupt → ember").
// ---------------------------------------------------------------------------

export type ProgressTone = "accent" | "success" | "danger";

export interface ProgressBarProps {
  /** 0–100. Clamped. */
  readonly value: number;
  /** Required accessible name (e.g. "Downloading llama3"). */
  readonly ariaLabel: string;
  readonly tone?: ProgressTone;
  readonly className?: string;
  readonly style?: CSSProperties;
}

function progressColor(tone: ProgressTone): string {
  switch (tone) {
    case "success":
      return "var(--color-success)";
    case "danger":
      return "var(--color-danger)";
    default:
      return "var(--color-accent)";
  }
}

export function ProgressBar({
  value,
  ariaLabel,
  tone = "accent",
  className,
  style,
}: ProgressBarProps): ReactElement {
  const clamped = Math.min(Math.max(value, 0), 100);
  return (
    <div
      role="progressbar"
      aria-label={ariaLabel}
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={className}
      data-testid="progress-bar"
      style={{
        width: "100%",
        // Design .bar — 4px tall, 2px radius, no border, over --panel3.
        height: 4,
        borderRadius: 2,
        backgroundColor: "var(--color-surface-elevated)",
        overflow: "hidden",
        ...style,
      }}
    >
      <div
        data-testid="progress-bar-fill"
        data-tone={tone}
        style={{
          width: `${clamped}%`,
          height: "100%",
          borderRadius: "var(--radius-full)",
          backgroundColor: progressColor(tone),
          transition: "width var(--duration-normal) var(--ease-standard)",
        }}
      />
    </div>
  );
}
