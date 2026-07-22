import type {
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@0x-copilot/api-types";
import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from "react";
import {
  Composer,
  type AttachmentAdapter,
  type ComposerHandle,
} from "./Composer";
import { Icon } from "../icons/Icon";
import type { FilePickerPort } from "../ports/FilePickerPort";
import type { ThinkingDepth } from "./depth";
import { ModelPill } from "./ModelPill";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
import type { KeyFormConnected } from "../onboarding/KeyForm";
import { ThinkingDepthControl } from "./ThinkingDepthControl";
import { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";
import { fileAttachmentAccept } from "./fileAttachmentAccept";

export type DetailsPanelKind = "context" | "usage";

/**
 * Render-prop arguments the composer core hands its host for the `+`
 * plus-menu popover slot. The core owns the anchor element, the open
 * state, and the dismissal action; the **host** owns the DOM-bound
 * portal + outside-click behaviour (both need `createPortal` / `window`
 * / `document`, which stay out of this substrate-agnostic package).
 *
 * - `open` — whether the menu should be shown.
 * - `anchorRef` — the `aui-plus-menu-root` element to position against.
 * - `onDismiss` — collapse the menu back to its root view (used by the
 *   host's outside-click handler).
 * - `children` — the already-rendered `<ComposerPlusMenu>` body.
 */
export interface AssistantComposerPlusMenuSlotArgs {
  readonly open: boolean;
  readonly anchorRef: RefObject<HTMLDivElement | null>;
  readonly onDismiss: () => void;
  readonly children: ReactNode;
}

export interface AssistantComposerProps {
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  /**
   * chat-surface attachment adapter (`add(file)` / `send(pending)` /
   * `remove(id)`). The host binds its runtime two-stage adapter through
   * the `bridgedAttachmentAdapter` bridge before handing it here so this
   * core stays free of the host's runtime attachment types.
   */
  attachmentAdapter?: AttachmentAdapter;
  /**
   * Substrate file picker. The `+` menu's Attach Image / Attach File
   * actions route through `filePicker.pick({ multiple, accept })` instead
   * of touching `document.createElement("input")` directly. The host binds
   * a File-backed implementation (web `<input type="file">`, desktop native
   * dialog) — the picked selections are handed to `addAttachment`, whose
   * runtime adapters need a real `File`.
   */
  filePicker: FilePickerPort;
  /**
   * Host slot for the `+` plus-menu popover (portal + outside-click). See
   * {@link AssistantComposerPlusMenuSlotArgs}.
   */
  renderPlusMenu: (args: AssistantComposerPlusMenuSlotArgs) => ReactNode;
  /**
   * Instruction-prompt builders. Injected so the core doesn't import the
   * host's `prompts` module. Behaviour (selected-skill prefixing on submit,
   * "use MCP server" instruction insertion) is unchanged.
   */
  skillInstructionPrompt: (displayName: string) => string;
  mcpServerInstructionPrompt: (displayName: string) => string;
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
  /**
   * FTUE P4 — additive slot for the connector-aware Tools trigger + its
   * popover, rendered next to `connectorsTrigger` in the bottom bar. The host
   * owns the DOM-bound portal (the package has no `document`). Additive: when
   * unset the bottom bar is byte-identical to before.
   */
  toolsTrigger?: ReactNode;
  /** PR 8.0.1 — display name of the active model, surfaced in the
   *  composer footer hint row. */
  activeModelLabel?: string;
  /** PR 8.0.2 — model + thinking-depth controls live here. */
  models?: Array<ModelCatalogModel & { disabled?: boolean }>;
  selectedModel?: string;
  onModelChange?: (id: string) => void;
  /** Register + select an arbitrary OpenRouter `vendor/model` slug. */
  onAddCustomModel?: (slug: string) => void;
  /**
   * When set, the ModelPill's "Add a provider key" footer opens an inline
   * `<KeyForm>` sub-view inside the model popover (saved through this port),
   * instead of the deep-link. Forwarded verbatim to {@link ModelPill}.
   */
  providerKeysPort?: ProviderKeysPort;
  /** Refresh seam fired after a successful inline add-key connect (see ModelPill). */
  onProviderKeyAdded?: (result: KeyFormConnected) => void;
  /**
   * Model-popover footer deep-link → Settings → Local models. Host-owned
   * navigation (the package never navigates). Forwarded verbatim to
   * {@link ModelPill}; when unset the footer link is not rendered.
   */
  onGetLocalModels?: () => void;
  /**
   * On-disk byte sizes of installed LOCAL models, keyed by name/id — the host
   * binder's join of `GET /v1/local-models` onto the model catalog. Forwarded
   * verbatim to {@link ModelPill}, where it turns a local row's sub-line into
   * the design's "42 GB · never leaves this machine".
   */
  localModelSizes?: Readonly<Record<string, number>>;
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
  /**
   * Optional error channel for a rejected async {@link onSubmit}. When the
   * host's `onSubmit` returns a promise that rejects (a failed `POST
   * /v1/agent/runs` — a missing provider key, a network error), the rejection
   * is routed here instead of being swallowed as an unhandled rejection. This
   * is the first-class replacement for a host having to wrap its own
   * `try/catch` around the composer's dispatch (see #158). When absent the
   * pre-existing behaviour is preserved (the rejection is still caught to
   * avoid an unhandled rejection, and `onClearSkills` still fires only on a
   * successful submit).
   */
  onSubmitError?: (error: unknown) => void;
  /** Stop-run handler. */
  onCancel?: () => void;
  /** Composer disabled (e.g. no active conversation row). */
  disabled?: boolean;
  /**
   * Starting textarea rows for the empty composer. Web keeps the roomy
   * default (3, the size tuned for the welcome-cards layout); the desktop
   * Run rail passes 2 for the compact v3 "quiet" composer shell.
   */
  minRows?: number;
  /**
   * Empty-composer placeholder. Defaults to the chat "Type a message…"; the
   * FTUE onboarding composer passes the SPEC hero placeholder. Optional so
   * every existing call site is unchanged.
   */
  placeholder?: string;
}

/**
 * Atlas composer. Wraps the single monorepo
 * `@0x-copilot/chat-surface` `<Composer>` with the Atlas-specific
 * `aui-*`-classed bottom bar (plus-menu, connectors trigger, mic, model
 * pill, depth control, send/stop) plus the selected-skills top-bar
 * pills. The chat-surface Composer owns text state, attachments, and
 * the imperative handle (setText/appendText/addAttachment/submit).
 * `@` stays plain text; `/` on an empty composer opens the skills
 * workspace pane (host-owned via onInputKeyDown).
 *
 * Substrate touchpoints are injected, not embedded, so this core stays
 * framework-agnostic (`no-restricted-globals` clean): the file picker is
 * a {@link FilePickerPort}, the `+` menu's portal + outside-click is a
 * host `renderPlusMenu` slot, and the instruction-prompt builders arrive
 * as props. The host binds the runtime `AttachmentAdapter` bridge (the
 * `add({file})` / `send(pending)` / `remove(attachment)` two-stage shape)
 * before handing the adapter here.
 *
 * The host (`ChatScreen`) forwards a `composerRef` so it can write to
 * the textarea imperatively (skill insertion path, post-OAuth resume
 * UI).
 */
export const AssistantComposer = forwardRef<
  ComposerHandle,
  AssistantComposerProps
>(function AssistantComposer(
  {
    connectors,
    skills,
    attachmentAdapter,
    filePicker,
    renderPlusMenu,
    skillInstructionPrompt,
    mcpServerInstructionPrompt,
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
    toolsTrigger,
    // activeModelLabel is still typed on the prop surface (callers haven't
    // been migrated) but the composer no longer surfaces it — the model
    // name lives in <ModelPill> only (Phase 9 dedup).
    activeModelLabel: _activeModelLabel,
    models,
    selectedModel,
    onModelChange,
    onAddCustomModel,
    providerKeysPort,
    onProviderKeyAdded,
    onGetLocalModels,
    localModelSizes,
    depth,
    onDepthChange,
    depthVisible,
    controlsDisabled,
    running = false,
    onSubmit,
    onSubmitError,
    onCancel,
    disabled = false,
    minRows = 3,
    placeholder = "Type a message…",
  },
  ref,
): ReactElement {
  const composerRef = useRef<ComposerHandle | null>(null);
  const slashCueTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  const dismissMenu = useCallback((): void => {
    setMenuOpen(false);
    setMenuView("root");
  }, []);

  useEffect(
    () => () => {
      if (slashCueTimeoutRef.current !== null) {
        clearTimeout(slashCueTimeoutRef.current);
      }
    },
    [],
  );

  const openFilePicker = useCallback(
    async (accept: string): Promise<void> => {
      const selections = await filePicker.pick({
        multiple: true,
        accept: [accept],
      });
      for (const selection of selections) {
        // The host binds a File-backed FilePickerPort — the runtime
        // attachment adapters read the picked file via
        // `FileReader.readAsDataURL(file)` and key on `file.lastModified`,
        // so a `File` (a structural superset of `FilePickerSelection`) is
        // required here.
        void composerRef.current?.addAttachment(selection as File);
      }
      if (selections.length > 0) {
        dismissMenu();
      }
    },
    [filePicker, dismissMenu],
  );

  function appendComposerInstruction(text: string): void {
    composerRef.current?.appendText(text);
    dismissMenu();
  }

  const showSlashCue = useCallback((text: string): void => {
    setSlashCueText(text);
    setSlashCueVisible(true);
    if (slashCueTimeoutRef.current !== null) {
      clearTimeout(slashCueTimeoutRef.current);
    }
    slashCueTimeoutRef.current = setTimeout(() => {
      setSlashCueVisible(false);
      slashCueTimeoutRef.current = null;
    }, 1400);
  }, []);

  function attachSkill(skill: Skill): void {
    onAttachSkill?.(skill);
    showSlashCue(`/${skill.name} attached`);
    dismissMenu();
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
      placeholder={placeholder}
      // Phase 9 composer redesign: empty composer was a single-row sliver
      // — felt skeletal next to the welcome cards. 3 rows is the size the
      // user identified as "what it should look like" (matches the focused
      // / multi-line state from earlier screenshots).
      // Hosts may override the starting rows (desktop rail passes 2).
      minRows={minRows}
      // v3 parity: the design's `.cmp textarea{max-height:130px}`. At the v3
      // metrics (12.5px × 1.55 line-height + 14px of vertical padding) 6 rows
      // lands on 130.25px; the previous 8 rows overshot to 176px.
      maxRows={6}
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
        //
        // RETURN the promise (don't `void` it): the inner Composer captures
        // it at its onSubmit call sites and `.catch`es a rejection into
        // `onSubmitError` (threaded below) — the single mechanism, so no host
        // has to re-wrap this in its own try/catch. `onClearSkills` still runs
        // ONLY on a successful submit (the `.then` is skipped on rejection),
        // so selected skills survive a failed send and a retry keeps them.
        return Promise.resolve(
          onSubmit({
            text,
            attachments:
              payload.attachments as unknown as ReadonlyArray<unknown>,
          }),
        ).then(() => onClearSkills?.());
      }}
      onSubmitError={onSubmitError}
      onCancel={onCancel}
      onInputKeyDown={handleInputKeyDown}
      hasTopBarContent={selectedSkills.length > 0}
      // Pass `undefined` (not `null`) when there's no topbar content —
      // chat-surface's Composer.tsx checks `topBarSlot !== undefined`
      // for the `data-has-topbar` flag, which the AUI CSS reads to
      // lift `--composer-shell-height` from 11rem → 13rem. `null` would
      // (incorrectly) trip that check and add ~32px of dead space below
      // the action row in the empty state.
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
              {/* Owner ruling: the affordance stays a PLUS (not the design's
               * paperclip) — but drawn, at the design's `.cmp-ic` metrics
               * (`.ui-cicon`: 26px square, 7px radius, 14px glyph). The old
               * literal "+" text node inherited the button font and never
               * matched the 14px icon tier next to it. */}
              <button
                className="aui-icon-button ui-cicon aui-composer-add-attachment"
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
                <Icon name="plus" size={14} />
              </button>
              {renderPlusMenu({
                open: menuOpen,
                anchorRef: menuRef,
                onDismiss: dismissMenu,
                children: (
                  <ComposerPlusMenu
                    view={menuView}
                    connectors={connectors}
                    skills={skills}
                    onBack={() => setMenuView("root")}
                    onAttachImage={() => void openFilePicker("image/*")}
                    onAttachFile={() =>
                      void openFilePicker(fileAttachmentAccept)
                    }
                    onOpenMcp={() => setMenuView("mcp")}
                    onOpenSkills={() => setMenuView("skills")}
                    onOpenMcpSettings={onOpenMcpSettings}
                    onOpenSkillsSettings={onOpenSkillsSettings}
                    onShowConnectors={() => {
                      onShowConnectors();
                      dismissMenu();
                    }}
                    onUseMcpServer={(server) =>
                      appendComposerInstruction(
                        mcpServerInstructionPrompt(server.display_name),
                      )
                    }
                    onUseSkill={(skill) => attachSkill(skill)}
                  />
                ),
              })}
            </div>
            {connectorsTrigger ?? null}
            {toolsTrigger ?? null}
            {/* v3 bottom-row order (owner ruling): [+] → tools → model → depth
             * on the left; mic + send flush right. The old `+ · tools · mic ·
             * divider · model` grouping and the divider itself have no
             * counterpart in the design's single-rhythm `.cmp-row`. */}
            {models && selectedModel !== undefined && onModelChange ? (
              <ModelPill
                models={models}
                value={selectedModel}
                onChange={onModelChange}
                disabled={controlsDisabled}
                onAddCustom={onAddCustomModel}
                providerKeysPort={providerKeysPort}
                onProviderKeyAdded={onProviderKeyAdded}
                onGetLocalModels={onGetLocalModels}
                localModelSizes={localModelSizes}
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
          {/* Right cluster. `margin-left: auto` (composer.css) is what pushes
           * it flush right now that the static hint row — whose
           * `margin-left: auto` used to do the pushing — is gone. */}
          <div className="aui-composer-action-wrapper__right">
            <button
              type="button"
              className="aui-icon-button ui-cicon atlas-composer-mic"
              aria-label="Voice input (coming soon)"
              data-tooltip="Voice input"
              disabled
            >
              <svg
                viewBox="0 0 24 24"
                width="14"
                height="14"
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
      // No static hint row (owner ruling). The design's `.cmp-hint` carries
      // "⏎ send · ⇧⏎ line"; the owner does not want that, nor the previous
      // "/ skills" cue or the "Sources cited inline" flag, so the row is gone
      // rather than restyled. (The earlier comment here claimed the mock shows
      // no send/newline hint — it does, at copilot-composer2.jsx:390; the row
      // is dropped by product choice, not by parity.)
      //
      // `hintRender` MUST still be passed: omitting it falls back to
      // Composer's OWN built-in `↵ send · ⇧+↵ new line · / skills` row.
      // Returning null renders nothing at all (Composer skips the slot
      // wrapper). The transient `.aui-composer-slash-cue` toast that appears
      // while typing "/" is unaffected — it lives in the action row.
      hintRender={() => null}
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
      className="aui-send-button ui-csend aui-composer-send"
      aria-label="Send message"
      data-tooltip="Send message"
      disabled={sendDisabled}
      onClick={onSend}
    >
      {/* Design `.cmp-send svg{width:14px;height:14px}` — a drawn paper-plane
       * from the icon SSOT, not the literal "↑" text node (whose size and
       * weight tracked the button font instead of the icon tier). */}
      <Icon name="send" size={14} />
    </button>
  );
}
