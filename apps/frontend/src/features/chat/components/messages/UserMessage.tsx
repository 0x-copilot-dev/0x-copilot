import { PlainText } from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";
import {
  Message,
  MessageAttachments,
  MessageParts,
} from "../../runtime/components";
import type { ThreadMessageLike } from "../../runtime/types";
import { AttachmentPill } from "../composer/AttachmentPill";

export function UserMessage({
  message,
}: {
  message: ThreadMessageLike;
}): ReactElement {
  return (
    <Message message={message} className="aui-message aui-message--user">
      <div className="aui-message__body">
        <MessageAttachments>
          {({ attachment }) => <AttachmentPill attachment={attachment} />}
        </MessageAttachments>
        <MessageParts components={{ Text: PlainText }} />
      </div>
    </Message>
  );
}
