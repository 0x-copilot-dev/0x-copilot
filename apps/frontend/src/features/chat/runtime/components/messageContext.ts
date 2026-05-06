import { createContext, useContext } from "react";
import type { ThreadMessageLike } from "../types";

/**
 * Shape every Atlas message component reads — message role, content,
 * attachments, status, metadata. Mirrors the relevant subset of
 * `ThreadMessageLike`. Components that need more than what's exposed here
 * should request a wider context shape rather than pass props through.
 */
export interface MessageContextValue {
  message: ThreadMessageLike;
  /**
   * Wired by the host (ChatScreen). Called by tool renderers when the
   * user resolves an interrupt — approval decision, MCP-auth choice,
   * ask-a-question answer. Payload shape is tool-specific; the host
   * dispatches by `approval_id` + `approval_kind`.
   */
  onResumeToolCall?: (payload: unknown) => void;
}

export const MessageContext = createContext<MessageContextValue | null>(null);

/** Hook that must be called inside a `<Message>` subtree. */
export function useMessage(): MessageContextValue {
  const ctx = useContext(MessageContext);
  if (ctx === null) {
    throw new Error(
      "useMessage must be used inside an Atlas <Message> subtree.",
    );
  }
  return ctx;
}
