import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type DragEvent,
  type FocusEvent,
  type ForwardedRef,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";

import { MentionPopover, type MentionCandidate } from "./MentionPopover";
import { ModelPicker, type Depth, type ModelDescriptor } from "./ModelPicker";
import { ToolPicker } from "./ToolPicker";

/* Shared composer for Studio / Focus / Auto.
 *
 * Design source: /tmp/atlas-design/0x-copilot-template/project/
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
 * Generic content-part shape carried by a {@link CompleteAttachment} when the
 * host's upload pipeline returns the assembled message payload (image bytes,
 * extracted text, etc.). The Composer never inspects the array — it's an
 * opaque blob handed back on submit so the run-create pipeline can ship it
 * to the agent. Mirrors `AttachmentContentPart` in the legacy frontend
 * runtime types (the surface the runtime composer hands hosts today).
 */
export interface AttachmentContentPart {
  readonly type: string;
  readonly [key: string]: unknown;
}

/**
 * A resolved attachment owned by the host. The Composer renders pills + a
 * remove button keyed by `id`; the host owns the actual storage (upload,
 * persistence, content-addressable blob, etc.) via {@link AttachmentAdapter}.
 *
 * Kept intentionally minimal — the file's *content* never enters the
 * Composer's render path (and must never be logged). Name + size + mime are
 * the only display-side metadata.
 *
 * The optional `content` array is a passthrough payload from the host's
 * upload pipeline. When the adapter's two-stage `add → send` path is used,
 * `send()` returns a CompleteAttachment with `content` populated; on submit
 * the Composer forwards the array unchanged in the {@link ComposerSubmitPayload}.
 * Single-stage adapters that resolve straight from `add()` may omit `content`
 * entirely.
 */
export interface CompleteAttachment {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly type: string;
  /** Host-defined opaque handle (upload result, blob ref, …). */
  readonly handle?: unknown;
  /**
   * Optional message-part payload returned by a two-stage adapter's
   * `send()` step. Forwarded verbatim in the submit payload — the
   * Composer never inspects it.
   */
  readonly content?: ReadonlyArray<AttachmentContentPart>;
  /** Discriminator. Defaults to `"complete"` when omitted. */
  readonly status?: { readonly type: "complete" };
}

/**
 * A two-stage attachment in flight. Returned by an adapter's `add()` step
 * when the upload requires an additional `send()` to finalise (mirrors the
 * legacy runtime AttachmentAdapter contract). The Composer renders the pill
 * eagerly so the user sees the attach landed; on submit it calls
 * `attachmentAdapter.send(pending)` to upgrade each pending attachment to a
 * {@link CompleteAttachment} before invoking the submit handler.
 */
export interface PendingAttachment {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly type: string;
  readonly handle?: unknown;
  readonly status: { readonly type: "pending" } | { readonly type: "running" };
}

/**
 * Guard a possibly-async `onSubmit` return value. When the host's submit
 * handler returns a promise, we attach a `.catch` so a rejected dispatch is
 * (a) never an unhandled rejection and (b) forwarded to the host's opt-in
 * `onSubmitError` channel. A synchronous (`void`) return is left untouched.
 * With no `onSubmitError`, the catch is a no-op sink — the rejection is
 * swallowed exactly as before, but without the console unhandled-rejection.
 */
function catchSubmitRejection(
  result: void | Promise<void>,
  onSubmitError?: (error: unknown) => void,
): void {
  if (
    result !== undefined &&
    result !== null &&
    typeof (result as Promise<void>).then === "function"
  ) {
    void (result as Promise<void>).catch((error: unknown) => {
      onSubmitError?.(error);
    });
  }
}

/** Discriminate pending vs. complete on the adapter return value. */
function isPendingAttachment(
  attachment: CompleteAttachment | PendingAttachment,
): attachment is PendingAttachment {
  const status = attachment.status;
  if (status === undefined) {
    return false;
  }
  return status.type === "pending" || status.type === "running";
}

