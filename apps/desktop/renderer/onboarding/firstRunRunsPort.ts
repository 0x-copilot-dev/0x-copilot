// Desktop `FirstRunRunsPort` — the two-step first-run create over the Transport.
//
// chat-surface stays substrate-clean (it never calls IPC/fetch); this host
// implementation performs the two POSTs the port contract describes:
//   1. POST /v1/agent/conversations {title}                        → conversation_id
//   2. POST /v1/agent/runs {conversation_id, user_input, model, …} → run_id
// then returns `{conversationId, runId}` for the shell's Run cockpit to stream.
//
// Identity is server-derived (the facade injects org/user from the bearer), so —
// exactly like `RunBinder.handleStartRun` (destinationBinders.tsx) and the
// desktop conversation-create in destinationBinders — the body carries NO
// identity. Mirrors the desktop wire shapes verified there.

import type { Transport } from "@0x-copilot/chat-transport";
import type {
  FirstRunCreateRunInput,
  FirstRunLaunchResult,
  FirstRunRunsPort,
} from "@0x-copilot/chat-surface";

interface CreateConversationResponseLite {
  readonly conversation_id?: string;
}

interface CreateRunResponseLite {
  readonly run_id?: string;
}

/** First-run conversation title, derived from the composed prompt (SPEC: a
 *  meaningful chat name). Falls back to a neutral label for an attachment-only
 *  send. Truncated to 60 chars (matches the web `titleFromPrompt` heuristic). */
function firstRunTitle(userInput: string): string {
  const trimmed = userInput.trim();
  return trimmed.length > 0 ? trimmed.slice(0, 60) : "First run";
}

/**
 * Build the desktop `FirstRunRunsPort` bound to a Transport. The port only
 * CREATES the run (no streaming) — the handoff target (`RunDestination` /
 * `useRunSession`) opens the SSE tail after handoff.
 */
export function createFirstRunRunsPort(transport: Transport): FirstRunRunsPort {
  return {
    async createFirstRun(
      input: FirstRunCreateRunInput,
    ): Promise<FirstRunLaunchResult> {
      // `project_id` rides step 1, and only when the user picked one — an
      // absent key is the byte-identical body every unfiled first run has
      // always posted, so an unfiled FTUE send is unchanged.
      const conversationBody: Record<string, unknown> = {
        title: firstRunTitle(input.userInput),
      };
      if (input.projectId != null && input.projectId !== "") {
        conversationBody.project_id = input.projectId;
      }
      const conversation =
        await transport.request<CreateConversationResponseLite>({
          method: "POST",
          path: "/v1/agent/conversations",
          body: conversationBody,
        });
      const conversationId = conversation.conversation_id ?? "";
      if (conversationId === "") {
        // A conversation with no id can't anchor a run — fail loudly so the
        // launch hook surfaces a StartRunError rather than posting a run into
        // the void.
        throw new Error(
          "first-run: conversation create returned no conversation_id",
        );
      }

      const body: Record<string, unknown> = {
        conversation_id: conversationId,
        user_input: input.userInput,
        // P4 — per-run web-search toggle from the composer Tools popover. The
        // backend reads it TOP-LEVEL (`CreateRunRequest.web_search_enabled`,
        // runs.py) and threads it onto `AgentRuntimeContext`; default true
        // matches the historic always-on, an explicit false disables it for
        // this run only.
        web_search_enabled: input.webSearchEnabled,
      };
      if (input.model !== null) {
        body.model = input.model;
      }
      if (input.attachments !== undefined && input.attachments.length > 0) {
        body.attachments = input.attachments;
      }
      // P4 — connectors the user PAUSED in the Tools popover seed the run's
      // `request_context.paused_connectors` (no conversation exists to PATCH at
      // toggle time). Connected connectors are live by default, so this is sent
      // only when the user actually paused one — and it must be
      // `paused_connectors`, not an omission from `connector_scopes`: the MCP
      // gate (`McpPermissionPolicy.is_server_card_authorized`) reads this field
      // and nothing else for a per-run opt-out.
      if (
        input.pausedConnectorIds !== undefined &&
        input.pausedConnectorIds.length > 0
      ) {
        body.request_context = {
          paused_connectors: input.pausedConnectorIds,
        };
      }
      // PRD-FS-10 §4.3 — the composer bypass pill. Same wire field and same
      // snake_case shape `buildRunCreateBody` posts for the in-chat composer,
      // so the FTUE and the cockpit ask the runtime for the identical thing.
      // Absent for Manual / master-switch-off, keeping the ordinary first-run
      // body byte-identical.
      if (input.filesystemBypass !== undefined) {
        body.filesystem_bypass = input.filesystemBypass;
      }

      const run = await transport.request<CreateRunResponseLite>({
        method: "POST",
        path: "/v1/agent/runs",
        body,
      });

      return { conversationId, runId: run.run_id ?? "" };
    },
  };
}
