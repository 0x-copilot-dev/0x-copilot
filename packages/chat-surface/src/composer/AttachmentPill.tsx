import type { ReactElement } from "react";

/**
 * Attachment chip rendered in the composer's pill row and inside user
 * messages. The optional `onRemove` callback turns the chip into a
 * removable variant — clicking the × button calls it. Composer mounts
 * pass it; message-mounts (read-only history) omit it.
 */
export function AttachmentPill({
  attachment,
  onRemove,
}: {
  attachment: { name: string; type: string };
  onRemove?: () => void;
}): ReactElement {
  return (
    <span className="aui-attachment-pill">
      <span>{attachment.name}</span>
      <small>{attachment.type}</small>
      {onRemove ? (
        <button
          type="button"
          className="aui-attachment-pill__remove"
          aria-label={`Remove ${attachment.name}`}
          onClick={onRemove}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}