/**
 * Host adapter for attachments. Reconciles P1-C's audit gap between the
 * frontend's legacy runtime composer (which had drag-and-drop + a two-stage
 * adapter) and the chat-surface Composer (which originally had neither).
 *
 * Two adapter shapes are supported — both flow through the same composer:
 *
 * - **Single-stage** (back-compat): `add(file)` resolves directly with a
 *   {@link CompleteAttachment}. `send` is not implemented. The Composer
 *   awaits `add` and renders the pill; submit forwards the attachment as-is.
 *
 * - **Two-stage** (parity with the runtime composer): `add(file)` resolves
 *   with a {@link PendingAttachment} (the host has reserved an id but not
 *   yet finalised the upload). The Composer renders the pill immediately
 *   and, on submit, calls `send(pending)` to upgrade each to a
 *   {@link CompleteAttachment} carrying the `content` payload.
 *
 * `remove(id)` is fire-and-forget. Both `Promise<void>` and `void` return
 * shapes are accepted so existing single-stage adapters keep working.
 *
 * If no adapter is supplied, drag-and-drop and the `+` button are inert
 * (existing call sites see no change).
 */
export interface AttachmentAdapter {
  add(file: File): Promise<CompleteAttachment | PendingAttachment>;
  /**
   * Optional. Required by two-stage adapters; absent on single-stage. When
   * present, the Composer awaits it on every pending attachment before
   * invoking the submit handler.
   */
  send?(pending: PendingAttachment): Promise<CompleteAttachment>;
  /**
   * Accepts both `Promise<void>` (runtime adapter parity) and `void`
   * (existing chat-surface adapters). Errors are swallowed by the
   * Composer — host owns user-facing error surfaces.
   */
  remove(id: string): Promise<void> | void;
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
 * Context handed to {@link ComposerProps.bottomBarRender} /
 * {@link ComposerProps.hintRender} so callers can compose host-specific
 * controls (model pill, depth chip, mic, send-with-cancel, etc.) using
 * the Composer's live state without reaching into refs.
 */
export interface ComposerSlotCtx {
  readonly text: string;
  readonly running: boolean;
  readonly disabled: boolean;
  readonly attachmentsCount: number;
  readonly focused: boolean;
}

/**
 * Imperative handle exposed via `forwardRef`. Hosts that need to drive the
 * composer programmatically (skill picker writing into the textarea, "Insert
 * citation here" actions, …) call methods on this. The data model stays
 * inside the component; the handle is the cross-cutting affordance.
 *
 * `setText` / `clear` / `appendText` wrap their updates in `flushSync` so
 * the controlled <textarea>'s DOM value is observable to callers in the
 * same synchronous tick.
 */
export interface ComposerHandle {
  readonly focus: () => void;
  readonly clear: () => void;
  readonly setText: (text: string) => void;
  readonly getText: () => string;
  /**
   * Append text to the existing buffer. If the textarea is focused, the
   * text is inserted at the current caret position; otherwise it is
   * appended with a single newline separator (matching the legacy runtime
   * composer's behaviour). The textarea is focused afterwards so the
   * caller's "skill inserted" UX flows into typing.
   */
  readonly appendText: (text: string) => void;
  /**
   * Programmatic attach via the same path as drag-drop / the `+` button.
   * Resolves once the adapter has accepted the file (the pill is rendered
   * before this promise resolves). Returns void; the resolved attachment
   * is owned by the adapter and visible in the submit payload.
   *
   * No-op when no `attachmentAdapter` is wired.
   */
  readonly addAttachment: (file: File) => Promise<void>;
  /**
   * Synchronous submit trigger — equivalent to the user pressing Enter or
   * clicking Send. Honours the same disabled / running / empty-content
   * guards. Used by the AssistantComposer's host-rendered Send button so
   * the click takes the same code path as a keyboard Enter without
   * synthesising keyboard events.
   */
  readonly submit: () => void;
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
   *
   * May return a promise: the host's dispatch (e.g. `POST /v1/agent/runs`)
   * is usually async. The Composer captures the return value and, if it is a
   * promise, attaches a `.catch` at every call site so a rejected submit is
   * (a) never an unhandled rejection and (b) forwarded to
   * {@link ComposerProps.onSubmitError} when the host opts in.
   */
  readonly onSubmit?: (payload: ComposerSubmitPayload) => void | Promise<void>;
  /**
   * Optional error channel for a rejected async {@link ComposerProps.onSubmit}.
   * When `onSubmit` returns a promise that rejects (a failed run-create,
   * a network error), the Composer routes the rejection here instead of
   * letting it become an unhandled rejection. Absent → the rejection is
   * still caught (no unhandled rejection) but not surfaced, preserving the
   * pre-existing behaviour. Fixes the class of bug where a host had to add
   * its own per-call `.catch` around the composer's promise.
   */
  readonly onSubmitError?: (error: unknown) => void;
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
  /**
   * Custom bottom-bar renderer. When provided, REPLACES the built-in
   * thin action row (attach / Tools / Model · Depth / mic / send) with
   * the caller's element. The host gets {@link ComposerSlotCtx} so it
   * can wire its own buttons to text / running / focus state. Used by
   * AssistantComposer to render aui-* class hooks + the host's
   * model-pill / depth-control / connectors trigger.
   *
   * When using `bottomBarRender`, the host is responsible for its own
   * send / cancel buttons — typically routed through the imperative
   * {@link ComposerHandle.submit} so the disabled / running guards
   * continue to apply.
   */
  readonly bottomBarRender?: (ctx: ComposerSlotCtx) => ReactNode;
  /**
   * Custom hint-row renderer. When provided, REPLACES the built-in
   * `↵ send · ⇧+↵ new line · / skills · ...` row. Returning `null` opts out
   * of the hint entirely — no row, and no empty slot wrapper (this is how
   * `AssistantComposer` renders the v3 composer, which has no hint strip).
   * When the row IS rendered it remains stateless info — never gate the
   * caller-supplied element on `running` (see apps/frontend/CLAUDE.md →
   * "Composer hint row").
   */
  readonly hintRender?: (ctx: ComposerSlotCtx) => ReactNode;
  /**
   * Pre-handler for keydown events on the textarea. Called BEFORE the
   * Composer's built-in handling (Enter to send, Escape to close).
   * Returning `true` (or calling `event.preventDefault()`) prevents the
   * Composer's default handling for that key — used by AssistantComposer
   * for the slash-key shortcut that opens the skills workspace pane on
   * an empty composer.
   */
  readonly onInputKeyDown?: (
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) => boolean | void;
  /**
   * Minimum textarea rows. Default `2` (matches the design spec
   * "bigger 2-row textarea"). Used together with {@link maxRows} to
   * compute the textarea's min / max height instead of hard-coded
   * pixels.
   */
  readonly minRows?: number;
  /**
   * Maximum textarea rows before the textarea starts scrolling.
   * Default `8` (~220px at 14px/1.5lh, preserving the previous
   * hard-coded 220px max). Values <minRows are clamped.
   */
  readonly maxRows?: number;
  /**
   * Additional className applied to the root container. Lets hosts
   * extend with their own classes (e.g. `aui-composer`) for CSS
   * targeting without forking the component. The Composer's own
   * inline styles continue to apply.
   */
  readonly className?: string;
  /**
   * When true, indicates the caller-rendered topBarSlot is showing
   * content even when there are no attachments. Flips `data-has-topbar`
   * on the root so callers can style accordingly (e.g. AssistantComposer
   * shows selected-skill pills inside its own topBar slot wrapper).
   */
  readonly hasTopBarContent?: boolean;
  /**
   * Host-injected model catalog for the built-in Model · Depth popover —
   * the source of truth for the model rows (sourced from the workspace
   * catalog, `/v1/agent/models`). When omitted, the popover falls back to a
   * static built-in default; every real consumer should inject a
   * catalog-backed list so the picker reflects the user's actual BYOK +
   * Local models. Ignored when a `bottomBarRender` slot supplies its own
   * model control (e.g. AssistantComposer's `ModelPill`).
   */
  readonly models?: ReadonlyArray<ModelDescriptor>;
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
    onSubmitError,
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
    bottomBarRender,
    hintRender,
    onInputKeyDown,
    minRows = DEFAULT_MIN_ROWS,
    maxRows = DEFAULT_MAX_ROWS,
    className,
    hasTopBarContent = false,
    models,
  } = props;

