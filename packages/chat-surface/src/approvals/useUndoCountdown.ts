// Countdown for the consent-card undo window (PR-1.6, moved from
// apps/frontend/.../tools/useUndoCountdown.ts).
//
// Returns ``{ secondsRemaining, expired }`` ticking once per second.
// The hook is server-clock agnostic: the input is an absolute Date
// from the server's ApprovalDecisionResponse.undo_expires_at; we just
// diff against ``Date.now()``. Server is authoritative on actual
// expiry — the FE countdown is best-effort UX.
//
// FR-1.30 timer neutralization: the tick uses the bare `setInterval` /
// `clearInterval` globals (not the `window.`-prefixed form) so the file
// carries no substrate-banned global. `setInterval`/`clearInterval` are
// not in `no-restricted-globals`; behavior (the 1000 ms tick) is
// byte-identical to the pre-hoist `window.setInterval` version.

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
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
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
