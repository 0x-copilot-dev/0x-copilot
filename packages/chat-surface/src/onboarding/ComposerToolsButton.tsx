// ComposerToolsButton — the run-scoped Tools pill in the composer action row.
//
// It deliberately owns no popover state or host DOM access. The paired
// ComposerToolsTrigger supplies the anchored disclosure; keeping this leaf
// simple lets every composer placement share the same visual control.

import type { CSSProperties, ReactNode } from "react";

import { Icon } from "../icons/Icon";

/**
 * "Tools" is the pill's ACCESSIBLE name only — it is deliberately not rendered
 * as visible text. The composer action row reads as icon + count (`🔌 2`), the
 * same register the model pill and the rest of the row use; the plug glyph
 * already carries the meaning, so the word was pure width.
 */
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
      aria-label={COMPOSER_TOOLS_BUTTON_COPY.label}
      data-testid="first-run-tools-button"
      data-open={open ? "true" : undefined}
      style={disabled === true ? disabledStyle : undefined}
    >
      <Icon name="plug" size={11} />
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
