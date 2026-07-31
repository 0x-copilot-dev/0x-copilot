import { Menu } from "@0x-copilot/design-system";
import { useRef, useState, type ReactElement } from "react";

import { Icon } from "../icons/Icon";
import type {
  FilesystemBypassMode,
  FilesystemBypassScope,
} from "./filesystemBypass";

/**
 * Composer execution-mode pill — **Manual** (default) or **Bypass**
 * (PRD-FS-10 §4.3). Same pill + popover recipe as {@link ModelPill}; no new
 * component vocabulary and no new glyph.
 *
 * The three-tier gate, and why `enabled` is not just a disabled style:
 *
 * - `enabled === false` (the Settings master switch is off) → a **disabled
 *   Manual** pill whose menu never opens. Bypass is not rendered anywhere, not
 *   rendered-and-greyed: offered-but-ignored is worse than absent, because a
 *   user who can see and click "Bypass" reasonably concludes it did something.
 * - `enabled === true` → a real menu: Manual · Bypass, plus a scope choice and
 *   a non-selectable clarifier that states the standing rule.
 *
 * The clarifier is terse on purpose. "Ungranted still asks" is a fact about the
 * system, not a warning about this particular choice; a sentence there would
 * read as the latter and make the safe option look risky.
 *
 * This component grants nothing. It reports a selection; the run-create seam
 * folds it against the server-held master switch, and the runtime re-checks the
 * bound (granted + writable + reversible) before any pause is skipped.
 */
export interface BypassPillProps {
  /** Current mode. */
  readonly mode: FilesystemBypassMode;
  /** Master switch (Settings). `false` ⇒ disabled Manual, no Bypass offered. */
  readonly enabled: boolean;
  /** Report a selection. */
  readonly onChange: (mode: FilesystemBypassMode) => void;
  /**
   * Scope of the selection. Optional: a host that does not surface the
   * message/run choice omits both and gets message scope, the safer default
   * (spent after one send).
   */
  readonly scope?: FilesystemBypassScope;
  readonly onScopeChange?: (scope: FilesystemBypassScope) => void;
  /** Disable the control for reasons other than the master switch (e.g. a run in flight). */
  readonly disabled?: boolean;
}

const MODE_LABEL: Record<FilesystemBypassMode, string> = {
  manual: "Manual",
  bypass: "Bypass",
};

const MODE_SUB: Record<FilesystemBypassMode, string> = {
  manual: "ask before each change",
  bypass: "apply changes without asking",
};

const SCOPE_LABEL: Record<FilesystemBypassScope, string> = {
  message: "This message",
  run: "This run",
};

/** The standing rule, stated as a fact. Exported so tests name it once. */
export const BYPASS_BOUND_NOTE = "Ungranted still asks";
export const BYPASS_BOUND_SUB = "bypass never widens what you granted";
export const BYPASS_DISABLED_TOOLTIP =
  "Turn on bypass in Settings → Model & behavior";

export function BypassPill({
  mode,
  enabled,
  onChange,
  scope = "message",
  onScopeChange,
  disabled = false,
}: BypassPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // The master switch is not a styling concern: with it off the pill reports
  // Manual and the menu cannot open, so no Bypass option exists in the DOM for
  // a user (or a test) to find.
  const locked = !enabled || disabled;
  const shownMode: FilesystemBypassMode = enabled ? mode : "manual";

  const commit = (next: FilesystemBypassMode): void => {
    onChange(next);
    setOpen(false);
  };

  const renderModeRow = (value: FilesystemBypassMode): ReactElement => {
    const active = value === shownMode;
    return (
      <button
        key={value}
        type="button"
        role="menuitemradio"
        aria-checked={active}
        className="ui-pop-row atlas-bypass-pill__item"
        data-on={active || undefined}
        onClick={() => commit(value)}
      >
        <span className="ui-pop-row__m atlas-bypass-pill__col">
          <span className="ui-pop-row__txt">{MODE_LABEL[value]}</span>
          <span className="ui-pop-row__sb">{MODE_SUB[value]}</span>
        </span>
        <span className="ui-pop-row__rad" aria-hidden="true">
          {active ? <Icon name="check" size={9} strokeWidth={3} /> : null}
        </span>
      </button>
    );
  };

  const renderScopeRow = (value: FilesystemBypassScope): ReactElement => {
    const active = value === scope;
    return (
      <button
        key={value}
        type="button"
        role="menuitemradio"
        aria-checked={active}
        className="ui-pop-row atlas-bypass-pill__item"
        data-on={active || undefined}
        onClick={() => {
          onScopeChange?.(value);
          setOpen(false);
        }}
      >
        <span className="ui-pop-row__m atlas-bypass-pill__col">
          <span className="ui-pop-row__txt">{SCOPE_LABEL[value]}</span>
        </span>
        <span className="ui-pop-row__rad" aria-hidden="true">
          {active ? <Icon name="check" size={9} strokeWidth={3} /> : null}
        </span>
      </button>
    );
  };

  return (
    <div className="atlas-bypass-pill__root">
      <button
        ref={buttonRef}
        type="button"
        className="atlas-bypass-pill ui-cpill"
        aria-haspopup={enabled ? "menu" : undefined}
        aria-expanded={enabled ? open : undefined}
        aria-label={`Execution mode: ${MODE_LABEL[shownMode]}`}
        disabled={locked}
        data-mode={shownMode}
        data-open={open || undefined}
        onClick={() => {
          if (locked) return;
          setOpen((current) => !current);
        }}
        data-tooltip={locked ? BYPASS_DISABLED_TOOLTIP : "Execution mode"}
        data-tooltip-placement="bottom"
      >
        <span className="ui-cpill__lb atlas-bypass-pill__name">
          {MODE_LABEL[shownMode]}
        </span>
        <Icon
          name="chevronDown"
          size={11}
          className="atlas-bypass-pill__caret"
        />
      </button>
      {/* Rendered only when the master switch is on. A closed-but-mounted menu
       * would still put "Bypass" in the accessibility tree, which is the
       * offered-but-ignored state this gate exists to prevent. */}
      {enabled ? (
        <Menu
          open={open}
          onClose={() => setOpen(false)}
          anchorRef={buttonRef}
          side="up"
          align="left"
          className="ui-pop atlas-bypass-pill__menu"
        >
          <div className="ui-pop__h">
            Execution <span className="ui-pop__h-meta">this chat</span>
          </div>
          <div className="ui-pop__list">
            {(["manual", "bypass"] as const).map(renderModeRow)}
            {onScopeChange !== undefined && shownMode === "bypass" ? (
              <>
                <div className="ui-pop__grp" aria-hidden="true">
                  Applies to
                </div>
                {(["message", "run"] as const).map(renderScopeRow)}
              </>
            ) : null}
          </div>
          {/* Non-selectable: it is a standing rule, not an option. */}
          <div className="ui-pop__f atlas-bypass-pill__note" role="note">
            <span className="atlas-bypass-pill__note-lead">
              {BYPASS_BOUND_NOTE}
            </span>
            <span className="atlas-bypass-pill__note-sub">
              {BYPASS_BOUND_SUB}
            </span>
          </div>
        </Menu>
      ) : null}
    </div>
  );
}
