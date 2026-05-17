import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type ForwardedRef,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";

import { MentionPopover, type MentionCandidate } from "./MentionPopover";
import { ModelPicker, type Depth } from "./ModelPicker";
import { ToolPicker } from "./ToolPicker";

/* Shared composer for Studio / Focus / Auto.
 *
 * Design source: /tmp/atlas-design/enterprise-search-template/project/
 * composer.jsx and chat1.md L805-820 ("bigger 2-row textarea, single
 * thin action row with Tools and Model · Depth plus attach, mic, send").
 *
 * Invariants (do not regress):
 * - One component used across all three modes. Variations come through
 *   props, never a forked variant.
 * - Hint row (↵ send · ⇧+↵ new line · / skills · model · Sources cited
 *   inline) renders unconditionally. Hiding it during a run was a real
 *   shipped regression — see apps/frontend/CLAUDE.md and the AssistantComposer
 *   note. Tests in this file lock this in.
 * - Enter sends, Shift+Enter inserts newline, "/" at a word boundary is
 *   reserved for the skills shortcut (the hint advertises it).
 * - Tools button = single popover with Skills + MCPs sections.
 * - Model button = single popover with Model rows + Fast/Balanced/Deep
 *   depth grid. */

/**
 * Composer mode.
 *
 * - `"compose"` (default) — full toolbar: Tools, Model · Depth, attach, mic, send.
 * - `"edit"` — chats-canvas-prd §15 EditComposer absorbed: textarea + Save +
 *   Cancel. Tools/Model/attach/mic are hidden because editing a prior message
 *   is structurally a "fix this text" intent, not a re-pick of skills/depth.
 *   Existing call sites that don't pass `mode` continue rendering compose.
 */
export type ComposerMode = "compose" | "edit";

/**
 * A resolved attachment owned by the host. The Composer renders pills + a
 * remove button keyed by `id`; the host owns the actual storage (upload,
 * persistence, content-addressable blob, etc.) via {@link AttachmentAdapter}.
 *
 * Kept intentionally minimal — the file's *content* never enters the
 * Composer's render path (and must never be logged). Name + size + mime are
 * the only display-side metadata.
 */
export interface CompleteAttachment {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly type: string;
  /** Host-defined opaque handle (upload result, blob ref, …). */
  readonly handle?: unknown;
}

/**
 * Host adapter for attachments. Reconciles P1-C's audit gap between the
 * frontend's legacy runtime composer (which had drag-and-drop + an
 * AttachmentAdapter) and the chat-surface Composer (which had neither).
 *
 * Contract:
 * - `add(file)` resolves with a {@link CompleteAttachment} once the host has
 *   accepted/uploaded the file. The Composer awaits it before showing a pill.
 * - `remove(id)` is fire-and-forget; the Composer drops the pill optimistically
 *   and the host cleans up its side.
 *
 * If no adapter is supplied, drag-and-drop is a no-op (existing call sites
 * see no change).
 */
export interface AttachmentAdapter {
  add(file: File): Promise<CompleteAttachment>;
  remove(id: string): void;
}

/**
 * Submission payload — text + the current attachment set. Replaces the older
 * `onSend(text: string)` signature for hosts that care about attachments;
 * `onSend` is preserved for backward compatibility (see
 * {@link ComposerProps.onSubmit} / {@link ComposerProps.onSend}).
 */
export interface ComposerSubmitPayload {
  readonly text: string;
  readonly attachments: ReadonlyArray<CompleteAttachment>;
}

/**
 * Imperative handle exposed via `forwardRef`. Hosts that need to drive the
 * composer programmatically (skill picker writing into the textarea, "Insert
 * citation here" actions, …) call methods on this. The data model stays
 * inside the component; the handle is the cross-cutting affordance.
 */
export interface ComposerHandle {
  readonly focus: () => void;
  readonly clear: () => void;
  readonly setText: (text: string) => void;
  readonly getText: () => string;
}

