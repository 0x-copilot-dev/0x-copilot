// FirstRunRunsPort — the host-injected two-step first-run create (PRD-P3 §3.1).
//
// chat-surface stays substrate-clean: it never calls `fetch`/IPC directly. The
// HOST implements this port over its Transport:
//   1. POST /v1/agent/conversations                                   → conversation_id
//   2. POST /v1/agent/runs {conversation_id, user_input, model, …}    → run_id
//
// Identity is server-derived (the facade overrides org/user) — the surface
// never sends identity. `stream` is intentionally ABSENT: the handoff target
// (RunDestination / useRunSession) opens the SSE tail after handoff, so the
// first-run port only needs to CREATE the run, not stream it.

import type {
  ModelSelectionRequest,
  RunAttachmentRequest,
} from "@0x-copilot/api-types";

export interface FirstRunCreateRunInput {
  /** The composed prompt (chip prompt or typed text). */
  readonly userInput: string;
  /** Resolved model selection, or null to let the runtime default. */
  readonly model: ModelSelectionRequest | null;
  /**
   * Client-inline attachments (the CSV chip → one attachment carrying an
   * inline `text` content part so the runtime worker actually reads the rows;
   * a base64 `file` part is summarised by name/size only — model-invisible).
   */
  readonly attachments?: readonly RunAttachmentRequest[];
}

export interface FirstRunLaunchResult {
  readonly conversationId: string;
  readonly runId: string;
}

/**
 * The two-step first-run create. The host implements it over its Transport.
 * Kept minimal (create only) so the surface can hand the created run off to the
 * shell's Run cockpit, which owns streaming.
 */
export interface FirstRunRunsPort {
  createFirstRun(input: FirstRunCreateRunInput): Promise<FirstRunLaunchResult>;
}
