// PR 3.2 — tiny viewport-width hook used by the workspace pane to
// switch into overlay mode below a breakpoint. Subscribes to a single
// `MediaQueryList` listener so re-renders only happen when the
// breakpoint is crossed (not on every resize tick).
//
// Returns `true` when the viewport is **narrower than** the breakpoint.

import { useEffect, useState } from "react";

export function useViewportOverlay(breakpointPx: number): boolean {
  const [overlay, setOverlay] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) {
      return false;
    }
    return window.matchMedia(`(max-width: ${breakpointPx - 1}px)`).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) {
      return undefined;
    }
    const mql = window.matchMedia(`(max-width: ${breakpointPx - 1}px)`);
    const onChange = (event: MediaQueryListEvent | MediaQueryList): void => {
      setOverlay("matches" in event ? event.matches : false);
    };
    setOverlay(mql.matches);
    mql.addEventListener("change", onChange);
    return () => {
      mql.removeEventListener("change", onChange);
    };
  }, [breakpointPx]);

  return overlay;
}
