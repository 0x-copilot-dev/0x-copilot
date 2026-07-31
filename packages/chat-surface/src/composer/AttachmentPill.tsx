import type { ReactElement, ReactNode } from "react";

/**
 * Attachment chip rendered in the composer's pill row and inside user
 * messages. The optional `onRemove` callback turns the chip into a
 * removable variant — clicking the × button calls it. Composer mounts
 * pass it; message-mounts (read-only history) omit it.
 *
 * `icon` and `removeLabel` exist for the pill's second tenant: a granted
 * folder, whose chip is the same object (a thing attached to this
 * conversation, dismissable by one control) but whose verb is not "remove".
 * Taking access away from the agent is a REVOKE, and a screen reader that
 * announced it as "Remove Downloads" would understate what the button does.
 */
export function AttachmentPill({
  attachment,
  onRemove,
  icon,
  removeLabel,
}: {
  attachment: { name: string; type: string };
  onRemove?: () => void;
  /** Leading glyph; omitted for file attachments (the name carries the type). */
  icon?: ReactNode;
  /** Accessible name for the × control. Defaults to `Remove <name>`. */
  removeLabel?: string;
}): ReactElement {
  return (
    <span className="aui-attachment-pill">
      {icon}
      <span>{attachment.name}</span>
      <small>{attachment.type}</small>
      {onRemove ? (
        <button
          type="button"
          className="aui-attachment-pill__remove"
          aria-label={removeLabel ?? `Remove ${attachment.name}`}
          onClick={onRemove}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}
