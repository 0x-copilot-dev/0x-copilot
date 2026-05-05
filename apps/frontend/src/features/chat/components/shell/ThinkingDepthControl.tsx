import type { ReactElement } from "react";
import {
  THINKING_DEPTHS,
  depthDescription,
  depthLabel,
  type ThinkingDepth,
} from "../../depth";

export interface ThinkingDepthControlProps {
  value: ThinkingDepth;
  onChange: (depth: ThinkingDepth) => void;
  /**
   * Hides the control when the active model doesn't support reasoning.
   * The component returns null in that case so the topbar layout
   * collapses cleanly — no display: none placeholder.
   */
  visible: boolean;
  disabled?: boolean;
}

/**
 * Three-segment radiogroup for Fast / Balanced / Deep. Maps to the
 * model's `reasoning.effort` slot through `applyDepth` in `depth.ts`.
 */
export function ThinkingDepthControl({
  value,
  onChange,
  visible,
  disabled,
}: ThinkingDepthControlProps): ReactElement | null {
  if (!visible) {
    return null;
  }
  return (
    <div
      className="atlas-depth"
      role="radiogroup"
      aria-label="Thinking depth — applies to your next message"
    >
      {THINKING_DEPTHS.map((depth) => {
        const checked = depth === value;
        return (
          <button
            key={depth}
            type="button"
            role="radio"
            aria-checked={checked}
            className="atlas-depth__chip"
            data-active={checked || undefined}
            disabled={disabled}
            onClick={() => onChange(depth)}
            onKeyDown={(event) => handleArrow(event, value, onChange)}
            data-tooltip={depthDescription(depth)}
            data-tooltip-placement="bottom"
          >
            {depthLabel(depth)}
          </button>
        );
      })}
    </div>
  );
}

function handleArrow(
  event: React.KeyboardEvent<HTMLButtonElement>,
  current: ThinkingDepth,
  onChange: (depth: ThinkingDepth) => void,
): void {
  const index = THINKING_DEPTHS.indexOf(current);
  if (index < 0) {
    return;
  }
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    event.preventDefault();
    onChange(THINKING_DEPTHS[(index + 1) % THINKING_DEPTHS.length]);
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    event.preventDefault();
    onChange(
      THINKING_DEPTHS[
        (index - 1 + THINKING_DEPTHS.length) % THINKING_DEPTHS.length
      ],
    );
  }
}
