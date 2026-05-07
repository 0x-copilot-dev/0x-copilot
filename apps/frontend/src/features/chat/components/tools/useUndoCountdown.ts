// PR 4.4.6.4 — countdown for the consent-card undo window.
//
// Returns ``{ secondsRemaining, expired }`` ticking once per second.
// The hook is server-clock agnostic: the input is an absolute Date
// from the server's ApprovalDecisionResponse.undo_expires_at; we just
// diff against ``Date.now()``. Server is authoritative on actual
// expiry — the FE countdown is best-effort UX.

import { useEffect, useState } from "react";

export interface UndoCountdownState {
  secondsRemaining: number;
  expired: boolean;
}

export function useUndoCountdown(undoUntil: Date | null): UndoCountdownState {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!undoUntil) {
      return;
    }
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [undoUntil]);

  if (!undoUntil) {
    return { secondsRemaining: 0, expired: true };
  }
  const remainingMs = undoUntil.getTime() - now;
  return {
    secondsRemaining: Math.max(0, Math.ceil(remainingMs / 1000)),
    expired: remainingMs <= 0,
  };
}
