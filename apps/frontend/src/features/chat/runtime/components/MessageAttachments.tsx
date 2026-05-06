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
