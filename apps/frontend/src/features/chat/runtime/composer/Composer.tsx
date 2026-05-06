import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";

/**
 * Imperative handle exposed by the Composer. The host (`ChatScreen`)
 * uses it to programmatically set / get the composer text — needed for
 * the skill-picker insertion path that historically went through
 * `aui.composer().setText(...)`.
 */
export interface ComposerHandle {
  setText: (text: string) => void;
  appendText: (text: string) => void;
  getText: () => string;
  focus: () => void;
  /**
   * Adds a file as an attachment via the configured adapter. Returns
   * the resulting `PendingAttachment` (or null if the file is
   * unaccepted by the adapter).
   */
  addAttachment: (file: File) => Promise<PendingAttachment | null>;
  /**
   * Programmatic submit. Same path as ⏎ — finalises attachments,
   * clears state, dispatches `onSubmit`. Used by the Send button so
   * we don't rely on synthesised keyboard events that React's
   * synthetic-event system wouldn't see.
   */
  submit: () => Promise<void>;
}

export interface ComposerSubmitPayload {
  readonly text: string;
  readonly attachments: readonly CompleteAttachment[];
}

export interface ComposerProps {
  /** Disabled = textarea read-only and submit suppressed (no run). */
  disabled?: boolean;
  /**
   * Whether a run is in flight. Drives the Send → Stop button toggle.
   * The host distinguishes `disabled` (composer not usable, e.g. no
   * conversation) from `running` (composer is showing the stop UI).
   */
  running?: boolean;
  /** Adapter that turns Files into attachments. Required to attach. */
  attachmentAdapter?: AttachmentAdapter;
  /** Placeholder text. */
  placeholder?: string;
  /** Maximum textarea rows before scroll. Default 5. */
  maxRows?: number;
  /** Minimum textarea rows. Default 1. */
  minRows?: number;
  /**
   * Submission. The Composer collects the textarea value + finalised
   * attachments and hands them off; the host wraps them in the
   * `AppendMessage` shape it needs.
   */
  onSubmit: (payload: ComposerSubmitPayload) => void | Promise<void>;
  /** Stop-run handler. Wired to the Stop button when `running`. */
  onCancel?: () => void;
  /**
   * Free render slot below the textarea. Caller renders attachment
   * previews, plus-menu, connectors-trigger, model-pill, depth-control,
   * mic, etc. Receives runtime state so it can style accordingly.
   */
  bottomBar?: (state: {
    text: string;
    running: boolean;
    disabled: boolean;
    attachmentsCount: number;
  }) => ReactNode;
  /**
   * Free render slot above the textarea. Caller uses it for the
   * attachment-pill row (so it scrolls with content) plus optional
   * quote preview.
   */
  topBar?: (state: {
    attachments: readonly (PendingAttachment | CompleteAttachment)[];
    onRemove: (id: string) => void;
  }) => ReactNode;
  /** Hint row shown beneath the bottom bar. */
  hint?: ReactNode;
  /** Additional className on the outer wrapper. */
  className?: string;
}

