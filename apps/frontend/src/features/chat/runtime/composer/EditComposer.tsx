import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type ReactElement,
} from "react";

/**
 * Inline edit composer for a user message. Replaces the
 * `MessagePrimitive` + `ComposerPrimitive` combo previously used by
 * `UserEditComposer`. The host controls cancel/save via callbacks; the
 * composer owns its local draft state seeded from the message's
 * current text.
 */
export function EditComposer({
  initialText,
  onCancel,
  onSave,
  maxRows = 8,
  className = "aui-message aui-message--user",
  composerClassName = "aui-edit-composer",
  inputClassName = "aui-composer__input",
  actionsClassName = "aui-edit-composer__actions",
  cancelClassName = "aui-ghost-button",
  saveClassName = "aui-send-button",
  cancelLabel = "Cancel",
  saveLabel = "Save",
}: {
  initialText: string;
  onCancel: () => void;
  onSave: (text: string) => void;
  maxRows?: number;
  className?: string;
  composerClassName?: string;
  inputClassName?: string;
  actionsClassName?: string;
  cancelClassName?: string;
  saveClassName?: string;
  cancelLabel?: string;
  saveLabel?: string;
}): ReactElement {
  const [text, setText] = useState(initialText);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow textarea up to maxRows. Same algorithm as the main
  // composer; kept inline because the edit composer doesn't use
  // attachments / dropzone / mic, so sharing felt heavier than
  // helpful.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight =
      parseFloat(window.getComputedStyle(el).lineHeight || "20") || 20;
    const max = lineHeight * maxRows + 16;
    el.style.height = `${Math.min(max, el.scrollHeight)}px`;
  }, [text, maxRows]);

  // Auto-focus + select-all on mount so the user can immediately
  // overwrite the existing text.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.select();
  }, []);

  const submit = useCallback((): void => {
    const trimmed = text.trim();
    if (trimmed.length === 0) return;
    onSave(trimmed);
  }, [onSave, text]);

  const handleKey = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>): void => {
      if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.nativeEvent.isComposing
      ) {
        event.preventDefault();
        submit();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
    },
    [onCancel, submit],
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>): void => {
      setText(event.target.value);
    },
    [],
  );

  return (
    <div className={className}>
      <div className={composerClassName}>
        <textarea
          ref={textareaRef}
          className={inputClassName}
          aria-label="Edit message"
          value={text}
          onChange={handleChange}
          onKeyDown={handleKey}
        />
        <div className={actionsClassName}>
          <button
            type="button"
            className={cancelClassName}
            title={cancelLabel}
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={saveClassName}
            title={saveLabel}
            onClick={submit}
            disabled={text.trim().length === 0}
          >
            {saveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
