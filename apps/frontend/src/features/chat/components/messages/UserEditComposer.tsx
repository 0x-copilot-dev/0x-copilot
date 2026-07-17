import type { ReactElement } from "react";
import { Composer } from "@0x-copilot/chat-surface";
import type { ThreadMessageLike } from "../../runtime/types";

/**
 * Inline edit composer for a user message. Renders the chat-surface
 * `<Composer>` in `mode="edit"` so the textarea, ⏎-saves, ⇧+⏎-newline,
 * Esc-cancels, auto-focus + select-all behaviour all flow through the
 * single monorepo Composer. The host seeds `initialText` from the
 * message and supplies save / cancel callbacks.
 *
 * Behaviour parity with the previous runtime EditComposer:
 *  - ⏎ saves; ⇧+⏎ adds a newline.
 *  - Esc cancels (chat-surface Composer wires Escape → onCancel when
 *    `mode === "edit"`).
 *  - The chat-surface Composer auto-sizes the textarea using its own
 *    minRows/maxRows clamp — no auto-focus / select-all by default,
 *    but the user clicks into the row before editing in every host
 *    flow we ship.
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
    <div className="aui-message aui-message--user">
      <div className="aui-edit-composer">
        <Composer
          mode="edit"
          initialText={textFromMessage(message)}
          onCancel={onCancel}
          onSave={onSave}
        />
      </div>
    </div>
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
