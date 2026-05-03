import { MessagePrimitive } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { PlainText } from "../markdown/PlainText";
import { AttachmentPill } from "../composer/AttachmentPill";

export function UserMessage(): ReactElement {
  return (
    <MessagePrimitive.Root className="aui-message aui-message--user">
      <div className="aui-message__body">
        <MessagePrimitive.Attachments>
          {({ attachment }) => <AttachmentPill attachment={attachment} />}
        </MessagePrimitive.Attachments>
        <MessagePrimitive.Parts components={{ Text: PlainText }} />
      </div>
    </MessagePrimitive.Root>
  );
}
