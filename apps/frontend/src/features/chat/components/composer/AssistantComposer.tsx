import type {
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@enterprise-search/api-types";
import {
  Composer,
  type AttachmentAdapter as ChatSurfaceAttachmentAdapter,
  type ComposerHandle,
  type CompleteAttachment as ChatSurfaceCompleteAttachment,
  type PendingAttachment as ChatSurfacePendingAttachment,
} from "@enterprise-search/chat-surface";
import {
  forwardRef,
  useEffect,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import type { ThinkingDepth } from "../../depth";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "../../prompts";
import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../../runtime/types";
import { ModelPill } from "../shell/ModelPill";
import { ThinkingDepthControl } from "../shell/ThinkingDepthControl";
import { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";
import { fileAttachmentAccept } from "./fileAttachmentAccept";

export type DetailsPanelKind = "context" | "usage";

/**
 * Atlas composer. Wraps the single monorepo
 * `@enterprise-search/chat-surface` `<Composer>` with the Atlas-specific
 * `aui-*`-classed bottom bar (plus-menu, connectors trigger, mic, model
 * pill, depth control, send/stop) plus the selected-skills top-bar
 * pills. The chat-surface Composer owns text state, attachments, and
 * the imperative handle (setText/appendText/addAttachment/submit).
 * `@` stays plain text; `/` on an empty composer opens the skills
 * workspace pane (host-owned via onInputKeyDown).
 *
 * Runtime `AttachmentAdapter`s ({@link AtlasCompositeAttachmentAdapter}
 * et al.) flow through unchanged — `bridgedAttachmentAdapter` adapts
 * their `add({file})` / `remove(attachment)` / `send(pending)` two-stage
 * shape to chat-surface's `add(file)` / `remove(id)` / `send(pending)`.
 *
 * The host (`ChatScreen`) forwards a `composerRef` so it can write to
 * the textarea imperatively (skill insertion path, post-OAuth resume
 * UI).
 */
export const AssistantComposer = forwardRef<
  ComposerHandle,
  {
    connectors: {
      servers: McpServer[];
      loading: boolean;
    };
    skills: {
      skills: Skill[];
      loading: boolean;
    };
    attachmentAdapter?: AttachmentAdapter;
    onOpenMcpSettings: () => void;
    onOpenSkillsSettings: () => void;
    onShowConnectors: () => void;
    onOpenDetailsPanel?: (kind: DetailsPanelKind) => void;
    onOpenSkillsPanel?: () => void;
    selectedSkills?: readonly Skill[];
    onAttachSkill?: (skill: Skill) => void;
    onRemoveSkill?: (skillId: string) => void;
    onClearSkills?: () => void;
    /**
     * PR 3.4 — slot for the per-chat connectors trigger + its popover.
     */
    connectorsTrigger?: ReactNode;
    /** PR 8.0.1 — display name of the active model, surfaced in the
     *  composer footer hint row. */
    activeModelLabel?: string;
    /** PR 8.0.2 — model + thinking-depth controls live here. */
    models?: Array<ModelCatalogModel & { disabled?: boolean }>;
    selectedModel?: string;
    onModelChange?: (id: string) => void;
    depth?: ThinkingDepth;
    onDepthChange?: (depth: ThinkingDepth) => void;
    depthVisible?: boolean;
    controlsDisabled?: boolean;
    /**
     * Whether a run is in flight. When true the Send button is
     * replaced with a Stop button that fires `onCancel`.
     */
    running?: boolean;
    /** Submission. The host wraps `text` + `attachments` into an
     *  `AppendMessage` shape and dispatches the run. */
    onSubmit: (payload: {
      text: string;
      attachments: ReadonlyArray<unknown>;
    }) => void | Promise<void>;
    /** Stop-run handler. */
    onCancel?: () => void;
    /** Composer disabled (e.g. no active conversation row). */
    disabled?: boolean;
  }
>(function AssistantComposer(
  {
    connectors,
    skills,
    attachmentAdapter,
    onOpenMcpSettings,
    onOpenSkillsSettings,
    onShowConnectors,
    onOpenDetailsPanel: _onOpenDetailsPanel,
    onOpenSkillsPanel,
    selectedSkills = [],
    onAttachSkill,
    onRemoveSkill,
    onClearSkills,
    connectorsTrigger,
    // activeModelLabel is still typed on the prop surface (callers haven't
    // been migrated) but the composer no longer surfaces it — the model
    // name lives in <ModelPill> only (Phase 9 dedup).
    activeModelLabel: _activeModelLabel,
    models,
    selectedModel,
    onModelChange,
    depth,
    onDepthChange,
    depthVisible,
    controlsDisabled,
    running = false,
    onSubmit,
    onCancel,
    disabled = false,
  },
  ref,
): ReactElement {
  const composerRef = useRef<ComposerHandle | null>(null);
  const slashCueTimeoutRef = useRef<number | null>(null);
  // Bridge the public forwardRef to the inner Composer ref, while
  // keeping a local handle for the plus-menu to call addAttachment.
  const setComposerRef = (handle: ComposerHandle | null): void => {
    composerRef.current = handle;
    if (typeof ref === "function") {
      ref(handle);
    } else if (ref) {
      ref.current = handle;
    }
  };

  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuView, setMenuView] = useState<ComposerMenuView>("root");
  const [slashCueVisible, setSlashCueVisible] = useState(false);
  const [slashCueText, setSlashCueText] = useState("/ skills");

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: PointerEvent): void {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
        setMenuView("root");
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);

  useEffect(
    () => () => {
      if (slashCueTimeoutRef.current !== null) {
        window.clearTimeout(slashCueTimeoutRef.current);
      }
    },
    [],
  );

  function openFilePicker(accept: string): void {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept = accept;
    input.hidden = true;
    document.body.appendChild(input);
    input.onchange = () => {
      const files = input.files;
      if (files) {
        for (const file of files) {
          void composerRef.current?.addAttachment(file);
        }
      }
      input.remove();
      setMenuOpen(false);
      setMenuView("root");
    };
    input.oncancel = () => input.remove();
    input.click();
  }

  function appendComposerInstruction(text: string): void {
    composerRef.current?.appendText(text);
    setMenuOpen(false);
    setMenuView("root");
  }

  const showSlashCue = useCallback((text: string): void => {
    setSlashCueText(text);
    setSlashCueVisible(true);
    if (slashCueTimeoutRef.current !== null) {
      window.clearTimeout(slashCueTimeoutRef.current);
    }
    slashCueTimeoutRef.current = window.setTimeout(() => {
      setSlashCueVisible(false);
      slashCueTimeoutRef.current = null;
    }, 1400);
  }, []);

  function attachSkill(skill: Skill): void {
    onAttachSkill?.(skill);
    showSlashCue(`/${skill.name} attached`);
    setMenuOpen(false);
    setMenuView("root");
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  const handleInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>): void => {
      if (event.key !== "/" || event.currentTarget.value.trim().length > 0) {
        return;
      }
      event.preventDefault();
      showSlashCue("/ skills");
      onOpenSkillsPanel?.();
    },
    [onOpenSkillsPanel, showSlashCue],
  );

  // Bridge the runtime two-stage AttachmentAdapter to the chat-surface
  // Composer's adapter shape. The runtime adapter family takes
  // `add({ file })` / `remove(attachment)` and never stamps a `size`
  // on the attachment; the chat-surface Composer expects `add(file)`,
  // `remove(id)`, and reads `size` for the pill render. We translate
  // here so the runtime adapters (image/text/file/composite) keep
  // working unchanged. An id→runtime-attachment registry keeps the
  // chat-surface remove call routable back to the right runtime
  // adapter (composite dispatches on the file's MIME type, so it
  // needs the original attachment).
  const adapterRegistryRef = useRef<Map<string, Attachment>>(new Map());
  const bridgedAttachmentAdapter = useMemo<
    ChatSurfaceAttachmentAdapter | undefined
  >(() => {
    if (!attachmentAdapter) return undefined;
    const runtime: AttachmentAdapter = attachmentAdapter;
    const registry = adapterRegistryRef.current;
    return {
      async add(file: File): Promise<ChatSurfacePendingAttachment> {
        const pending = await runtime.add({ file });
        registry.set(pending.id, pending);
        return {
          id: pending.id,
          name: pending.name,
          // `pending.contentType` is only populated by some adapters;
          // fall back to the file's MIME type so the chat-surface pill
          // still renders a sensible label.
          type: pending.contentType ?? file.type ?? pending.type,
          size: file.size,
          // The chat-surface "pending" status union is narrower than the
          // runtime's `requires-action | running`; both map to "pending"
          // for the chat-surface pill — the runtime adapter is the one
          // that knows how to finalise.
          status: { type: "pending" },
          handle: pending,
        };
      },
      async send(
        pendingShim: ChatSurfacePendingAttachment,
      ): Promise<ChatSurfaceCompleteAttachment> {
        const runtimePending = registry.get(pendingShim.id) as
          | PendingAttachment
          | undefined;
        if (!runtimePending) {
          throw new Error(
            `No runtime attachment registered for id ${pendingShim.id}`,
          );
        }
        const completed = await runtime.send(runtimePending);
        registry.set(completed.id, completed);
        // Forward the runtime CompleteAttachment verbatim plus the
        // chat-surface display fields. `onSubmit` downstream reads it
        // as a runtime CompleteAttachment (id/type/name/contentType/
        // content/file); we preserve every runtime field so the
        // run-create pipeline can build its `attachments[]` body.
        // chat-surface's CompleteAttachment is a structural superset
        // (adds size + optional handle); the only TS gap is the
        // `AttachmentContentPart` element shape (runtime: strict union;
        // chat-surface: `{type; [k]: unknown}`), so we widen via
        // `unknown` at the slot boundary.
        const bridged = {
          ...completed,
          size: pendingShim.size,
          handle: completed,
          status: { type: "complete" as const },
        };
        return bridged as unknown as ChatSurfaceCompleteAttachment;
      },
      async remove(id: string): Promise<void> {
        const attachment = registry.get(id);
        registry.delete(id);
        if (attachment) {
          await runtime.remove(attachment);
        }
      },
    };
  }, [attachmentAdapter]);

  return (
    <Composer
      ref={setComposerRef}
      className="aui-composer"
      disabled={disabled}
      running={running}
      attachmentAdapter={bridgedAttachmentAdapter}
      placeholder="Type a message…"
      // Phase 9 composer redesign: empty composer was a single-row sliver
      // — felt skeletal next to the welcome cards. 3 rows is the size the
      // user identified as "what it should look like" (matches the focused
      // / multi-line state from earlier screenshots). maxRows lifted to 8
      // so multi-line drafts have headroom before internal scroll kicks in.
      minRows={3}
      maxRows={8}
      onSubmit={(payload) => {
        const skillInstructions = selectedSkills.map((skill) =>
          skillInstructionPrompt(skill.display_name),
        );
        const text = [...skillInstructions, payload.text]
          .filter((part) => part.trim().length > 0)
          .join("\n\n");
        // The bridged adapter returns chat-surface CompleteAttachments
        // that ALSO carry the runtime fields (id/type/name/contentType/
        // content[]); the host's onSubmit reads them as runtime
        // CompleteAttachments downstream. Cast through unknown rather
        // than spreading so the structural superset stays intact.
        void Promise.resolve(
          onSubmit({
            text,
            attachments:
              payload.attachments as unknown as ReadonlyArray<unknown>,
          }),
        ).then(() => onClearSkills?.());
      }}
      onCancel={onCancel}
      onInputKeyDown={handleInputKeyDown}
      hasTopBarContent={selectedSkills.length > 0}
      // Pass `undefined` (not `null`) when there's no topbar content —
      // chat-surface's Composer.tsx checks `topBarSlot !== undefined`
      // for the `data-has-topbar` flag, which the AUI CSS reads to
      // lift `--composer-shell-height` from 11rem → 13rem. `null` would
      // (incorrectly) trip that check and add ~32px of dead space below
      // the hint row in the empty state.
      topBarSlot={
        selectedSkills.length > 0 ? (
          <div className="aui-composer-attachments">
            {selectedSkills.map((skill) => (
              <span key={skill.skill_id} className="aui-skill-pill">
                <code>/{skill.name}</code>
                <span>{skill.display_name}</span>
                {onRemoveSkill ? (
                  <button
                    type="button"
                    className="aui-skill-pill__remove"
                    aria-label={`Remove ${skill.display_name} skill`}
                    onClick={() => onRemoveSkill(skill.skill_id)}
                  >
                    ×
                  </button>
                ) : null}
              </span>
            ))}
          </div>
        ) : undefined
      }
      bottomBarRender={({ text, running: isRunning, attachmentsCount }) => (
        <div className="aui-composer-action-wrapper">
          <div className="aui-composer-tools">
            <div className="aui-plus-menu-root" ref={menuRef}>
              <button
                className="aui-icon-button aui-composer-add-attachment"
                type="button"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label="Open attachment and tools menu"
                data-tooltip="Add attachment"
                onClick={() => {
                  setMenuOpen((current) => !current);
                  setMenuView("root");
                }}
              >
                +
              </button>
              <AnchoredPlusMenu open={menuOpen} anchorRef={menuRef}>
                <ComposerPlusMenu
                  view={menuView}
                  connectors={connectors}
                  skills={skills}
                  onBack={() => setMenuView("root")}
                  onAttachImage={() => openFilePicker("image/*")}
                  onAttachFile={() => openFilePicker(fileAttachmentAccept)}
                  onOpenMcp={() => setMenuView("mcp")}
                  onOpenSkills={() => setMenuView("skills")}
                  onOpenMcpSettings={onOpenMcpSettings}
                  onOpenSkillsSettings={onOpenSkillsSettings}
                  onShowConnectors={() => {
                    onShowConnectors();
                    setMenuOpen(false);
                    setMenuView("root");
                  }}
                  onUseMcpServer={(server) =>
                    appendComposerInstruction(
                      mcpServerInstructionPrompt(server.display_name),
                    )
                  }
                  onUseSkill={(skill) => attachSkill(skill)}
                />
              </AnchoredPlusMenu>
            </div>
            {connectorsTrigger ?? null}
            <button
              type="button"
              className="aui-icon-button atlas-composer-mic"
              aria-label="Voice input (coming soon)"
              data-tooltip="Voice input"
              disabled
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="9" y="3" width="6" height="12" rx="3" />
                <path d="M5 11a7 7 0 0 0 14 0" />
                <path d="M12 18v3" />
              </svg>
            </button>
            {/* Visual divider between the accent-icon cluster (+ / connectors
             * / mic) and the setting-pill cluster (Model · Depth). Gives the
             * toolbar visual rhythm so the eye groups controls by intent
             * instead of scanning a uniform-gap strip. */}
            <span className="aui-composer-tools-spacer" aria-hidden="true" />
            {models && selectedModel !== undefined && onModelChange ? (
              <ModelPill
                models={models}
                value={selectedModel}
                onChange={onModelChange}
                disabled={controlsDisabled}
              />
            ) : null}
            {depth !== undefined && onDepthChange ? (
              <ThinkingDepthControl
                value={depth}
                onChange={onDepthChange}
                visible={depthVisible ?? true}
                disabled={controlsDisabled}
              />
            ) : null}
          </div>
          <div className="aui-composer-action-wrapper__right">
            {/* Phase 9 composer cleanup: dropped the focus-meta line — the
             * model name was already shown in <ModelPill> above, and the
             * "Sources cited inline" mode flag lives in the hint row.
             * Keeping it here was duplicate signal for the same surface. */}
            <AssistantComposerSendButton
              text={text}
              attachmentsCount={attachmentsCount}
              running={isRunning}
              disabled={disabled}
              onSend={() => composerRef.current?.submit()}
              onCancel={onCancel}
            />
          </div>
          {slashCueVisible ? (
            <span className="aui-composer-slash-cue" role="status">
              {slashCueText.startsWith("/") ? (
                <>
                  <kbd>/</kbd>
                  {slashCueText.slice(1)}
                </>
              ) : (
                slashCueText
              )}
            </span>
          ) : null}
        </div>
      )}
      hintRender={() => (
        // Hint row is stateless info — render it unconditionally. Do
        // NOT gate on `running` (or any other run-state flag); hiding
        // shortcuts mid-flight makes the composer look broken. See
        // apps/frontend/CLAUDE.md → "Composer hint row".
        <div className="aui-composer__hint" aria-hidden="false">
          <span>
            <kbd>↵</kbd> send
          </span>
          <span className="aui-composer__hint-sep" aria-hidden="true" />
          <span>
            <kbd>⇧</kbd>+<kbd>↵</kbd> new line
          </span>
          <span className="aui-composer__hint-sep" aria-hidden="true" />
          <span>
            <kbd>/</kbd> skills
          </span>
          <span className="aui-composer__hint-grow" />
          {/* Trailing meta: just the mode flag. Model name lives in the
           * ModelPill above (one source of truth); duplicating it here was
           * visual noise and made the hint row look busy. */}
          <span className="aui-composer__hint-meta">Sources cited inline</span>
        </div>
      )}
    />
  );
});

