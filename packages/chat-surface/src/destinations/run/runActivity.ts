import {
  ACTIVE_AGENT_RUN_STATUSES,
  type AgentRunStatus,
} from "@0x-copilot/api-types";

/**
 * The four non-terminal statuses, as a lookup. Built FROM the api-types SSOT
 * rather than retyped: this concept already existed twice (the array in
 * `@0x-copilot/api-types` and a hand-written `Set` in `useRunTranscript`, whose
 * comment cited a `useRunSession` const that no longer exists), and a third
 * hand-written copy is how the three drift apart.
 */
const ACTIVE: ReadonlySet<AgentRunStatus> = new Set(ACTIVE_AGENT_RUN_STATUSES);

/**
 * Is the run still capable of producing output?
 *
 * **`null` counts as active.** The status is unknown during the window between
 * binding a run and the first event that carries one, so callers must only
 * suppress live affordances on a POSITIVELY terminal status. Treating unknown
 * as terminal would blink the follow-live chip off at the start of every run —
 * exactly the moment it matters most.
 */
export function isRunActive(status: AgentRunStatus | null): boolean {
  return status === null || ACTIVE.has(status);
}
