// Web `FirstRunRunsPort` — the two-step first-run create over the typed
// `api/agentApi` module (the sanctioned frontend seam; features never touch
// the Transport singleton directly — see apps/frontend/eslint.config.js).
//
// chat-surface stays substrate-clean (it never calls fetch/IPC); this host
// implementation performs the two POSTs the port contract describes:
//   1. POST /v1/agent/conversations {title}                        → conversation_id
//   2. POST /v1/agent/runs {conversation_id, user_input, model, …} → run_id
// then returns `{conversationId, runId}` for the shell's Run cockpit to stream.
//
// Mirrors the desktop `createFirstRunRunsPort` (renderer/onboarding), but binds
// `api/agentApi.createConversation` / `createRun` instead of a raw Transport —
// the two hosts implement the same shared port over their own substrate seam.

import type {
  FirstRunCreateRunInput,
  FirstRunLaunchResult,
  FirstRunRunsPort,
} from "@0x-copilot/chat-surface";

import { createConversation, createRun } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";

/** First-run conversation title, derived from the composed prompt (SPEC: a
 *  meaningful chat name). Falls back to a neutral label for an attachment-only
 *  send. Truncated to 60 chars (matches the desktop `firstRunTitle`). */
function firstRunTitle(userInput: string): string {
  const trimmed = userInput.trim();
  return trimmed.length > 0 ? trimmed.slice(0, 60) : "First run";
}

/**
 * Build the web `FirstRunRunsPort` bound to the signed-in `identity`. The port
 * only CREATES the run (no streaming) — the handoff target (`RunDestination` /
 * `useRunSession`) opens the SSE tail after handoff. Identity is threaded
 * through the typed api client; the facade still derives the authoritative
 * (org, user) from the verified bearer.
 */
export function createFirstRunRunsPort(
  identity: RequestIdentity,
): FirstRunRunsPort {
  return {
    async createFirstRun(
      input: FirstRunCreateRunInput,
    ): Promise<FirstRunLaunchResult> {
      const conversation = await createConversation(identity, {
        title: firstRunTitle(input.userInput),
      });
      const conversationId = conversation.conversation_id;
      if (conversationId === "") {
        // A conversation with no id can't anchor a run — fail loudly so the
        // launch hook surfaces a StartRunError rather than posting a run into
        // the void.
        throw new Error(
          "first-run: conversation create returned no conversation_id",
        );
      }

      const run = await createRun(conversationId, input.userInput, identity, {
        model: input.model,
        // P4: the FTUE Tools-popover web-search toggle. Threaded top-level on
        // the run body (the field the backend actually reads); `createRun`
        // omits it when true so the always-on default is preserved.
        webSearchEnabled: input.webSearchEnabled,
        attachments:
          input.attachments !== undefined && input.attachments.length > 0
            ? [...input.attachments]
            : undefined,
      });

      return { conversationId, runId: run.run_id ?? "" };
    },
  };
}
