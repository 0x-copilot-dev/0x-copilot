// projectChatMessages — pure selector: the CURRENT run's live assistant turn,
// as an ORDERED list of parts keyed by `sequence_no`.
//
// The Run cockpit reads exactly one event source (useRunSession.events, FR-3.3).
// subagents + approvals already project off it via pure selectors; chat did NOT
// — TcChat rendered a stale one-time GET and the streamed reply was dropped.
// This selector closes that gap for the assistant side of the transcript.
//
// SCOPE — this owns ONLY the in-flight assistant turn of the active run:
//   - user turns + prior-run turns come from the persisted `/messages` history
//     (the run stream carries no user_message event and resets per run), so this
//     selector never emits user messages — `useRunTranscript` merges the two.
//   - only MAIN-agent deltas (event.subagent_id == null) become the chat bubble;
//     subagent streams belong to the Agents tab, not the reply.
//
// THE ORDERING INVARIANT (the bug this file used to be)
// -----------------------------------------------------
// A turn is `text → tools → text → tools → text`, not `{one text}{one
// reasoning}`. This fold used to keep ONE accumulator per KIND: every
// `model_delta` in the run concatenated into a single `text` string, every
// reasoning delta into a single `reasoning` string, and then `final_response`
// *replaced* the text accumulator outright. Two consequences, both shipped:
//
//   1. text the model emitted BEFORE a tool call was overwritten by the text it
//      emitted after — not misplaced, destroyed;
//   2. the turn carried one anchor (its first token), so every tool / fleet /
//      approval card that ran mid-turn sorted after the whole bubble.
//
// The ledger was never wrong: every frame carries a monotonic `sequence_no` and
// the terminal event seals `[1..N]`. The order was thrown away HERE. So:
//
//   - walk events in `sequence_no` order;
//   - hold at most ONE open part, and close it the moment an event of a
//     different kind arrives (`PART_BREAKING_EVENT_TYPES` = the events that
//     render as their own card, i.e. the model stopped talking and acted);
//   - a delta arriving with no matching open part OPENS A NEW ONE at that seq —
//     this is the whole fix: text after a tool call is a new part, not an
//     append to the part above the tool card;
//   - `reasoning_summary` caps the part that is OPEN, never `findIndex(first)`;
//   - `final_response` reconciles the LAST text part only, never the accumulator.
//
// Every part carries the `seq` it opened at, so `TcChat` can interleave the
// cards between parts by that one total order instead of guessing from
// wall-clock timestamps.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import type { MessagePartStatus } from "../../messages/types";
import type {
  TcChatMessage,
  TcChatMessagePart,
} from "../../thread-canvas/TcChat";

/**
 * Events that end an open text/reasoning part.
 *
 * Deliberately a closed set rather than "any non-delta event": closing on an
 * incidental frame (a heartbeat, `run_started`, a todo snapshot) would split a
 * sentence — or worse, a GFM table — across two `MarkdownText` parts that each
 * parse as half a document. These are exactly the events that render as their
 * own item in the transcript, so a break here is a break the user can see.
 */
const PART_BREAKING_EVENT_TYPES: ReadonlySet<string> = new Set([
  "tool_call_started",
  "tool_call_completed",
  "tool_result",
  "approval_requested",
  "approval_resolved",
  "mcp_auth_required",
  "subagent_started",
  "subagent_completed",
  "subagent_fleet_started",
  "subagent_fleet_finished",
]);

/**
 * Read the streamed text chunk off an event payload, else "".
 *
 * The runtime does NOT put the chunk under `text`: `model_delta` carries it as
 * `payload.delta` (with a duplicate `message`), and `reasoning_summary_delta`
 * carries it as `payload.delta` too (alongside a cumulative `summary`). Reading
 * only `text` folded every delta to "" — so live token streaming AND the
 * reasoning stream never rendered; text appeared only at `final_response`.
 * Resolve `text` (legacy/other events) → `message` → `delta` (the streamed
 * chunk). We do NOT read `message` on `final_response` as a structured object:
 * there it is the final string the worker wrote (`{MESSAGE: final_text}`).
 */