/**
 * Atlas send / stop button. Renders the Stop control while a run is
 * in flight; otherwise a Send control disabled when the composer is
 * empty (no text AND no staged attachments). Replaces the previous
 * runtime-composer `<ComposerSendButton>` — same shape, kept inline
 * because no other call site needs it.
 */
function AssistantComposerSendButton({
  text,
  attachmentsCount,
  running,
  disabled,
  onSend,
  onCancel,
}: {
  text: string;
  attachmentsCount: number;
  running: boolean;
  disabled?: boolean;
  onSend: () => void;
  onCancel?: () => void;
}): ReactElement {
  if (running) {
    return (
      <button
        type="button"
        className="aui-send-button aui-send-button--stop"
        aria-label="Stop response"
        data-tooltip="Stop response"
        onClick={() => onCancel?.()}
      >
        <span className="aui-send-button__stop-icon" aria-hidden="true" />
      </button>
    );
  }
  const sendDisabled =
    disabled || (text.trim().length === 0 && attachmentsCount === 0);
  return (
    <button
      type="button"
      className="aui-send-button aui-composer-send"
      aria-label="Send message"
      data-tooltip="Send message"
      disabled={sendDisabled}
      onClick={onSend}
    >
      ↑
    </button>
  );
}

/**
 * Portal + fixed-position wrapper for the `+` plus-menu popup.
 *
 * The composer card has ``overflow: hidden`` and a fixed
 * ``--composer-shell-height``, so an absolutely-positioned popup
 * inside the card gets clipped (or worse, overlays the textarea
 * because the card is tall enough to "fit" it). Rendering the popup
 * at ``document.body`` with ``position: fixed`` coords computed from
 * the anchor's bounding rect lets it escape the composer entirely
 * and sit above the card the way every other dropdown in the app
 * does.
 *
 * Mirrors the design-system ``Menu`` primitive's positioning logic
 * (PR 4.4.6 fix) — kept inline here because ``ComposerPlusMenu``
 * isn't built on top of ``Menu`` and rolling its own fixed-position
 * shell beats refactoring its 200-line body.
 */
function AnchoredPlusMenu({
  open,
  anchorRef,
  children,
}: {
  open: boolean;
  anchorRef: { current: HTMLElement | null };
  children: ReactNode;
}): ReactElement | null {
  const [style, setStyle] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open) return;
    const compute = (): void => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const SPACE = 8;
      setStyle({
        position: "fixed",
        bottom: window.innerHeight - rect.top + SPACE,
        left: rect.left,
        zIndex: 50,
      });
    };
    compute();
    window.addEventListener("resize", compute);
    window.addEventListener("scroll", compute, true);
    return () => {
      window.removeEventListener("resize", compute);
      window.removeEventListener("scroll", compute, true);
    };
  }, [open, anchorRef]);

  if (!open) return null;
  if (typeof document === "undefined") return null;
  return createPortal(<div style={style}>{children}</div>, document.body);
}
