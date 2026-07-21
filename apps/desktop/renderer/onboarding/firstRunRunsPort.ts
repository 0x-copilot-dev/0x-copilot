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
      const conversation =
        await transport.request<CreateConversationResponseLite>({
          method: "POST",
          path: "/v1/agent/conversations",
          body: { title: firstRunTitle(input.userInput) },
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
      };
      if (input.model !== null) {
        body.model = input.model;
      }
      if (input.attachments !== undefined && input.attachments.length > 0) {
        body.attachments = input.attachments;
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
