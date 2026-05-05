import { useEffect, useRef, useState, type ReactElement } from "react";

export interface ConversationTitleProps {
  title: string | null;
  onRename?: (next: string) => Promise<void> | void;
  disabled?: boolean;
}

const FALLBACK_TITLE = "Untitled chat";

/**
 * One-line conversation title. Read-only by default; double-click enters
 * inline edit mode and Enter commits via the optional `onRename`
 * callback (PR 1.6 PATCH). Escape cancels. The PATCH endpoint already
 * exists; this component only owns the editing affordance.
 */
export function ConversationTitle({
  title,
  onRename,
  disabled,
}: ConversationTitleProps): ReactElement {
  const display = title?.trim() ? title : FALLBACK_TITLE;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(display);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) {
      setDraft(display);
    }
  }, [display, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const canEdit = Boolean(onRename) && !disabled;

  const commit = async (): Promise<void> => {
    setEditing(false);
    const trimmed = draft.trim();
    if (!onRename || trimmed === (title ?? "").trim()) {
      return;
    }
    await onRename(trimmed);
  };

  if (!editing) {
    return (
      <h1
        className="atlas-title"
        title={display}
        onDoubleClick={() => {
          if (canEdit) {
            setEditing(true);
          }
        }}
        aria-label={
          canEdit
            ? "Conversation title — double-click to rename"
            : "Conversation title"
        }
      >
        {display}
      </h1>
    );
  }

  return (
    <input
      ref={inputRef}
      className="atlas-title atlas-title--editing"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        void commit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void commit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          setDraft(display);
          setEditing(false);
        }
      }}
      aria-label="Edit conversation title"
    />
  );
}