function payloadText(event: RuntimeEventEnvelope): string {
  const payload = event.payload;
  if (payload !== null && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    // `message` is what `RuntimeTextPayload` actually declares, and what the
    // worker writes: `final_payload = {MESSAGE: final_text}` and the same
    // `final_text` is passed as the event summary
    // (`runtime_worker/handlers/run.py:735-779`).
    //
    // `text` is read FIRST only for historical tolerance — no runtime event
    // has ever carried it.
    for (const key of ["text", "message", "delta"] as const) {
      const value = record[key];
      if (typeof value === "string" && value !== "") {
        return value;
      }
    }
  }
  return "";
}

/** The cumulative reasoning text on a `reasoning_summary` cap, if any. */
function reasoningSummaryText(event: RuntimeEventEnvelope): string {
  const payload = event.payload;
  if (payload !== null && typeof payload === "object") {
    const value = (payload as Record<string, unknown>).summary;
    if (typeof value === "string" && value !== "") {
      return value;
    }
  }
  return payloadText(event) || event.summary || "";
}

/** A part under construction. Mutable by design — the fold is the only writer. */
interface DraftPart {
  type: "text" | "reasoning";
  text: string;
  /** `sequence_no` of the event that OPENED this part — its anchor. */
  readonly seq: number;
  readonly startedAtMs?: number;
  updatedAtMs?: number;
  /** Closed parts never accept another delta; a later delta opens a new part. */
  closed: boolean;
}

function parseMs(value: string | undefined): number | undefined {
  if (value === undefined) {
    return undefined;
  }
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? undefined : ms;
}

/**
 * Project the active run's events into the live assistant turn (0 or 1 message,
 * with N ordered parts).
 * Returns `[]` until the main agent has produced text or reasoning.
 */
