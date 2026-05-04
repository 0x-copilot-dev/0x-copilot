import { classNames } from "@enterprise-search/design-system";
import { useEffect, useRef, useState, type ReactElement } from "react";

export type RunIndicator = {
  label: string;
  visible: boolean;
};

// Once the indicator becomes visible, keep it on-screen for at least this long
// even if `visible` flips back to false. On a warm conversation the agent
// often produces visible model_delta text within a frame of run start, so
// without a floor the pulsating "Planning next step..." hint would render for
// one paint and disappear before the user could perceive it.
//
// The CSS pulse animation is 1.2s long (see styles.css:
// .aui-planning-indicator__word). 1500ms covers one full cycle plus the
// staggered word delays, so the user sees at least one complete pulse.
const MIN_VISIBLE_MS = 1500;

export function PlanningIndicator({
  label,
  visible,
}: RunIndicator): ReactElement {
  const [shown, setShown] = useState(visible);
  const shownAtRef = useRef<number | null>(visible ? Date.now() : null);

  useEffect(() => {
    if (visible) {
      if (!shown) {
        shownAtRef.current = Date.now();
        setShown(true);
      } else if (shownAtRef.current === null) {
        shownAtRef.current = Date.now();
      }
      return;
    }
    if (!shown) {
      return;
    }
    const elapsed = Date.now() - (shownAtRef.current ?? 0);
    const wait = Math.max(0, MIN_VISIBLE_MS - elapsed);
    if (wait === 0) {
      shownAtRef.current = null;
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => {
      shownAtRef.current = null;
      setShown(false);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [visible, shown]);

  const words = label.split(" ");
  return (
    <div
      className="aui-planning-indicator"
      data-visible={shown ? "true" : "false"}
      role={shown ? "status" : undefined}
      aria-live={shown ? "polite" : undefined}
      aria-hidden={shown ? undefined : "true"}
      aria-label={label}
    >
      <span className="aui-planning-indicator__text" aria-hidden="true">
        {words.map((word, index) => (
          <span
            className={classNames(
              "aui-planning-indicator__word",
              `aui-planning-indicator__word--${index + 1}`,
            )}
            key={`${word}-${index}`}
          >
            {word}
          </span>
        ))}
      </span>
    </div>
  );
}
