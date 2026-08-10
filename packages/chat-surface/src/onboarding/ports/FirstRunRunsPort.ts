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

import type { FilesystemBypassSelection } from "../../composer/filesystemBypass";

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
  /**
   * P4 — the composer Tools popover's per-run web-search toggle (SPEC `webOn`,
   * default true). Threaded onto the run so an explicit `false` omits the
   * built-in `web_search` tool for THIS run only (no regression to today's
   * always-on default). The host binder maps it onto the run body's
   * `web_search_enabled` field.
   */
  readonly webSearchEnabled: boolean;
  /**
   * P4 — per-run connector opt-OUTS from the Tools popover. Connected
   * connectors are live by default (that is what the popover now renders), so
   * this carries only the ones the user paused. The FTUE has no conversation at
   * toggle time, so the host seeds them into the created run's
   * `request_context.paused_connectors` — the field the runtime's MCP gate
   * reads — rather than PATCHing a not-yet-existent conversation. Omitted when
   * nothing is paused, which is the common case.
   */
  readonly pausedConnectorIds?: readonly string[];
  /**
   * PRD-FS-10 §4.3 — the composer bypass pill at send time. Omitted for the
   * default Manual posture and whenever the workspace master switch is off, so
   * an ordinary first run posts the byte-identical body it always did.
   *
   * The FTUE is where this matters most and where it was missing longest: the
   * first message of a chat is exactly when a user decides how much asking they
   * want, and the pill only existed from the second message onwards.
   *
   * ADVISORY, like every other carrier of this value. The runtime folds it
   * against the master switch it holds server-side and re-checks the grant
   * before skipping any approval pause; it can neither widen a grant nor
   * authorize a write.
   */
  readonly filesystemBypass?: FilesystemBypassSelection;
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
