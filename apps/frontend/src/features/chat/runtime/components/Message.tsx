import type { ReactElement, ReactNode } from "react";
import { MessageContext, type MessageContextValue } from "./messageContext";
import type { ThreadMessageLike } from "../types";

/**
 * Props-driven message root. Replaces `MessagePrimitive.Root` from
 * `@assistant-ui/react`. The host (`ThreadBody`) walks the thread's
 * message list and renders one `<Message>` per item, passing the
 * `ChatThreadMessage` value plus any callbacks tools need.
 *
 * Owns no streaming logic — `message.status` and `message.content` are
 * snapshots from the host's reducer. When a delta arrives the host
 * re-renders with a new `message` object.
 */
export interface MessageProps {
  message: ThreadMessageLike;
  className?: string;
  children?: ReactNode;
  /**
   * Forwarded into context for descendants — tool renderers call this
   * when the user resolves an interrupt. See `MessageContextValue`.
   */
  onResumeToolCall?: MessageContextValue["onResumeToolCall"];
}

export function Message({
  message,
  className,
  children,
  onResumeToolCall,
}: MessageProps): ReactElement | null {
  // Defensive: the host occasionally renders this Provider before the
  // upstream message snapshot has resolved (HMR transition, race on
  // initial mount). Bail rather than poison the context with
  // `message: undefined`, which crashes every descendant that calls
  // `useMessage()` and reads `message.<field>`.
  if (!message) {
    if (import.meta.env?.DEV) {
      // eslint-disable-next-line no-console
      console.warn("Message: rendered with undefined message; skipping.");
    }
    return null;
  }
  return (
    <MessageContext.Provider value={{ message, onResumeToolCall }}>
      <div className={className}>{children}</div>
    </MessageContext.Provider>
  );
}
