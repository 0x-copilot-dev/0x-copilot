// <AccessModeSegment> — per-connector 3-way access-mode control.
//
// Source: phase-4/PRD.md FR-4.21/4.22 + DESIGN-SPEC §3 (Tools = connectors:
// "per-tool segmented Read / Read & act / Off"). Each connected tool row in
// the Tools destination renders one of these, reflecting the connector's
// current `access_mode` and firing `onChange` when the user picks another.
//
// This is a self-contained `radiogroup` (roving tabindex + arrow-key nav +
// `aria-checked`), NOT the settings `SegmentedControl` — a per-connector
// access switch needs full keyboard-radio semantics and its own fixed,
// wire-derived option set. The three options are enumerated from the
// `CONNECTOR_ACCESS_MODES` value tuple (api-types SSOT) so the control can
// never drift from the union.
//
// Pure presentation: no fetch, no persistence. The host wires `onChange` to
// the access-mode PATCH; the optimistic-update + revert-on-failure lives in
// the binder (PRD §11), not here. Token-only styles.

import {
  useRef,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import {
  CONNECTOR_ACCESS_MODES,
  type ConnectorAccessMode,
} from "@0x-copilot/api-types";

export interface AccessModeSegmentProps {
  /** The connector's current access mode — the checked radio. */
  readonly value: ConnectorAccessMode;
  /** Fired with the picked mode (never re-fired for the current value). */
  readonly onChange: (mode: ConnectorAccessMode) => void;
  /** Renders every option non-interactive + dimmed. */
  readonly disabled?: boolean;
  /**
   * Accessible name for the radiogroup. Hosts should pass a per-connector
   * name (e.g. `Access mode for Gmail`) so multiple segments on one page are
   * distinguishable to assistive tech. Defaults to a generic name.
   */
  readonly ariaLabel?: string;
  /** Optional element id on the radiogroup (a11y wiring / label association). */
  readonly id?: string;
}

/** Human labels for each mode — single source for the pill + a11y name. */
const ACCESS_MODE_LABEL: Readonly<Record<ConnectorAccessMode, string>> = {
  read: "Read",
  read_act: "Read & act",
  off: "Off",
};

export function AccessModeSegment({
  value,
  onChange,
  disabled = false,
  ariaLabel = "Access mode",
  id,
}: AccessModeSegmentProps): ReactElement {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  // Move focus + selection to the mode at `index` (wrapping). Radiogroup
  // convention: arrow keys change the selection, not just focus.
  const selectAt = (index: number): void => {
    const count = CONNECTOR_ACCESS_MODES.length;
    const wrapped = ((index % count) + count) % count;
    const next = CONNECTOR_ACCESS_MODES[wrapped];
    if (next === undefined) return;
    // Focus the target button imperatively — every radio is always in the
    // DOM, so this lands correctly regardless of the controlled re-render.
    refs.current[wrapped]?.focus();
    if (next !== value) onChange(next);
  };

  const handleKeyDown = (
    e: ReactKeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void => {
    if (disabled) return;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
        e.preventDefault();
        selectAt(index + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        e.preventDefault();
        selectAt(index - 1);
        break;
      case "Home":
        e.preventDefault();
        selectAt(0);
        break;
      case "End":
        e.preventDefault();
        selectAt(CONNECTOR_ACCESS_MODES.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-disabled={disabled ? true : undefined}
      id={id}
      data-testid="access-mode-segment"
      data-value={value}
      style={groupStyle}
    >
      {CONNECTOR_ACCESS_MODES.map((mode, index) => {
        const selected = mode === value;
        return (
          <button
            key={mode}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={ACCESS_MODE_LABEL[mode]}
            disabled={disabled}
            tabIndex={selected ? 0 : -1}
            data-value={mode}
            data-testid={`access-mode-option-${mode}`}
            onClick={() => {
              if (!disabled && !selected) onChange(mode);
            }}
            onKeyDown={(e) => handleKeyDown(e, index)}
            style={itemStyle(selected, disabled)}
          >
            {ACCESS_MODE_LABEL[mode]}
          </button>
        );
      })}
    </div>
  );
}

// === Styles ================================================================

const groupStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 2,
  padding: 2,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg, #131316)",
};

function itemStyle(selected: boolean, disabled: boolean): CSSProperties {
  return {
    appearance: "none",
    border: "none",
    borderRadius: "var(--radius-sm, 6px)",
    padding: "4px 10px",
    font: "inherit",
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: selected ? 600 : 500,
    cursor: disabled ? "not-allowed" : "pointer",
    background: selected ? "var(--color-bg-elevated, #18181b)" : "transparent",
    color: selected
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    boxShadow: selected ? "0 0 0 1px var(--color-accent, #d97757)" : "none",
    opacity: disabled ? 0.5 : 1,
    transition:
      "background-color var(--duration-fast, 120ms) var(--ease-standard, ease)",
  };
}
