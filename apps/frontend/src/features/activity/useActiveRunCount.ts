import { useEffect, useState } from "react";

import type { AgentRunStatus } from "@0x-copilot/api-types";

import { listConversations } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";

// In-flight run statuses (mirrors activityApi.mapRunStatus → "running"/
// "needs_input"): a run is "active" while queued/running/cancelling or blocked
// on an approval. done/cancelled/failed/timed_out are terminal.
const ACTIVE_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "running",
  "queued",
  "cancelling",
  "waiting_for_approval",
]);

const POLL_MS = 30_000;

/**
 * Count of the user's in-flight runs, derived from the conversation list
 * (`latest_run_status ∈ active`) — the source PRD-H.5 prescribes for the rail
 * Run badge (PRD-C.2), rather than a bespoke endpoint. Best-effort and polled:
 * a fetch failure keeps the last known count so a transient blip never clears a
 * real badge. Returns 0 when signed out.
 */
export function useActiveRunCount(identity: RequestIdentity | null): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (identity === null) {
      setCount(0);
      return;
    }
    let cancelled = false;
    const tick = async (): Promise<void> => {
      try {
        const res = await listConversations(identity, { limit: 100 });
        if (cancelled) return;
        setCount(
          res.conversations.filter(
            (c) =>
              c.latest_run_status != null &&
              ACTIVE_RUN_STATUSES.has(c.latest_run_status),
          ).length,
        );
      } catch {
        // Best-effort badge — keep the last known count on a transient failure.
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [identity]);

  return count;
}