export function projectChatMessages(
  events: readonly RuntimeEventEnvelope[],
): TcChatMessage[] {
  const drafts: DraftPart[] = [];
  // Index into `drafts` rather than a reference: the open part is mutated from
  // inside closures, and TypeScript's control-flow analysis cannot follow that
  // through a `DraftPart | null` binding (it narrows to `never` downstream).
  let openIndex = -1;
  let firstSeq: number | null = null;
  let createdAt: string | undefined;
  let finalized = false;
  let messageId: string | null = null;
  let runId: string | null = null;
  // True when a card has landed since the last prose arrived. The terminal text
  // may only settle INTO the tail text part when this is false — otherwise a run
  // that ends right after a tool call would reconcile its answer into the
  // sentence the model spoke BEFORE the call, which is the original overwrite
  // bug wearing a smaller hat.
  let cardSinceProse = false;
  const seen = new Set<string>();

  // The stream is append-only in arrival order, which is `sequence_no` order in
  // practice — but a replay tail stitched onto a live subscription can arrive
  // out of order, and the whole fix rests on this being the true total order.
  // Sorting is O(n log n) on an already-sorted array and costs nothing real.
  const ordered = [...events].sort((a, b) => a.sequence_no - b.sequence_no);

  /** Close the open part, if any. Idempotent. */
  const closeOpen = (): void => {
    if (openIndex !== -1) {
      drafts[openIndex].closed = true;
      openIndex = -1;
    }
  };

  /** Open (or reuse) a part of `type`, appending `delta` to it. */
  const appendDelta = (
    type: "text" | "reasoning",
    delta: string,
    event: RuntimeEventEnvelope,
  ): void => {
    const at = parseMs(event.created_at);
    if (openIndex === -1 || drafts[openIndex].type !== type) {
      // A different kind was open (or nothing was) — the previous part ended.
      closeOpen();
      drafts.push({
        type,
        text: delta,
        seq: event.sequence_no,
        ...(at !== undefined ? { startedAtMs: at, updatedAtMs: at } : {}),
        closed: false,
      });
      openIndex = drafts.length - 1;
      return;
    }
    drafts[openIndex].text += delta;
    if (at !== undefined) {
      drafts[openIndex].updatedAtMs = at;
    }
  };

  for (const event of ordered) {
    // Subagent streams surface in the Agents tab, never the main reply bubble.
    if (event.subagent_id != null) {
      continue;
    }
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);
    if (runId === null) {
      runId = event.run_id;
    }

    if (PART_BREAKING_EVENT_TYPES.has(event.event_type)) {
      // The model stopped talking and acted. Whatever was open ended here; the
      // next delta opens a NEW part, which is what lets text render on both
      // sides of the card this event produces.
      closeOpen();
      cardSinceProse = true;
      continue;
    }

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
        appendDelta("text", delta, event);
        cardSinceProse = false;
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
        appendDelta("reasoning", delta, event);
        cardSinceProse = false;
        break;
      }
      case "reasoning_summary": {
        // The provider's explicit close marker. It carries the CUMULATIVE
        // summary, so it replaces the text of the part that is OPEN — the old
        // fold wrote it into `findIndex(isReasoningPart)`, i.e. the FIRST
        // reasoning part in the turn, destroying span #1 when span #2 capped.
        const text = reasoningSummaryText(event);
        if (openIndex !== -1 && drafts[openIndex].type === "reasoning") {
          const draft = drafts[openIndex];
          if (text !== "") {
            draft.text = text;
          }
          draft.updatedAtMs = parseMs(event.created_at) ?? draft.updatedAtMs;
          closeOpen();
          break;
        }
        if (text === "") {
          break;
        }
        if (firstSeq === null) {
          firstSeq = event.sequence_no;
          createdAt = event.created_at;
        }
        const at = parseMs(event.created_at);
        drafts.push({
          type: "reasoning",
          text,
          seq: event.sequence_no,
          ...(at !== undefined ? { startedAtMs: at, updatedAtMs: at } : {}),
          closed: true,
        });
        break;
      }
      case "final_response": {
        finalized = true;
        closeOpen();
        messageId = event.event_id;
        createdAt = createdAt ?? event.created_at;
        if (firstSeq === null) {
          firstSeq = event.sequence_no;
        }
        // Canonical resolution mirrors projectChatEntry: payload text ?? summary.
        // It is the LAST assistant turn's text, so it reconciles the LAST text
        // part — never the whole accumulator (that is what deleted every
        // pre-tool-call sentence in the turn).
        const text = payloadText(event) || event.summary || "";
        if (text === "") {
          break;
        }
        let lastTextIndex = -1;
        for (let i = drafts.length - 1; i >= 0; i -= 1) {
          if (drafts[i].type === "text") {
            lastTextIndex = i;
            break;
          }
        }
        // Only reconcile when the tail text part is genuinely still the live
        // one. Once a card has landed since the last prose, the terminal text
        // belongs to a segment that never streamed and opens its OWN part —
        // otherwise a run ending immediately after a tool call would overwrite
        // the sentence spoken before that call.
        if (
          !cardSinceProse &&
          lastTextIndex === drafts.length - 1 &&
          lastTextIndex !== -1
        ) {
          drafts[lastTextIndex].text = text;
        } else {
          const at = parseMs(event.created_at);
          drafts.push({
            type: "text",
            text,
            seq: event.sequence_no,
            ...(at !== undefined ? { startedAtMs: at, updatedAtMs: at } : {}),
            closed: true,
          });
        }
        break;
      }
      default:
        break;
    }
  }

  const nonEmpty = drafts.filter((draft) => draft.text !== "");
  if (nonEmpty.length === 0) {
    return [];
  }

  const parts: TcChatMessagePart[] = nonEmpty.map((draft) => {
    const status: MessagePartStatus =
      draft.closed || finalized ? { type: "complete" } : { type: "running" };
    return {
      type: draft.type,
      text: draft.text,
      status,
      seq: draft.seq,
      ...(draft.startedAtMs !== undefined
        ? { startedAtMs: draft.startedAtMs }
        : {}),
      ...(draft.updatedAtMs !== undefined
        ? { updatedAtMs: draft.updatedAtMs }
        : {}),
    };
  });

  const parsedMs = createdAt !== undefined ? Date.parse(createdAt) : Number.NaN;
  return [
    {
      message_id: messageId ?? `run-assistant-${firstSeq ?? 0}`,
      role: "assistant",
      parts,
      ...(runId !== null ? { run_id: runId } : {}),
      created_at_ms: Number.isNaN(parsedMs) ? undefined : parsedMs,
    },
  ];
}