export interface ComposerProps {
  /**
   * Text-only send handler (backward-compat surface). Prefer
   * {@link ComposerProps.onSubmit} for new call sites — it carries
   * attachments. If both are supplied, `onSubmit` wins and `onSend` is
   * ignored. At least one of them must be wired or send will silently no-op.
   */
  readonly onSend?: (text: string) => void;
  /**
   * Submit handler with the full payload (text + attachments). Preferred
   * over {@link ComposerProps.onSend}. Reconciles P1-C's audit gap with the
   * old runtime composer.
   */
  readonly onSubmit?: (payload: ComposerSubmitPayload) => void;
  /**
   * Host-owned attachment adapter. When supplied, drag-and-drop on the
   * composer body and the attach button become live — each dropped file is
   * handed to `add(file)` and the resolved {@link CompleteAttachment} is
   * rendered as a removable pill. Without an adapter, drag-and-drop is
   * inert (the Composer still accepts dragenter/dragover so the page
   * default-handler doesn't open the file in a new tab).
   */
  readonly attachmentAdapter?: AttachmentAdapter;
  readonly onCancel?: () => void;
  readonly running?: boolean;
  readonly disabled?: boolean;
  readonly placeholder?: string;
  readonly initialModel?: string;
  readonly initialTools?: ReadonlyArray<string>;
  readonly initialDepth?: Depth;
  readonly portalTarget?: HTMLElement;
  /**
   * Composer mode (chats-canvas-prd §15). `"edit"` collapses the toolbar
   * and relabels Send → Save. Default `"compose"`.
   */
  readonly mode?: ComposerMode;
  /**
   * Caller-rendered region above the textarea. Used by ChatScreen to
   * surface selected-skills pills, attachment chips, or a "/skill"
   * preview without forking the Composer. ARIA grouping is the caller's
   * job — the Composer just hosts the slot.
   */
  readonly topBarSlot?: ReactNode;
  /**
   * Caller-rendered inline actions, placed between the attach button and
   * the Tools toggle. Used by ChatScreen for the per-chat connectors
   * button (chats-canvas-prd §15 — "Hosted at the call site").
   */
  readonly inlineActions?: ReactNode;
  /**
   * Handler for `/`-skill commands. Called when the user types `/` at a
   * word boundary and either presses Enter or otherwise submits a skill
   * shortcut. The `skill` argument is the slug (the token after the
   * leading `/`); `args` is the remainder of the input (trimmed). The
   * skill picker UI itself is a host concern — the Composer just emits
   * the event.
   */
  readonly onSkillCommand?: (skill: string, args: string) => void;
  /** Initial value of the textarea — used by `mode="edit"`. */
  readonly initialText?: string;
  /** Save action for edit mode. Receives the edited text. */
  readonly onSave?: (text: string) => void;
}

interface MentionTriggerState {
  readonly start: number;
  readonly query: string;
}

const DEFAULT_MODEL = "claude-opus-4-7";
const DEFAULT_DEPTH: Depth = "balanced";

