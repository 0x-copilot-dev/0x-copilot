import type {
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@enterprise-search/api-types";
import {
  forwardRef,
  useEffect,
  useCallback,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import type { ThinkingDepth } from "../../depth";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "../../prompts";
import {
  Composer,
  ComposerSendButton,
  type ComposerHandle,
} from "../../runtime/composer";
import type { AttachmentAdapter } from "../../runtime/types";
import { ModelPill } from "../shell/ModelPill";
import { ThinkingDepthControl } from "../shell/ThinkingDepthControl";
import { AttachmentPill } from "./AttachmentPill";
import { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";
import { fileAttachmentAccept } from "./fileAttachmentAccept";

export type DetailsPanelKind = "context" | "usage";

/**
 * Atlas composer. Replaces the previous `ComposerPrimitive` + `AuiIf` +
 * `unstable_useMentionAdapter` + `unstable_useSlashCommandAdapter`
 * implementation with a self-owned `<Composer>` from
 * `runtime/composer`. `@` remains plain text; `/` opens the skills
 * workspace tab when typed into an empty composer, while skill insertion
 * still flows through the workspace-pane skills tab and the `+` plus-menu.
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
    activeModelLabel,
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

  return (
    <Composer
      ref={setComposerRef}
      className="aui-composer"
      disabled={disabled}
      running={running}
      attachmentAdapter={attachmentAdapter}
      placeholder="Ask Atlas to find, summarize, or draft something for your team…"
      minRows={1}
      maxRows={5}
      onSubmit={async (payload) => {
        const skillInstructions = selectedSkills.map((skill) =>
          skillInstructionPrompt(skill.display_name),
        );
        const text = [...skillInstructions, payload.text]
          .filter((part) => part.trim().length > 0)
          .join("\n\n");
        await onSubmit({ text, attachments: payload.attachments });
        onClearSkills?.();
      }}
      onCancel={onCancel}
      onInputKeyDown={handleInputKeyDown}
      hasTopBarContent={selectedSkills.length > 0}
      topBar={({ attachments, onRemove }) =>
        attachments.length > 0 || selectedSkills.length > 0 ? (
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
            {attachments.map((attachment) => (
              <AttachmentPill
                key={attachment.id}
                attachment={attachment}
                onRemove={() => onRemove(attachment.id)}
              />
            ))}
          </div>
        ) : null
      }
      bottomBar={({ text, running: isRunning, attachmentsCount, focused }) => (
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
              {menuOpen ? (
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
              ) : null}
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
            {focused ? (
              <span className="aui-composer-focus-meta" aria-hidden="true">
                {activeModelLabel ?? "Atlas"} · Sources cited inline
              </span>
            ) : null}
            <ComposerSendButton
              text={text}
              attachmentsCount={attachmentsCount}
              running={isRunning}
              disabled={disabled}
              onSend={() => void composerRef.current?.submit()}
              onCancel={onCancel}
              className="aui-send-button aui-composer-send"
              stopClassName="aui-send-button aui-send-button--stop"
              sendIcon="↑"
              stopIcon={
                <span
                  className="aui-send-button__stop-icon"
                  aria-hidden="true"
                />
              }
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
      hint={
        running ? null : (
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
            <span className="aui-composer__hint-meta">
              {activeModelLabel ?? "Atlas"} · Sources cited inline
            </span>
          </div>
        )
      }
    />
  );
});