  const isEdit = mode === "edit";

  const [text, setText] = useState(initialText ?? "");
  const [model, setModel] = useState(initialModel);
  const [depth, setDepth] = useState<Depth>(initialDepth);
  const [tools, setTools] = useState<ReadonlyArray<string>>(initialTools ?? []);
  const [toolPickerOpen, setToolPickerOpen] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [mention, setMention] = useState<MentionTriggerState | null>(null);
  /* Mixed-status list — single-stage adapters' returns land here as a
   * CompleteAttachment immediately; two-stage adapters' `add()` lands as a
   * PendingAttachment and is upgraded on submit. We render pills the same way
   * for both shapes (we only key on id / name / size). */
  const [attachments, setAttachments] = useState<
    ReadonlyArray<CompleteAttachment | PendingAttachment>
  >([]);
  /* Tracks "a file is being dragged over the composer" for visual affordance
   * and so the page's default handler (which would otherwise navigate to the
   * file) is consistently suppressed. */
  const [isDragOver, setIsDragOver] = useState(false);
  /* Drives data-focused for CSS targeting + the focus-tracked plumbing the
   * legacy runtime composer exposed via its bottomBar slot. */
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /* Guard against re-entrancy on submit (handle.submit() can be called from
   * a host button that's also a click target inside the bottom bar). */
  const submittingRef = useRef(false);