function ComposerInner(
  props: ComposerProps,
  ref: ForwardedRef<ComposerHandle>,
): ReactElement {
  const {
    onSend,
    onSubmit,
    attachmentAdapter,
    onCancel,
    running = false,
    disabled = false,
    placeholder = "Send a message…",
    initialModel = DEFAULT_MODEL,
    initialTools,
    initialDepth = DEFAULT_DEPTH,
    portalTarget,
    mode = "compose",
    topBarSlot,
    inlineActions,
    onSkillCommand,
    initialText,
    onSave,
  } = props;

  const isEdit = mode === "edit";

  const [text, setText] = useState(initialText ?? "");
  const [model, setModel] = useState(initialModel);
  const [depth, setDepth] = useState<Depth>(initialDepth);
  const [tools, setTools] = useState<ReadonlyArray<string>>(initialTools ?? []);
  const [toolPickerOpen, setToolPickerOpen] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [mention, setMention] = useState<MentionTriggerState | null>(null);
  const [attachments, setAttachments] = useState<
    ReadonlyArray<CompleteAttachment>
  >([]);
  /* Tracks "a file is being dragged over the composer" for visual affordance
   * and so the page's default handler (which would otherwise navigate to the
   * file) is consistently suppressed. */
  const [isDragOver, setIsDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /* Imperative handle for hosts that need to write into the composer
   * programmatically (skill-picker workspace pane, citation injector, …).
   * The data model stays inside the component; the handle is the
   * cross-cutting affordance — see chats-canvas-prd §15 ComposerHandle row.
   *
   * `setText` / `clear` wrap their state updates in `flushSync` so the
   * controlled <textarea>'s DOM `value` is observable to callers in the
   * same synchronous tick. This matters for hosts (and tests) that read
   * `textareaRef.value` immediately after `ref.current.setText(…)` without
   * awaiting a render — without flushSync, React batches the update and the
   * textarea still reads as empty. */
  useImperativeHandle(
    ref,
    () => ({
      focus: (): void => {
        textareaRef.current?.focus();
      },
      clear: (): void => {
        flushSync(() => {
          setText("");
          setMention(null);
        });
      },
      setText: (next: string): void => {
        flushSync(() => {
          setText(next);
        });
      },
      getText: (): string => text,
    }),
    [text],
  );

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (el === null) {
      return;
    }
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT_PX);
    el.style.height = `${next}px`;
  }, [text]);

  const detectMention = useCallback(
    (value: string, caret: number): MentionTriggerState | null => {
      const upto = value.slice(0, caret);
      const at = upto.lastIndexOf("@");
      if (at === -1) {
        return null;
      }
      const before = at === 0 ? " " : upto[at - 1];
      if (before !== " " && before !== "\n") {
        return null;
      }
      const query = upto.slice(at + 1);
      if (/\s/.test(query)) {
        return null;
      }
      return { start: at, query };
    },
    [],
  );

  const handleTextChange = (next: string, caret: number): void => {
    setText(next);
    setMention(detectMention(next, caret));
  };

  const detectSkillCommand = useCallback(
    (value: string): { skill: string; args: string } | null => {
      // A "/skill" command is the entire input starting with "/" followed by
      // a slug (alphanumeric + dash/underscore), optionally followed by args.
      // We intentionally restrict to "input starts with /" — slashes inside
      // the middle of a message (URLs, paths) must not trigger the picker.
      const trimmed = value.trimStart();
      if (!trimmed.startsWith("/")) {
        return null;
      }
      const body = trimmed.slice(1);
      const match = /^([a-zA-Z][\w-]*)(?:\s+([\s\S]*))?$/.exec(body);
      if (match === null) {
        return null;
      }
      return { skill: match[1], args: (match[2] ?? "").trim() };
    },
    [],
  );

  const send = (): void => {
    const trimmed = text.trim();
    /* In compose mode we need either text *or* attachments to submit. Edit
     * mode still requires text (you can't "save" an empty edit). */
    const hasContent = isEdit
      ? trimmed.length > 0
      : trimmed.length > 0 || attachments.length > 0;
    if (!hasContent || disabled || running) {
      return;
    }
    /* "/skill ..." submissions exit through onSkillCommand if the host
     * wired it; otherwise they fall through to the normal send path so
     * the input isn't lost. */
    if (onSkillCommand) {
      const cmd = detectSkillCommand(trimmed);
      if (cmd !== null) {
        onSkillCommand(cmd.skill, cmd.args);
        setText("");
        setMention(null);
        return;
      }
    }
    if (isEdit) {
      onSave?.(trimmed);
      // Don't clear in edit mode — host decides whether to keep, close,
      // or replace the composer after Save.
      setMention(null);
      return;
    }
    /* Prefer onSubmit (the payload form). Fall through to onSend only
     * when onSubmit is absent — call sites can migrate gradually and we
     * never invoke both for one submission. */
    if (onSubmit !== undefined) {
      onSubmit({ text: trimmed, attachments });
    } else if (onSend !== undefined) {
      onSend(trimmed);
    }
    setText("");
    setMention(null);
    setAttachments([]);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
      return;
    }
    if (event.key === "Escape") {
      if (toolPickerOpen) {
        setToolPickerOpen(false);
      }
      if (modelPickerOpen) {
        setModelPickerOpen(false);
      }
      if (mention !== null) {
        setMention(null);
      }
      if (isEdit) {
        onCancel?.();
      }
    }
  };

  const insertMention = (candidate: MentionCandidate): void => {
    if (mention === null) {
      return;
    }
    const before = text.slice(0, mention.start);
    const after = text.slice(mention.start + 1 + mention.query.length);
    const inserted = `@${candidate.slug} `;
    const next = `${before}${inserted}${after}`;
    setText(next);
    setMention(null);
    const el = textareaRef.current;
    if (el !== null) {
      const pos = before.length + inserted.length;
      Promise.resolve().then(() => {
        el.focus();
        el.setSelectionRange(pos, pos);
      });
    }
  };

  const toggleTool = (name: string): void => {
    setTools((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name],
    );
  };

  /* --- Attachment drag-and-drop (P1-B1 Delta 2) ---
   *
   * Drag-and-drop is purely additive. Existing call sites that don't pass
   * an `attachmentAdapter` see the same composer they always did; we still
   * suppress the page's default drop handler so the browser doesn't
   * navigate to the dropped file, but no attachment state is touched. */
  const handleDragOver = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled || isEdit) {
      return;
    }
    /* Only react to file drags — text/element drags pass through. */
    const types = event.dataTransfer?.types;
    const isFileDrag =
      types !== undefined &&
      Array.from(types as unknown as Iterable<string>).includes("Files");
    if (!isFileDrag) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (!isDragOver) {
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>): void => {
    /* Fire only when the cursor exits the container (not its children). */
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }
    setIsDragOver(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    if (disabled || isEdit) {
      return;
    }
    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setIsDragOver(false);
    if (attachmentAdapter === undefined) {
      return;
    }
    /* Resolve each file independently; one failure doesn't block the rest.
     * Errors are swallowed here on purpose — host adapters own user-facing
     * error surfaces (toast, banner, etc.) and the Composer must not leak
     * file paths or contents to the console. */
    for (let i = 0; i < files.length; i += 1) {
      const file = files.item(i);
      if (file === null) {
        continue;
      }
      attachmentAdapter
        .add(file)
        .then((attachment) => {
          setAttachments((prev) => [...prev, attachment]);
        })
        .catch(() => {
          /* Intentional no-op — see comment above. */
        });
    }
  };

  const removeAttachment = (id: string): void => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    attachmentAdapter?.remove(id);
  };

  /* Send enables when there's text *or* (in compose mode) an attachment.
   * Edit mode keeps the text-required rule — "save" on an empty edit makes
   * no semantic sense. */
  const hasSubmissionContent = isEdit
    ? text.trim().length > 0
    : text.trim().length > 0 || attachments.length > 0;
  const canSend = hasSubmissionContent && !disabled && !running;
  const modelLabel = labelForModel(model);
  const depthLabel = labelForDepth(depth);

  return (
    <div
      data-testid="composer"
      data-running={running ? "true" : undefined}
      data-mode={mode}
      data-drag-over={isDragOver ? "true" : undefined}
      style={
        isDragOver
          ? { ...containerStyle, ...containerDragOverStyle }
          : containerStyle
      }
      aria-disabled={disabled}
      onDragEnter={handleDragOver}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {topBarSlot !== undefined ? (
        <div data-testid="composer-topbar-slot" style={topBarSlotStyle}>
          {topBarSlot}
        </div>
      ) : null}
      {attachments.length > 0 ? (
        <div
          data-testid="composer-attachments"
          style={attachmentStripStyle}
          aria-label="Attachments"
        >
          {attachments.map((attachment) => (
            <span
              key={attachment.id}
              data-testid={`composer-attachment-${attachment.id}`}
              data-attachment-id={attachment.id}
              style={attachmentPillStyle}
              title={`${attachment.name} (${formatBytes(attachment.size)})`}
            >
              <PaperclipIcon />
              <span style={attachmentPillNameStyle}>{attachment.name}</span>
              <span style={attachmentPillSizeStyle} aria-hidden="true">
                {formatBytes(attachment.size)}
              </span>
              <button
                type="button"
                onClick={() => removeAttachment(attachment.id)}
                aria-label={`Remove ${attachment.name}`}
                title="Remove"
                data-testid={`composer-attachment-remove-${attachment.id}`}
                style={attachmentPillRemoveStyle}
              >
                <CloseIcon />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <textarea
        ref={textareaRef}
        value={text}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) =>
          handleTextChange(e.target.value, e.target.selectionStart ?? 0)
        }
        onKeyDown={handleKeyDown}
        onKeyUp={(e) => {
          const target = e.currentTarget;
          handleTextChange(target.value, target.selectionStart ?? 0);
        }}
        rows={2}
        aria-label={isEdit ? "Edit message" : "Message"}
        style={textareaStyle}
        data-testid="composer-textarea"
      />
      <div style={toolbarStyle}>
        <div style={toolbarLeftStyle}>
          {isEdit ? null : (
            <>
              {/* Attach. Icon-only; the design composer shows attach as part
               * of the thin action row alongside Tools. The host wires the
               * filepicker (out of scope here) — for now this is a visual
               * affordance with a no-op onClick. */}
              <button
                type="button"
                aria-label="Attach a file"
                title="Attach a file"
                data-testid="composer-attach"
                style={iconButtonStyle(false)}
              >
                <PlusIcon />
              </button>
              {inlineActions !== undefined ? (
                <div
                  data-testid="composer-inline-actions"
                  style={inlineActionsStyle}
                >
                  {inlineActions}
                </div>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  setToolPickerOpen((v) => !v);
                  setModelPickerOpen(false);
                }}
                aria-pressed={toolPickerOpen}
                aria-label="Tools"
                data-testid="composer-tools-toggle"
                style={pillButtonStyle(toolPickerOpen)}
              >
                <WrenchIcon />
                <span>
                  {tools.length > 0 ? `Tools · ${tools.length}` : "Tools"}
                </span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setModelPickerOpen((v) => !v);
                  setToolPickerOpen(false);
                }}
                aria-pressed={modelPickerOpen}
                aria-label="Model and depth"
                data-testid="composer-model-toggle"
                style={pillButtonStyle(modelPickerOpen)}
              >
                <span style={modelDotStyle(depth)} aria-hidden="true" />
                <span>
                  {modelLabel}
                  <span style={modelDepthSepStyle}> · </span>
                  {depthLabel}
                </span>
              </button>
            </>
          )}
        </div>
        <div style={toolbarRightStyle}>
          {isEdit ? (
            <>
              <button
                type="button"
                onClick={() => onCancel?.()}
                aria-label="Cancel edit"
                title="Cancel"
                data-testid="composer-edit-cancel"
                style={cancelButtonStyle}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={send}
                disabled={!canSend}
                aria-label="Save edit"
                title="Save"
                data-testid="composer-edit-save"
                style={saveButtonStyle(canSend)}
              >
                Save
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label="Voice input"
                title="Voice input"
                data-testid="composer-mic"
                style={iconButtonStyle(false)}
              >
                <MicIcon />
              </button>
              {running ? (
                <button
                  type="button"
                  onClick={() => onCancel?.()}
                  aria-label="Stop"
                  title="Stop"
                  data-testid="composer-cancel"
                  style={cancelButtonStyle}
                >
                  <StopIcon />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={send}
                  disabled={!canSend}
                  aria-label="Send"
                  title="Send"
                  data-testid="composer-send"
                  style={sendButtonStyle(canSend)}
                >
                  <SendIcon />
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {/* Hint row is stateless info — MUST render whether or not a run
       * is active. See ComposerProps doc comment. */}
      <div style={hintRowStyle} data-testid="composer-hint">
        <span style={hintItemStyle}>
          <kbd style={kbdStyle}>↵</kbd> {isEdit ? "save" : "send"}
        </span>
        <span style={hintSepStyle} aria-hidden="true" />
        <span style={hintItemStyle}>
          <kbd style={kbdStyle}>⇧</kbd>+<kbd style={kbdStyle}>↵</kbd> new line
        </span>
        <span style={hintSepStyle} aria-hidden="true" />
        <span style={hintItemStyle}>
          <kbd style={kbdStyle}>/</kbd> skills
        </span>
        <span style={hintTrailingStyle}>
          {isEdit ? "Editing" : `${modelLabel} · Sources cited inline`}
        </span>
      </div>
      {!isEdit && toolPickerOpen ? (
        <div style={popoverHostStyle}>
          <ToolPicker
            open={true}
            selectedTools={tools}
            onToggle={toggleTool}
            onClose={() => setToolPickerOpen(false)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
      {!isEdit && modelPickerOpen ? (
        <div style={popoverHostStyle}>
          <ModelPicker
            open={true}
            selectedModel={model}
            selectedDepth={depth}
            onSelect={setModel}
            onDepthChange={setDepth}
            onClose={() => setModelPickerOpen(false)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
      {mention !== null ? (
        <div style={popoverHostStyle}>
          <MentionPopover
            open={true}
            query={mention.query}
            onSelect={insertMention}
            onClose={() => setMention(null)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Public Composer — `forwardRef` so hosts can call `ref.current.setText(…)`
 * and friends (chats-canvas-prd §15 ComposerHandle). Existing call sites
 * that don't pass a ref keep working unchanged.
 */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(
  ComposerInner,
);
Composer.displayName = "Composer";

function labelForModel(id: string): string {
  if (id === "claude-opus-4-7") {
    return "Opus 4.7";
  }
  if (id === "claude-sonnet-4-6") {
    return "Sonnet 4.6";
  }
  if (id === "claude-haiku-4-5") {
    return "Haiku 4.5";
  }
  return id;
}

function labelForDepth(depth: Depth): string {
  if (depth === "fast") {
    return "Fast";
  }
  if (depth === "deep") {
    return "Deep";
  }
  return "Balanced";
}

/* Inline icons (no external dep). Sized in em so they scale with the
 * button's font-size and inherit currentColor for token-driven theming. */
function PlusIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function WrenchIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10.5 2.5a3 3 0 0 1 2.83 3.95l-2.45-1.41-1.71 1L8 3.94l1-1.7a3 3 0 0 1 1.5.26ZM6 7.5l-3 5.2a1.5 1.5 0 1 0 2.6 1.5l3-5.2" />
    </svg>
  );
}

function MicIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="6" y="2" width="4" height="7" rx="2" />
      <path d="M3.5 7a4.5 4.5 0 0 0 9 0M8 11.5V14M5.5 14h5" />
    </svg>
  );
}

function SendIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2.5 8 13.5 2.5 11 13.5l-3-4.5-5.5-1Z" />
    </svg>
  );
}

function StopIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
    >
      <rect x="4" y="4" width="8" height="8" rx="1.5" />
    </svg>
  );
}

function PaperclipIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10.5 3.5 5 9a2 2 0 1 0 2.83 2.83l6-6a3.5 3.5 0 0 0-4.95-4.95L3 6.76" />
    </svg>
  );
}

function CloseIcon(): ReactNode {
  return (
    <svg
      width="1em"
      height="1em"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  );
}

/**
 * Human-readable size string for an attachment pill. Plain math so we stay
 * dependency-free. Sizes ≥ 1 KiB are rendered with one decimal; smaller
 * are rendered as bytes. We never log this — it's display-only.
 */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}

const MAX_TEXTAREA_HEIGHT_PX = 220;
const MIN_TEXTAREA_HEIGHT_PX = 56; // ≈ 2 rows of 14px text at 1.5 line-height

const containerStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 12,
  padding: 10,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  fontFamily: "var(--font-sans)",
  color: "var(--color-text)",
  position: "relative",
};

const textareaStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  padding: "10px 12px",
  fontSize: 14,
  lineHeight: 1.5,
  resize: "none",
  outline: "none",
  width: "100%",
  fontFamily: "inherit",
  minHeight: MIN_TEXTAREA_HEIGHT_PX,
  maxHeight: MAX_TEXTAREA_HEIGHT_PX,
  overflowY: "auto",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  /* Thin action row — single row, low visual weight. */
  minHeight: 32,
};

const toolbarLeftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  minWidth: 0,
};

const toolbarRightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const iconButtonStyle = (active: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 28,
  background: active ? "var(--color-surface-muted)" : "transparent",
  color: "var(--color-text-muted)",
  border: "1px solid transparent",
  borderRadius: 6,
  padding: 0,
  fontSize: 14,
  cursor: "pointer",
});

