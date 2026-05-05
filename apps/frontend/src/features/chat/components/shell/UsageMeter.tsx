import { IconButton } from "@enterprise-search/design-system";
import type { ReactElement } from "react";

export interface UsageMeterProps {
  /**
   * 0–100 percentage of the context window currently *used*. `null` means
   * "unknown" — the meter renders empty and the click still opens the
   * usage details panel where the user can see the breakdown.
   */
  pct: number | null;
  onOpen: () => void;
  disabled?: boolean;
}

const FULL_BAR_SEGMENTS = 12;

/**
 * Compact bar + percentage. Clicking opens the existing usage details
 * panel (no new fetch in this component). Visualised as a fixed segment
 * count so percentage drift never causes pixel jitter.
 */
export function UsageMeter({
  pct,
  onOpen,
  disabled,
}: UsageMeterProps): ReactElement {
  const clamped =
    pct === null ? null : Math.max(0, Math.min(100, Math.round(pct)));
  const filled =
    clamped === null
      ? 0
      : Math.max(
          0,
          Math.min(
            FULL_BAR_SEGMENTS,
            Math.round((clamped / 100) * FULL_BAR_SEGMENTS),
          ),
        );
  const tone =
    clamped === null
      ? "unknown"
      : clamped >= 90
        ? "danger"
        : clamped >= 70
          ? "warn"
          : "ok";
  const label =
    clamped === null
      ? "Usage — open for details"
      : `Usage — ${clamped}% of context window`;
  return (
    <IconButton
      type="button"
      variant="ghost"
      className="atlas-usage-meter"
      onClick={onOpen}
      disabled={disabled}
      data-tone={tone}
      aria-label={label}
      data-tooltip="Open usage"
      data-tooltip-placement="bottom"
    >
      <span className="atlas-usage-meter__bar" aria-hidden="true">
        {Array.from({ length: FULL_BAR_SEGMENTS }, (_, index) => (
          <span
            key={index}
            className="atlas-usage-meter__seg"
            data-on={index < filled ? "true" : undefined}
          />
        ))}
      </span>
      <span className="atlas-usage-meter__pct">
        {clamped === null ? "—" : `${clamped}%`}
      </span>
    </IconButton>
  );
}
