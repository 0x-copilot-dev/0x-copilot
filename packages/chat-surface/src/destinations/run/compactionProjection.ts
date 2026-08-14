// projectCompactionNotices — the transcript's compaction boundaries, projected
// off the SAME `session.events` every other cockpit selector reads (FR-3.3: one
// event source, pure selectors over it, no second subscription or projector).
//
// WHAT THIS EXISTS FOR
// --------------------
// The runtime has bounded oversized tool results out of model context for a long
// time (`agent_runtime.context.tool_result_admission`): the bytes get parked in
// the object store and the model is handed a bounded preview in their place. The
// user could only ever observe the CONSEQUENCE — the agent not knowing something
// it had "already read" — because the transcript said nothing about the moment
// it happened. `compression_note` is that moment, emitted beside the
// `tool_result` event it describes and inside the run's causal prefix.
//
// So this is a BOUNDARY MARKER, not a card. It says "the model stopped holding
// all of this from here"; there is nothing to decide, expand or act on.
//
// THE LABEL IS THE SERVER'S, NOT OURS
// -----------------------------------
// `display_title` is projected server-side from the same typed counts the
// producer validated (`RuntimeEventPresentation._display_title_for` →
// `Messages.Event.compaction_title`), so the sentence the reader gets and the
// numbers beside it cannot disagree, and no client derives a timeline label from
// an event-name prefix. We read the counts too, but only to draw the quiet
// `12.4k → 380` detail — never to re-word the title.
//
// MATERIALITY IS RE-CHECKED HERE
// ------------------------------
// `CompactionNotice.is_material` already refuses to emit a note that compacted
// nothing, so in practice every note on the wire saved tokens. The guard is
// repeated because this projection also runs over replayed history, where a
// note written before that rule existed would otherwise draw a divider across
// the transcript announcing that nothing happened.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

/** The wire event this selector folds. */
const COMPACTION_EVENT_TYPE = "compression_note";

/**
 * Neutral fallback for a note whose server projection carried no title. The
 * counts still render beside it, so the row is never contentless.
 */
const FALLBACK_LABEL = "Compacted tool output";

/** One compaction boundary, ready to interleave into the transcript. */
export interface CompactionNoticeEntry {
  /** The `compression_note` event id — the row's stable key. */
  readonly eventId: string;
  /** The run the note belongs to; `null` when the envelope named none. */
  readonly runId: string | null;
  /**
   * `sequence_no` of the note. The transcript interleave is ordered on this and
   * nothing else — the note is emitted in the same async pass as the
   * `tool_result` it describes, so its seq is the exact point in the turn where
   * the model's view of that result narrowed.
   */
  readonly seq: number;
  /** The server-projected line. Never re-derived here. */
  readonly label: string;
  /** Estimated tokens the compaction kept out of model context. Always > 0. */
  readonly tokensSaved: number;
  /** Serialized size of the source, in estimated tokens. */
  readonly beforeTokens: number | null;
  /** Size of what the model was handed instead. */
  readonly afterTokens: number | null;
  /** The tool whose result was compacted, when the call was identified. */
  readonly toolName: string | null;
}

/** Read a non-negative integer off a payload, else `null`. Rejects booleans. */
function readCount(
  payload: Record<string, unknown>,
  key: string,
): number | null {
  const value = payload[key];
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (!Number.isInteger(value) || value < 0) return null;
  return value;
}

/** Read a non-empty string off a payload, else `null`. */
function readText(
  payload: Record<string, unknown>,
  key: string,
): string | null {
  const value = payload[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * Project every material compaction note on `events`, in `sequence_no` order.
 *
 * Deduped by `event_id`: a replay tail stitched onto a live subscription can
 * deliver the same envelope twice, and two dividers for one compaction reads as
 * two compactions.
 */
export function projectCompactionNotices(
  events: readonly RuntimeEventEnvelope[],
): readonly CompactionNoticeEntry[] {
  const byEventId = new Map<string, CompactionNoticeEntry>();
  for (const event of events) {
    if (event.event_type !== COMPACTION_EVENT_TYPE) continue;
    if (byEventId.has(event.event_id)) continue;
    const payload =
      event.payload !== null && typeof event.payload === "object"
        ? (event.payload as Record<string, unknown>)
        : {};
    const beforeTokens = readCount(payload, "before_tokens");
    const afterTokens = readCount(payload, "after_tokens");
    // The producer derives `tokens_saved` from the two counts it prints beside
    // it, so prefer the number it actually sent; fall back to the difference
    // only when it is absent, and never invent one from a single count.
    const saved =
      readCount(payload, "tokens_saved") ??
      (beforeTokens !== null && afterTokens !== null
        ? Math.max(beforeTokens - afterTokens, 0)
        : null);
    if (saved === null || saved <= 0) continue;
    byEventId.set(event.event_id, {
      eventId: event.event_id,
      runId: event.run_id ?? null,
      seq: event.sequence_no,
      label: event.display_title ?? FALLBACK_LABEL,
      tokensSaved: saved,
      beforeTokens,
      afterTokens,
      toolName: readText(payload, "tool_name"),
    });
  }
  return [...byEventId.values()].sort((left, right) => left.seq - right.seq);
}