const pillButtonStyle = (active: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 28,
  background: active ? "var(--color-surface-muted)" : "transparent",
  color: "var(--color-text-muted)",
  border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
  borderRadius: 999,
  padding: "0 10px",
  fontSize: 12,
  cursor: "pointer",
  whiteSpace: "nowrap",
});

const modelDotStyle = (depth: Depth): CSSProperties => ({
  width: 6,
  height: 6,
  borderRadius: "50%",
  background:
    depth === "fast"
      ? "var(--color-success)"
      : depth === "deep"
        ? "var(--color-warning)"
        : "var(--color-accent)",
  flexShrink: 0,
});

const modelDepthSepStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  margin: "0 2px",
};

const sendButtonStyle = (enabled: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 32,
  height: 32,
  background: enabled ? "var(--color-accent)" : "var(--color-surface-muted)",
  color: enabled ? "var(--color-accent-contrast)" : "var(--color-text-subtle)",
  border: "none",
  borderRadius: 8,
  padding: 0,
  fontSize: 14,
  cursor: enabled ? "pointer" : "not-allowed",
});

const cancelButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 32,
  height: 32,
  background: "var(--color-surface-muted)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  padding: "0 10px",
  fontSize: 12,
  cursor: "pointer",
};

const saveButtonStyle = (enabled: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: 32,
  height: 32,
  background: enabled ? "var(--color-accent)" : "var(--color-surface-muted)",
  color: enabled ? "var(--color-accent-contrast)" : "var(--color-text-subtle)",
  border: "none",
  borderRadius: 8,
  padding: "0 12px",
  fontSize: 12,
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
});

const topBarSlotStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  minHeight: 0,
};

const inlineActionsStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
};

const hintRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "2px 4px 0",
  fontSize: 11,
  color: "var(--color-text-subtle)",
  minHeight: 18,
};

const hintItemStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
};

const hintTrailingStyle: CSSProperties = {
  marginLeft: "auto",
  color: "var(--color-text-subtle)",
};

const hintSepStyle: CSSProperties = {
  width: 1,
  height: 10,
  background: "var(--color-border)",
};

const kbdStyle: CSSProperties = {
  fontFamily: "var(--font-sans)",
  fontSize: 10.5,
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  borderBottomWidth: 2,
  borderRadius: 4,
  padding: "1px 5px",
  color: "var(--color-text-muted)",
};

const popoverHostStyle: CSSProperties = {
  marginTop: 4,
};

const containerDragOverStyle: CSSProperties = {
  /* Subtle drag-target affordance — token-driven, no hard-coded colors. */
  borderColor: "var(--color-accent)",
  background: "var(--color-surface-muted)",
};

const attachmentStripStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  paddingBottom: 2,
};

const attachmentPillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  height: 24,
  padding: "0 4px 0 8px",
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  color: "var(--color-text-muted)",
  borderRadius: 999,
  fontSize: 12,
  maxWidth: 240,
};

const attachmentPillNameStyle: CSSProperties = {
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  minWidth: 0,
};

const attachmentPillSizeStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontSize: 10.5,
  flexShrink: 0,
};

const attachmentPillRemoveStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  background: "transparent",
  color: "var(--color-text-muted)",
  border: "none",
  borderRadius: "50%",
  padding: 0,
  cursor: "pointer",
  fontSize: 11,
  flexShrink: 0,
};
