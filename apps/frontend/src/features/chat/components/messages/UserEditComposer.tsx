import { ComposerPrimitive } from "@assistant-ui/react";
import type { ReactElement } from "react";

/**
 * Inline edit composer for a user message. The outer wrapper is a plain
 * div — it carries only the visual class. The inner `ComposerPrimitive`
 * is what owns edit state today (replaced in Phase 4 of the
 * `@assistant-ui/react` migration).
 */
export function UserEditComposer(): ReactElement {
  return (
    <div className="aui-message aui-message--user">
      <ComposerPrimitive.Root className="aui-edit-composer">
        <ComposerPrimitive.Input
          className="aui-composer__input"
          aria-label="Edit message"
          maxRows={8}
          submitMode="enter"
        />
        <div className="aui-edit-composer__actions">
          <ComposerPrimitive.Cancel
            className="aui-ghost-button"
            title="Cancel editing"
          >
            Cancel
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send
            className="aui-send-button"
            title="Save edited message"
          >
            Save
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </div>
  );
}
