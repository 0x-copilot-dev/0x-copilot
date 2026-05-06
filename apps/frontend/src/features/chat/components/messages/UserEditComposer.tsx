import type { ReactElement } from "react";
import { EditComposer } from "../../runtime/composer";
import type { ThreadMessageLike } from "../../runtime/types";

/**
 * Inline edit composer for a user message. Replaces the previous
 * `MessagePrimitive` + `ComposerPrimitive` implementation. Receives
 * the message being edited so it can seed the textarea with the
 * existing text, and the host's save / cancel callbacks.
 *
 * Behaviour parity with the assistant-ui edit composer:
 *  - ⏎ saves; ⇧+⏎ adds a newline.
 *  - Esc cancels.
 *  - Auto-focus + select-all on mount so the user can immediately
 *    overwrite the existing text.
 */
export function UserEditComposer({
  message,
  onCancel,
  onSave,
}: {
  message: ThreadMessageLike;
  onCancel: () => void;
  onSave: (text: string) => void;
}): ReactElement {
  return (
    <EditComposer
      initialText={textFromMessage(message)}
      onCancel={onCancel}
      onSave={onSave}
    />
  );
}

function textFromMessage(message: ThreadMessageLike): string {
  const content = message.content;
  if (typeof content === "string") return content;
  if (!content) return "";
  const parts: string[] = [];
  for (const part of content) {
    if (part.type === "text") {
      parts.push(part.text);
    }
  }
  return parts.join("\n");
}
