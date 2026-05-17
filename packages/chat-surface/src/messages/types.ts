// Frontend-domain types for message rendering. These are not backend
// contract types (the backend speaks RuntimeEventEnvelope in api-types);
// they describe how the chat surface models a streaming message in the
// UI. Both substrates render the same shapes; the domain layer above
// the transport projects backend events into these.

/**
 * Status of an individual message part. Distinct from a top-level
 * MessageStatus because the part-level "requires-action" reason is
 * constrained to "interrupt" — the runtime only stalls a part for an
 * interrupt; other stop-reasons live at the message level.
 */
export type MessagePartStatus =
  | { readonly type: "running" }
  | { readonly type: "complete"; readonly reason?: string }
  | {
      readonly type: "incomplete";
      readonly reason?: string;
      readonly error?: unknown;
    }
  | { readonly type: "requires-action"; readonly reason?: "interrupt" };

export interface TextMessagePart {
  readonly type: "text";
  readonly text: string;
  readonly status?: MessagePartStatus;
}

export interface ReasoningMessagePart {
  readonly type: "reasoning";
  readonly text: string;
  readonly status?: MessagePartStatus;
  /**
   * `event.created_at` (epoch ms) of the first delta/cap that contributed
   * to this part. Together with `updatedAtMs` it lets the
   * `<ReasoningGroup>` render the "Thought process · Ns" elapsed-time
   * stamp without an additional event-time clock per message. Optional
   * because pre-PR-3.6 messages and synthesised parts may lack one.
   */
  readonly startedAtMs?: number;
  /**
   * `event.created_at` (epoch ms) of the latest event applied to this
   * part. While the part is `running` the difference between
   * `updatedAtMs` and `startedAtMs` is the live "Thinking… · Ns"
   * counter; once `complete`, it freezes the final span length.
   */
  readonly updatedAtMs?: number;
}

/**
 * Per-part state synthesised by the runtime before handing parts to a
 * renderer. `status` is required at this layer — the walker fills it in
 * (defaulting to "complete" or "running" depending on whether a tool-call
 * already has a result).
 */
export interface MessagePartState {
  readonly status: MessagePartStatus;
}

export type TextMessagePartProps = MessagePartState & TextMessagePart;
export type ReasoningMessagePartProps = MessagePartState & ReasoningMessagePart;
