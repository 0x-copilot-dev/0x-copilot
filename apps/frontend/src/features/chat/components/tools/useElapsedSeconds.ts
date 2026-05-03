import { useEffect, useState } from "react";

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
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [active]);
  const parsedStartedAt = startedAt ? Date.parse(startedAt) : Number.NaN;
  const startMs = Number.isFinite(parsedStartedAt)
    ? parsedStartedAt
    : mountedAt;
  return Math.max(0, Math.floor((now - startMs) / 1000));
}
