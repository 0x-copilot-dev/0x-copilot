// projectChatMessages — pure selector: the CURRENT run's live assistant reply.
//
// The Run cockpit reads exactly one event source (useRunSession.events, FR-3.3).
// subagents + approvals already project off it via pure selectors; chat did NOT
// — TcChat rendered a stale one-time GET and the streamed reply was dropped.
// This selector closes that gap for the assistant side of the transcript.
//
// SCOPE — this owns ONLY the in-flight assistant message of the active run:
//   - user turns + prior-run turns come from the persisted `/messages` history
//     (the run stream carries no user_message event and resets per run), so this
//     selector never emits user messages — `useRunTranscript` merges the two.
//   - only MAIN-agent deltas (event.subagent_id == null) become the chat bubble;
//     subagent streams belong to the Agents tab, not the reply.
//
// COALESCING — the runtime emits one `model_delta` per token; this concatenates
// the run of deltas into ONE assistant message. `reasoning_summary_delta` folds
// into a separate `reasoning` part. While no `final_response` has landed the
// parts carry `status:{type:"running"}` (drives the streaming cursor); the
// terminal `final_response` flips them to `complete` and supplies the canonical
// text (payload.text ?? summary), matching projectChatEntry's own resolution.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import type { MessagePartStatus } from "../../messages/types";
import type {
  TcChatMessage,
  TcChatMessagePart,
} from "../../thread-canvas/TcChat";

/**
 * Read the streamed text chunk off an event payload, else "".
 *
 * The runtime does NOT put the chunk under `text`: `model_delta` carries it as
 * `payload.delta` (with a duplicate `message`), and `reasoning_summary_delta`
 * carries it as `payload.delta` too (alongside a cumulative `summary`). Reading
 * only `text` folded every delta to "" — so live token streaming AND the
 * reasoning stream never rendered; text appeared only at `final_response`.
 * Resolve `text` (legacy/other events) → `delta` (the streamed chunk). We do
 * NOT read `message` here: on `final_response` that key is a structured message
 * object, not a string (final_response text still resolves via `event.summary`).
 */
function payloadText(event: RuntimeEventEnvelope): string {
  const payload = event.payload;
  if (payload !== null && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of ["text", "delta"] as const) {
      const value = record[key];
      if (typeof value === "string" && value !== "") {
        return value;
      }
    }
  }
  return "";
}

/**
 * Project the active run's events into the live assistant message (0 or 1).
 * Returns `[]` until the main agent has produced text or reasoning.
 */
export function projectChatMessages(
  events: readonly RuntimeEventEnvelope[],
): TcChatMessage[] {
  let text = "";
  let reasoning = "";
  let firstSeq: number | null = null;
  let createdAt: string | undefined;
  let finalized = false;
  let messageId: string | null = null;
  const seen = new Set<string>();

  for (const event of events) {
    // Subagent streams surface in the Agents tab, never the main reply bubble.
    if (event.subagent_id != null) {
      continue;
    }
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);

    switch (event.event_type) {
      case "model_delta": {
        const delta = payloadText(event);
        if (delta === "") {
          break;
        }
        if (firstSeq === null) {
          firstSeq = event.sequence_no;
          createdAt = event.created_at;
        }
        text += delta;
        break;
      }
      case "reasoning_summary_delta": {
        const delta = payloadText(event);
        if (delta === "") {
          break;
        }
        if (firstSeq === null) {
          firstSeq = event.sequence_no;
          createdAt = event.created_at;
        }
        reasoning += delta;
        break;
      }
      case "final_response": {
        finalized = true;
        // Canonical resolution mirrors projectChatEntry: payload.text ?? summary.
        text = payloadText(event) || event.summary || text;
        messageId = event.event_id;
        createdAt = createdAt ?? event.created_at;
        break;
      }
      default:
        break;
    }
  }

  if (text === "" && reasoning === "") {
    return [];
  }

  const status: MessagePartStatus = finalized
    ? { type: "complete" }
    : { type: "running" };
  const parts: TcChatMessagePart[] = [];
  if (reasoning !== "") {
    parts.push({ type: "reasoning", text: reasoning, status });
  }
  if (text !== "" || parts.length === 0) {
    parts.push({ type: "text", text, status });
  }

  const parsedMs = createdAt !== undefined ? Date.parse(createdAt) : Number.NaN;
  return [
    {
      message_id: messageId ?? `run-assistant-${firstSeq ?? 0}`,
      role: "assistant",
      parts,
      created_at_ms: Number.isNaN(parsedMs) ? undefined : parsedMs,
    },
  ];
}