/**
 * Atlas composer. Replaces the assistant-ui `ComposerPrimitive` family
 * with a single forwardRef component. The host owns runtime callbacks
 * (`onSubmit`, `onCancel`); the composer owns its local state (text,
 * attachments) and exposes an imperative handle for the few cases
 * (skill-picker insertion) that need to write to it from outside.
 */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(
  function Composer(
    {
      disabled = false,
      running = false,
      attachmentAdapter,
      placeholder = "Ask Atlas to find, summarize, or draft something for your team…",
      maxRows = 5,
      minRows = 1,
      onSubmit,
      onCancel,
      bottomBar,
      topBar,
      hint,
      className,
    },
    handleRef,
  ): ReactElement {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const [text, setText] = useState("");
    const [attachments, setAttachments] = useState<
      Array<PendingAttachment | CompleteAttachment>
    >([]);
    const [dragOver, setDragOver] = useState(false);
    const submittingRef = useRef(false);

    // Auto-resize textarea between minRows and maxRows.
    useEffect(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = "auto";
      const lineHeight =
        parseFloat(window.getComputedStyle(el).lineHeight || "20") || 20;
      const min = lineHeight * minRows;
      const max = lineHeight * maxRows + 16;
      const next = Math.min(max, Math.max(min, el.scrollHeight));
      el.style.height = `${next}px`;
    }, [text, minRows, maxRows]);

    const removeAttachment = useCallback(
      async (id: string): Promise<void> => {
        const target = attachments.find((a) => a.id === id);
        if (!target) return;
        setAttachments((current) => current.filter((a) => a.id !== id));
        if (attachmentAdapter && target) {
          try {
            await attachmentAdapter.remove(target);
          } catch {
            // The remove path is best-effort — adapter cleanup
            // failures shouldn't undo the local state update.
          }
        }
      },
      [attachmentAdapter, attachments],
    );

    const addFile = useCallback(
      async (file: File): Promise<PendingAttachment | null> => {
        if (!attachmentAdapter) return null;
        try {
          // `add` may return either a Promise<PendingAttachment> or an
          // AsyncGenerator (multi-stage uploads). Atlas's adapters all
          // return Promise<PendingAttachment>; collapse the union via a
          // best-effort await + type-narrow.
          const result = await attachmentAdapter.add({ file });
          if (
            result &&
            typeof result === "object" &&
            "id" in result &&
            "name" in result
          ) {
            const pending = result as PendingAttachment;
            setAttachments((current) => [...current, pending]);
            return pending;
          }
          return null;
        } catch {
          return null;
        }
      },
      [attachmentAdapter],
    );

    const submit = useCallback(async (): Promise<void> => {
      if (submittingRef.current) return;
      const trimmed = text.trim();
      if (trimmed.length === 0 && attachments.length === 0) return;
      submittingRef.current = true;
      try {
        // Finalise pending attachments via the adapter's `send` step.
        const finalised: CompleteAttachment[] = [];
        for (const attachment of attachments) {
          if (attachment.status.type === "complete") {
            finalised.push(attachment as CompleteAttachment);
            continue;
          }
          if (attachmentAdapter) {
            try {
              const completed = await attachmentAdapter.send(
                attachment as PendingAttachment,
              );
              finalised.push(completed);
            } catch {
              // Skip the failed attachment but proceed with the rest.
            }
          }
        }
        await onSubmit({ text: trimmed, attachments: finalised });
        setText("");
        setAttachments([]);
      } finally {
        submittingRef.current = false;
      }
    }, [attachmentAdapter, attachments, onSubmit, text]);

    useImperativeHandle(
      handleRef,
      () => ({
        setText: (next: string): void => {
          setText(next);
          // Defer to next paint so caret/selection lands on the new
          // value (otherwise focus snaps before the value updates).
          requestAnimationFrame(() => {
            textareaRef.current?.focus();
          });
        },
        appendText: (next: string): void => {
          setText((current) =>
            current.trimEnd() ? `${current.trimEnd()}\n${next}` : next,
          );
          requestAnimationFrame(() => {
            textareaRef.current?.focus();
          });
        },
        getText: (): string => text,
        focus: (): void => {
          textareaRef.current?.focus();
        },
        addAttachment: addFile,
        submit: submit,
      }),
      [addFile, submit, text],
    );

    const handleKey = useCallback(
      (event: KeyboardEvent<HTMLTextAreaElement>): void => {
        if (
          event.key === "Enter" &&
          !event.shiftKey &&
          !event.nativeEvent.isComposing
        ) {
          event.preventDefault();
          if (running || disabled) return;
          void submit();
        }
      },
      [disabled, running, submit],
    );

    const handleChange = useCallback(
      (event: ChangeEvent<HTMLTextAreaElement>): void => {
        setText(event.target.value);
      },
      [],
    );

    const handleDragOver = useCallback(
      (event: DragEvent<HTMLDivElement>): void => {
        if (!attachmentAdapter) return;
        event.preventDefault();
        setDragOver(true);
      },
      [attachmentAdapter],
    );

    const handleDragLeave = useCallback(
      (event: DragEvent<HTMLDivElement>): void => {
        if (event.currentTarget === event.target) {
          setDragOver(false);
        }
      },
      [],
    );

    const handleDrop = useCallback(
      (event: DragEvent<HTMLDivElement>): void => {
        event.preventDefault();
        setDragOver(false);
        const files = event.dataTransfer?.files;
        if (!files) return;
        for (const file of Array.from(files)) {
          void addFile(file);
        }
      },
      [addFile],
    );

    return (
      <div
        className={className}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        data-composer-dragover={dragOver || undefined}
      >
        {topBar?.({ attachments, onRemove: (id) => void removeAttachment(id) })}
        <textarea
          ref={textareaRef}
          className="aui-composer__input"
          aria-label="Message"
          placeholder={placeholder}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKey}
          rows={minRows}
          disabled={disabled}
        />
        {bottomBar?.({
          text,
          running,
          disabled,
          attachmentsCount: attachments.length,
        })}
        {hint}
      </div>
    );
  },
);

/**
 * Send / Stop button. Renders a Stop control when `running`, otherwise
 * a Send control disabled when `text` is empty AND no attachments are
 * staged.
 */
export function ComposerSendButton({
  text,
  attachmentsCount,
  running,
  disabled,
  onSend,
  onCancel,
  sendIcon,
  stopIcon,
  sendLabel = "Send message",
  stopLabel = "Stop response",
  className,
  stopClassName,
}: {
  text: string;
  attachmentsCount: number;
  running: boolean;
  disabled?: boolean;
  onSend: () => void;
  onCancel?: () => void;
  sendIcon?: ReactNode;
  stopIcon?: ReactNode;
  sendLabel?: string;
  stopLabel?: string;
  className?: string;
  stopClassName?: string;
}): ReactElement {
  if (running) {
    return (
      <button
        type="button"
        className={stopClassName ?? className}
        aria-label={stopLabel}
        data-tooltip={stopLabel}
        onClick={() => onCancel?.()}
      >
        {stopIcon ?? "■"}
      </button>
    );
  }
  const sendDisabled =
    disabled || (text.trim().length === 0 && attachmentsCount === 0);
  return (
    <button
      type="button"
      className={className}
      aria-label={sendLabel}
      data-tooltip={sendLabel}
      disabled={sendDisabled}
      onClick={onSend}
    >
      {sendIcon ?? "↑"}
    </button>
  );
}