  /* Clamp + derive the textarea min/max heights from rows so callers can
   * push the textarea taller or shorter without touching the file. At the v3
   * metrics (12.5px · 1.55 line-height = 19.375px per row, + 14px of vertical
   * padding) the design's `.cmp textarea{max-height:130px}` is `maxRows: 6`,
   * which is what AssistantComposer passes; the rail's `minRows: 2` floor is
   * ~53px. Auto-grow (below) clamps scrollHeight into that band, so both the
   * empty and the multi-line states track whatever rows a caller asks for. */
  const clampedMinRows = Math.max(1, minRows);
  const clampedMaxRows = Math.max(clampedMinRows, maxRows);
  const textareaMinPx =
    clampedMinRows * TEXTAREA_LINE_HEIGHT_PX + TEXTAREA_PADDING_PX;
  const textareaMaxPx =
    clampedMaxRows * TEXTAREA_LINE_HEIGHT_PX + TEXTAREA_PADDING_PX;

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (el === null) {
      return;
    }
    el.style.height = "auto";
    const next = Math.min(
      Math.max(el.scrollHeight, textareaMinPx),
      textareaMaxPx,
    );
    el.style.height = `${next}px`;
  }, [text, textareaMinPx, textareaMaxPx]);

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

  /* Finalise any pending attachments via the two-stage `send()` step, then
   * dispatch to the host. Single-stage attachments pass through unchanged.
   * Errors per-attachment are swallowed; the rest of the batch still ships
   * (the legacy runtime composer had the same policy). */
  const finaliseAttachments = useCallback(async (): Promise<
    ReadonlyArray<CompleteAttachment>
  > => {
    if (attachments.length === 0) {
      return [];
    }
    const finalised: CompleteAttachment[] = [];
    for (const attachment of attachments) {
      if (!isPendingAttachment(attachment)) {
        finalised.push(attachment);
        continue;
      }
      if (attachmentAdapter?.send !== undefined) {
        try {
          const completed = await attachmentAdapter.send(attachment);
          finalised.push(completed);
        } catch {
          /* Skip the failed attachment, ship the rest. */
        }
      }
    }
    return finalised;
  }, [attachments, attachmentAdapter]);

  const send = useCallback((): void => {
    if (submittingRef.current) {
      return;
    }
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
    /* Fast path: no pending attachments → synchronous dispatch + clear.
     * This is the existing behaviour and keeps tests that read state
     * immediately after Enter unchanged. We flushSync the clear so the
     * DOM textarea value is observable to callers in the same tick —
     * matters for `handle.submit()` callers that read state immediately
     * after, where React's normal batching otherwise defers the clear. */
    const hasPending = attachments.some(isPendingAttachment);
    if (!hasPending) {
      const completeOnly = attachments.filter(
        (a): a is CompleteAttachment => !isPendingAttachment(a),
      );
      /* Capture the (possibly async) submit result and attach a rejection
       * catch so a failed host dispatch never becomes an unhandled rejection
       * and is surfaced through `onSubmitError` when the host opts in. The
       * clear below still runs synchronously — the composer empties on send
       * exactly as before; error handling is out-of-band. */
      let submitResult: void | Promise<void> = undefined;
      if (onSubmit !== undefined) {
        submitResult = onSubmit({ text: trimmed, attachments: completeOnly });
      } else if (onSend !== undefined) {
        onSend(trimmed);
      }
      catchSubmitRejection(submitResult, onSubmitError);
      flushSync(() => {
        setText("");
        setMention(null);
        setAttachments([]);
      });
      return;
    }
    /* Two-stage path: finalise pending attachments then dispatch. We
     * clear local state once the async finalise resolves to avoid the
     * user re-submitting mid-flight. */
    submittingRef.current = true;
    finaliseAttachments()
      .then((finalised) => {
        let submitResult: void | Promise<void> = undefined;
        if (onSubmit !== undefined) {
          submitResult = onSubmit({ text: trimmed, attachments: finalised });
        } else if (onSend !== undefined) {
          onSend(trimmed);
        }
        catchSubmitRejection(submitResult, onSubmitError);
        setText("");
        setMention(null);
        setAttachments([]);
      })
      .finally(() => {
        submittingRef.current = false;
      });
  }, [
    attachments,
    detectSkillCommand,
    disabled,
    finaliseAttachments,
    isEdit,
    onSave,
    onSend,
    onSkillCommand,
    onSubmit,
    onSubmitError,
    running,
    text,
  ]);

  /* Programmatic attach — same path as drop + the `+` button. Returns a
   * promise that resolves once the adapter has accepted the file (the
   * pill renders during this window). No-op without an adapter. */
  const addFile = useCallback(
    async (file: File): Promise<void> => {
      if (attachmentAdapter === undefined) {
        return;
      }
      try {
        const attachment = await attachmentAdapter.add(file);
        setAttachments((prev) => [...prev, attachment]);
      } catch {
        /* Adapter owns user-facing errors. */
      }
    },
    [attachmentAdapter],
  );

  /* Imperative handle for hosts that need to write into the composer
   * programmatically (skill-picker workspace pane, citation injector, …).
   * The data model stays inside the component; the handle is the
   * cross-cutting affordance — see chats-canvas-prd §15 ComposerHandle row.
   *
   * `setText` / `clear` / `appendText` wrap their state updates in
   * `flushSync` so the controlled <textarea>'s DOM `value` is observable
   * to callers in the same synchronous tick. This matters for hosts (and
   * tests) that read `textareaRef.value` immediately after
   * `ref.current.setText(…)` without awaiting a render — without
   * flushSync, React batches the update and the textarea still reads as
   * empty. */
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
      appendText: (next: string): void => {
        const el = textareaRef.current;
        const caret =
          el !== null && globalThis.document.activeElement === el
            ? (el.selectionStart ?? text.length)
            : null;
        flushSync(() => {
          setText((current) => {
            if (caret !== null) {
              return `${current.slice(0, caret)}${next}${current.slice(caret)}`;
            }
            if (current.trimEnd().length === 0) {
              return next;
            }
            return `${current.trimEnd()}\n${next}`;
          });
        });
        /* Focus after the insertion so the caller's "skill inserted" UX
         * flows into typing. RAF lets the controlled value settle so the
         * caret lands at the end of the inserted text rather than 0. */
        requestAnimationFrame(() => {
          textareaRef.current?.focus();
        });
      },
      addAttachment: (file: File): Promise<void> => addFile(file),
      submit: (): void => {
        send();
      },
    }),
    [addFile, send, text],
  );

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    /* Caller-supplied interceptor runs FIRST so it can claim keys (e.g.
     * the `/` shortcut on an empty composer) before the default Enter /
     * Escape handling. Two ways to claim: return `true`, or call
     * preventDefault on the event. */
    if (onInputKeyDown !== undefined) {
      const handled = onInputKeyDown(event);
      if (handled === true || event.defaultPrevented) {
        return;
      }
    }
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
     * file paths or contents to the console. Routes through `addFile` so
     * drop / `+` button / `handle.addAttachment(...)` share one path. */
    for (let i = 0; i < files.length; i += 1) {
      const file = files.item(i);
      if (file === null) {
        continue;
      }
      void addFile(file);
    }
  };

  const removeAttachment = (id: string): void => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    /* Adapter remove returns `Promise<void> | void`. We don't await it —
     * the pill is dropped optimistically. Failed cleanups are the
     * adapter's problem to surface (toast / log). */
    const result = attachmentAdapter?.remove(id);
    if (
      result !== undefined &&
      typeof result === "object" &&
      "catch" in result
    ) {
      void (result as Promise<void>).catch(() => {
        /* swallow */
      });
    }
  };

  /* The `+` button. Single source of truth for the file-picker open
   * path used by both the built-in button and bottomBarRender hosts
   * that want their own. */
  const openFilePicker = useCallback((): void => {
    const el = fileInputRef.current;
    if (el === null) {
      return;
    }
    /* Reset so picking the same file twice in a row still fires
     * onchange. */
    el.value = "";
    el.click();
  }, []);

  const handleFileInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>): void => {
      const files = event.target.files;
      if (files === null) {
        return;
      }
      for (let i = 0; i < files.length; i += 1) {
        const file = files.item(i);
        if (file !== null) {
          void addFile(file);
        }
      }
      /* Reset so re-selecting the same file fires onchange next time. */
      event.target.value = "";
    },
    [addFile],
  );

  const handleFocus = useCallback((): void => {
    setFocused(true);
  }, []);

  const handleBlur = useCallback((event: FocusEvent<HTMLDivElement>): void => {
    /* Don't flip on blur into our own popovers / buttons — check
     * whether focus is still inside the composer container. */
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setFocused(false);
    }
  }, []);

  /* Send enables when there's text *or* (in compose mode) an attachment.
   * Edit mode keeps the text-required rule — "save" on an empty edit makes
   * no semantic sense. */
  const hasSubmissionContent = isEdit
    ? text.trim().length > 0
    : text.trim().length > 0 || attachments.length > 0;
  const canSend = hasSubmissionContent && !disabled && !running;
  const modelLabel = labelForModel(model, models);
  const depthLabel = labelForDepth(depth);

  const slotCtx: ComposerSlotCtx = {
    text,
    running,
    disabled,
    attachmentsCount: attachments.length,
    focused,
  };

  /* data-has-topbar reflects "is the host or the Composer's own
   * attachment strip currently showing visible topBar content". The
   * caller can force-flip via `hasTopBarContent` when its topBarSlot
   * still has non-attachment chips even when no attachments exist. */
  const hasTopBar =
    topBarSlot !== undefined || attachments.length > 0 || hasTopBarContent;

  /* Evaluated once so the null-opt-out check below and the render read the
   * same node (calling `hintRender` twice would double any host-side work). */
  const hintSlotContent =
    hintRender !== undefined ? hintRender(slotCtx) : undefined;

  return (
    <div
      data-testid="composer"
      data-running={running ? "true" : undefined}
      data-mode={mode}
      data-drag-over={isDragOver ? "true" : undefined}
      data-focused={focused ? "true" : undefined}
      data-has-topbar={hasTopBar ? "true" : undefined}
      className={className}
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
      onBlur={handleBlur}
    >
      {/* Hidden input — shared by the `+` button and any bottomBarRender
       * hosts that route through ComposerHandle.addAttachment. Only
       * rendered when an attachment adapter is wired so the file picker
       * can't be opened by accident in adapter-less call sites. */}
      {attachmentAdapter !== undefined ? (
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          data-testid="composer-file-input"
          onChange={handleFileInputChange}
        />
      ) : null}
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
        onFocus={handleFocus}
        rows={clampedMinRows}
        aria-label={isEdit ? "Edit message" : "Message"}
        style={{
          ...textareaStyle,
          minHeight: textareaMinPx,
          maxHeight: textareaMaxPx,
        }}
        data-testid="composer-textarea"
      />
      {/* bottomBarRender, when provided, REPLACES the built-in thin
       * action row entirely (P1.6 gap 2). The host gets the full
       * ComposerSlotCtx and must own its own send/cancel — typically
       * via handle.submit(). Edit-mode keeps its own Save/Cancel row
       * regardless since the design spec makes edit-mode's bottom row
       * non-replaceable. */}
      {bottomBarRender !== undefined && !isEdit ? (
        <div data-testid="composer-bottombar-slot">
          {bottomBarRender(slotCtx)}
        </div>
      ) : (
        <div style={toolbarStyle}>
          <div style={toolbarLeftStyle}>
            {isEdit ? null : (
              <>
                {/* Attach. Icon-only; the design composer shows attach as
                 * part of the thin action row alongside Tools. When an
                 * `attachmentAdapter` is wired the click opens the hidden
                 * file input; without an adapter the button stays inert so
                 * existing call sites see no behavioural change. */}
                <button
                  type="button"
                  aria-label="Attach a file"
                  title="Attach a file"
                  data-testid="composer-attach"
                  onClick={
                    attachmentAdapter !== undefined ? openFilePicker : undefined
                  }
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
      )}
      {/* Hint row is stateless info — MUST render whether or not a run
       * is active. See ComposerProps doc comment. When `hintRender` is
       * provided, the host's element REPLACES the built-in row (still
       * unconditionally rendered, just by the caller). A host that returns
       * `null` opts OUT of the hint entirely: the slot wrapper is skipped so
       * the shell isn't left with an empty div eating a flex gap. */}
      {hintRender !== undefined ? (
        hintSlotContent === null || hintSlotContent === undefined ? null : (
          <div data-testid="composer-hint-slot">{hintSlotContent}</div>
        )
      ) : (
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
      )}
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
            models={models}
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

function labelForModel(
  id: string,
  models?: ReadonlyArray<ModelDescriptor>,
): string {
  /* Injected catalog first — the composer pill must show the label for the
   * host's actual model, not a hardcoded one. */
  const injected = models?.find((m) => m.id === id);
  if (injected !== undefined) {
    return injected.label;
  }
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

/* Textarea sizing — pixels per row are derived from font-size * line-height
 * plus the static vertical padding on `textareaStyle`, so changing either of
 * those below re-derives every caller's min/max height. */
// v3 "quiet" system: copilot-v3.css `.cmp textarea{font:12.5px/1.55;
// padding:10px 12px 4px}`. The 13px/1.5 the composer had drifted to made the
// draft text a step heavier than the design and, with the extra bottom pad,
// mis-derived every row height.
const TEXTAREA_FONT_SIZE_PX = 12.5;
const TEXTAREA_LINE_HEIGHT_RATIO = 1.55;
const TEXTAREA_LINE_HEIGHT_PX =
  TEXTAREA_FONT_SIZE_PX * TEXTAREA_LINE_HEIGHT_RATIO; // 19.375
const TEXTAREA_PADDING_PX = 14; // 10 top + 4 bottom
const DEFAULT_MIN_ROWS = 2;
const DEFAULT_MAX_ROWS = 8;

/* Design `.cmp`: ONE box. 11px radius, the `--line2` hairline, and NO padding
 * of its own — every child owns its inset (`.cmp textarea{padding:10px 12px
 * 4px}`, `.cmp-row{padding:6px 8px 8px}`, `.cmp-att{padding:9px 10px 0}`), so
 * the textarea and the action row sit flush against the shell edge instead of
 * inside a second inset frame.
 *
 * `--composer-shell-border` is a seam, not a token: the border has to be
 * authored inline (this component is mounted by hosts that don't ship a class
 * for it), and an inline shorthand can't be overridden by a CSS `:focus-within`
 * rule. Reading a custom property lets composer.css darken the border on focus
 * (design `.cmp:focus-within{border-color:var(--line3)}`) with no `!important`
 * and no accent ring. */
const containerStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--composer-shell-border, var(--color-border-strong))",
  borderRadius: 11,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 0,
  fontFamily: "var(--font-sans)",
  color: "var(--color-text)",
  position: "relative",
};

const textareaStyle: CSSProperties = {
  /* Flush inside the shell — no fill, no border, no radius. The previous
   * elevated-bg + hairline + 8px radius drew a second box inside the already
   * bordered shell (the visible "double box"). */
  background: "transparent",
  color: "var(--color-text)",
  border: 0,
  borderRadius: 0,
  padding: "10px 12px 4px",
  fontSize: TEXTAREA_FONT_SIZE_PX,
  lineHeight: TEXTAREA_LINE_HEIGHT_RATIO,
  resize: "none",
  outline: "none",
  width: "100%",
  fontFamily: "inherit",
  /* minHeight/maxHeight are applied inline by ComposerInner so the
   * caller can drive them via minRows / maxRows props. */
  overflowY: "auto",
};

/* Design `.cmp-row{gap:5px;padding:6px 8px 8px}`. The inset lives here (and on
 * the other direct children) now that the shell itself is padding-free. */
const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 5,
  padding: "6px 8px 8px",
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
  fontSize: "var(--font-size-md)",
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
  fontSize: "var(--font-size-xs)",
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
  fontSize: "var(--font-size-md)",
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
  fontSize: "var(--font-size-xs)",
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
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
});

/* Design `.cmp-att{padding:9px 10px 0}` — shell-flush children own their
 * inset. */
const topBarSlotStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  minHeight: 0,
  padding: "9px 10px 0",
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
  /* Own inset (the shell is padding-free); the row sits under the action row
   * so it only needs side + bottom padding. */
  padding: "0 10px 8px",
  fontSize: "var(--font-size-2xs)",
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
  fontSize: "var(--font-size-2xs)",
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
  /* Design `.cmp-att{padding:9px 10px 0}` + the 2px the strip already
   * reserved below itself. */
  padding: "9px 10px 2px",
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
  fontSize: "var(--font-size-xs)",
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
  fontSize: "var(--font-size-2xs)",
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
  fontSize: "var(--font-size-2xs)",
  flexShrink: 0,
};
