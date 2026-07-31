// BypassPill — "will this run ask me, or just go?" (PRD-FS-10 §4.3).
//
// It takes the composer slot the model pill vacated, because that slot is the
// first thing the eye reaches and model choice is set-and-forget while execution
// mode is the decision a user re-makes per task.
//
// THIS PRD SHIPS SELECTION ONLY. Nothing here changes what the runtime does;
// PRD-FS-11 consumes the value and owns precedence (message > run > master).
// Shipping the control ahead of the behaviour is deliberate — the two land
// independently — which is exactly why the DISABLED state matters more than the
// enabled one: until the Settings master is on, this must not offer Bypass at
// all. An option that is offered and then ignored is worse than an absent one.
//
// Same pill recipe as the model pill (`.ui-cpill` + the shared `.ui-pop*`
// popover), no new component and no new glyph.

import { Menu } from "@0x-copilot/design-system";
import { useRef, useState, type ReactElement } from "react";

import { Icon } from "../icons/Icon";

/** Manual asks before each act; bypass does not. Default is manual, always. */
export type BypassMode = "manual" | "bypass";

/** Pinned copy — the tests match these, and so does a user reading the pill. */
export const BYPASS_PILL_COPY = {
  manual: "Manual",
  bypass: "Bypass",
  manualSub: "ask before each act",
  bypassSub: "act without asking",
  /** Terse because it is a standing RULE, not a warning about this choice. */
  clarifier: "Ungranted still asks",
  clarifierSub: "bypass never widens what you granted",
  offHint: "Turn on bypass in Settings to change this",
} as const;

export interface BypassPillProps {
  /** Current selection. Ignored for display when `enabled` is false. */
  readonly mode: BypassMode;
  /**
   * The Settings master, off by default. False ⇒ a DISABLED **Manual** pill
   * with a tooltip pointing at Settings, and no menu — not a menu whose second
   * row does nothing.
   */
  readonly enabled: boolean;
  readonly onChange: (mode: BypassMode) => void;
}

/**
 * Execution-mode pill: `Manual ▾` / `Bypass ▾`.
 *
 * When `enabled` is false the label is forced to **Manual** regardless of the
 * `mode` prop. A stale "Bypass" selection surviving a master switched back off
 * would claim behaviour the system will not perform, and the pill's whole job is
 * to say what this run will actually do.
 */
export function BypassPill({
  mode,
  enabled,
  onChange,
}: BypassPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const effective: BypassMode = enabled ? mode : "manual";
  const label = BYPASS_PILL_COPY[effective];

  const commit = (next: BypassMode): void => {
    setOpen(false);
    if (next !== mode) {
      onChange(next);
    }
  };

  const row = (value: BypassMode, sub: string): ReactElement => (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={effective === value}
      className="ui-pop-row"
      data-on={effective === value || undefined}
      onClick={() => commit(value)}
    >
      <span className="ui-pop-row__m">
        <span className="ui-pop-row__nm">
          <span className="ui-pop-row__txt">{BYPASS_PILL_COPY[value]}</span>
        </span>
        <span className="ui-pop-row__sb">{sub}</span>
      </span>
      <span className="ui-pop-row__rad" aria-hidden="true">
        {effective === value ? (
          <Icon name="check" size={9} strokeWidth={3} />
        ) : null}
      </span>
    </button>
  );

  return (
    <div className="atlas-bypass-pill__root">
      <button
        ref={buttonRef}
        type="button"
        className="atlas-bypass-pill"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Execution mode: ${label}`}
        disabled={!enabled}
        data-mode={effective}
        data-open={open || undefined}
        data-tooltip={
          enabled ? "Choose execution mode" : BYPASS_PILL_COPY.offHint
        }
        data-tooltip-placement="bottom"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="ui-cpill__lb atlas-bypass-pill__name">{label}</span>
        <Icon
          name="chevronDown"
          size={11}
          className="atlas-bypass-pill__caret"
        />
      </button>
      {/* Mounted only while open (the `Menu` primitive returns null otherwise),
          and never openable while disabled — so with the master off there is no
          "Bypass" node anywhere in the document for a user to reach. */}
      <Menu
        open={open && enabled}
        onClose={() => setOpen(false)}
        anchorRef={buttonRef}
        side="up"
        align="left"
        className="ui-pop atlas-bypass-pill__menu"
      >
        <div className="ui-pop__h">
          Execution <span className="ui-pop__h-meta">this run</span>
        </div>
        <div className="ui-pop__list">
          {row("manual", BYPASS_PILL_COPY.manualSub)}
          {row("bypass", BYPASS_PILL_COPY.bypassSub)}
        </div>
        {/* Non-selectable on purpose: it is a fact about the system, not a
            third choice. `.ui-pop-row` would give it the hover fill of one. */}
        <div className="atlas-bypass-pill__note">
          <span className="atlas-bypass-pill__note-lead">
            {BYPASS_PILL_COPY.clarifier}
          </span>
          <span className="atlas-bypass-pill__note-sub">
            {BYPASS_PILL_COPY.clarifierSub}
          </span>
        </div>
      </Menu>
    </div>
  );
}
