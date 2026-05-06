import type { ReactElement, ReactNode } from "react";
import type { ThreadMessageLike } from "../types";
import { useMessage } from "./messageContext";

type Attachment = NonNullable<ThreadMessageLike["attachments"]>[number];

/**
 * Iterates the message's attachments and calls `children({ attachment })`
 * once per entry. Replaces `MessagePrimitive.Attachments`.
 *
 * No-op when the message has no attachments — callers don't need to
 * conditionally render this; an empty list yields nothing.
 */
export function MessageAttachments({
  children,
}: {
  children: (props: { attachment: Attachment }) => ReactNode;
}): ReactElement | null {
  const { message } = useMessage();
  // Defensive: the runtime refactor occasionally renders descendants
  // before the Provider's `message` prop is non-null (HMR transition,
  // empty thread shell, optimistic wrappers). Render nothing rather
  // than crash the entire ErrorBoundary subtree.
  if (!message) {
    if (import.meta.env?.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        "MessageAttachments: <Message message={...}> Provider rendered with undefined message; skipping.",
      );
    }
    return null;
  }
  const attachments = message.attachments;
  if (!attachments || attachments.length === 0) {
    return null;
  }
  return (
    <>
      {attachments.map((attachment) => (
        <span key={attachment.id} className="aui-message__attachment-slot">
          {children({ attachment })}
        </span>
      ))}
    </>
  );
}
