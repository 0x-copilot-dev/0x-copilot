// ComposerToolsButton — the run-scoped Tools pill in the composer action row.
//
// It deliberately owns no popover state or host DOM access. The paired
// ComposerToolsTrigger supplies the anchored disclosure; keeping this leaf
// simple lets every composer placement share the same visual control.

import type { CSSProperties, ReactNode } from "react";

import { Icon } from "../icons/Icon";

export const COMPOSER_TOOLS_BUTTON_COPY = {
  label: "Tools",
} as const;

export interface ComposerToolsButtonProps {
  readonly open: boolean;
  readonly onClick: () => void;
  /** Tools currently ON (web search + active connectors). Badge is hidden at 0. */
  readonly activeCount: number;
  readonly disabled?: boolean;
}

export function ComposerToolsButton(
  props: ComposerToolsButtonProps,
): ReactNode {
  const { open, onClick, activeCount, disabled } = props;
  return (
    <button
      type="button"
      className="ui-cpill"
      onClick={onClick}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label="Tools"
      data-testid="first-run-tools-button"
      data-open={open ? "true" : undefined}
      style={disabled === true ? disabledStyle : undefined}
    >
      <Icon name="plug" size={11} />
      <span className="ui-cpill__lb">{COMPOSER_TOOLS_BUTTON_COPY.label}</span>
      {activeCount > 0 ? (
        <span
          className="ui-cpill__n"
          data-testid="first-run-tools-button-badge"
        >
          {activeCount}
        </span>
      ) : null}
    </button>
  );
}

const disabledStyle: CSSProperties = {
  opacity: 0.5,
  cursor: "default",
};
