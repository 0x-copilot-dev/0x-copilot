import { useEffect, useState } from "react";

// FR-1.30 — the interval timers reference the bare `setInterval` /
// `clearInterval` globals (which are NOT substrate-restricted) rather than
// the browser-object-prefixed forms, so this hook is portable to any
// substrate (web + desktop webview) without a browser-global lint violation.
// The 5000 ms tick cadence is unchanged.
export function useElapsedSeconds(
  active: boolean,
  startedAt: string | null,
): number {
  const [mountedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, [active]);
  const parsedStartedAt = startedAt ? Date.parse(startedAt) : Number.NaN;
  const startMs = Number.isFinite(parsedStartedAt)
    ? parsedStartedAt
    : mountedAt;
  return Math.max(0, Math.floor((now - startMs) / 1000));
}
