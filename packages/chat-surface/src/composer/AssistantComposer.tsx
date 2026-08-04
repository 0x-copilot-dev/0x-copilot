import type {
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@0x-copilot/api-types";
import {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
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
import type { DictationPort } from "../ports/DictationPort";
import type { WorkspaceGrantPort } from "../ports/WorkspaceGrantPort";
import {
  mostRecentFirst,
  useWorkspaceFolderGrants,
} from "./useWorkspaceFolderGrants";
import { WorkspaceFolderBar } from "./WorkspaceFolderBar";
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
  /** Host-owned speech-to-text capability. Omitted disables the mic. */
  dictationPort?: DictationPort;
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
   * Substrate folder-grant capability — OPTIONAL, and its absence is meaningful.
   * Supplied (desktop) it mounts the {@link WorkspaceFolderBar} above the frame
   * and the {@link BypassPill} in the control row; omitted or null (web, tests)
   * NEITHER renders, because there is no folder to grant and no ask to bypass,
   * and a control that cannot work is worse than no control.
   *
   * Not folded into {@link FilePickerPort}: that seam is content upload and
   * deliberately exposes no path. See `ports/WorkspaceGrantPort`.
   */
  workspaceGrantPort?: WorkspaceGrantPort | null;
  /**
   * Has this chat already sent a message? HOST-SUPPLIED — never inferred here
   * (PRD-FS-10 §6.3). The hosts disagree about what counts as a message, so a
   * transcript-length guess inside the package would be wrong on one of them.
   *
   * Drives one thing: the folder bar is orientation ("this is what I'm working
   * on"), which is needed when STARTING, not mid-conversation where the
   * transcript already shows what the agent has been touching — so the bar
   * renders only while this is false.
   *
   * Defaults to **true** (bar absent). Every surface that is pre-first-message
   * by construction goes through `OnboardingComposer`, which passes false; a
   * host that says nothing is mid-conversation, and the safe default for a
   * forgotten prop is a missing bar rather than one on the wrong screen.
   */
  hasSentFirstMessage?: boolean;
  /**
   * Execution-mode pill (`<BypassPill>`) and its popover — PRD-FS-10 §4.2 puts
   * it in the slot the model pill vacates, immediately right of Tools, because
   * execution mode is the decision a user re-makes per task while model choice
   * is set-and-forget.
   *
   * A SLOT rather than data props. PRD-FS-10 shipped the pill mounted here off
   * `bypassMode` / `bypassMasterEnabled` / `onBypassModeChange`; PRD-FS-11
   * replaced that with a host-owned trigger, because the master switch is
   * SERVER-held (`WorkspaceBehaviorOverrides.filesystem_bypass_enabled`) and a
   * substrate-agnostic core must not learn to fetch it. Keeping both would give
   * one control two mount points and the mode two owners.
   *
   * Still gated here on `workspaceGrantPort` (see the render site): a host may
   * pass a trigger, but a composer with no grant capability never shows it.
   * Omitted → nothing renders, which is the correct web/test degradation.
   */
  bypassTrigger?: ReactNode;
  /**
   * Where this chat BELONGS — the project-filing zone (`<ProjectFilingChip>`),
   * rendered directly BELOW the composer frame.
   *
   * BELOW is the whole point, and it is why this is a second zone rather than a
   * tenth control in the action row. What the agent can REACH sits ABOVE the
   * frame (the folder bar: many folders, granted per chat); where the work
   * BELONGS sits BELOW it (one project, or none). Capability points up, filing
   * points down. Merging the two into one row was considered and rejected — it
   * reads as one list of "things attached to this message", which filing is not:
   * a grant is a per-chat capability, a project is a fact about the chat that
   * outlives every message in it.
   *
   * A SLOT, not data props, for the same reason as {@link bypassTrigger}: the
   * project list and the `project_id` write are host-owned (facade reads), and a
   * substrate-agnostic core must not learn to fetch them. Unlike the bypass
   * trigger it is NOT gated on `workspaceGrantPort` — filing is not a
   * filesystem capability, so it must render on web, where there is no grant
   * port and therefore never a folder bar.
   *
   * Omitted (or null) → the zone is absent and adds no height, which is the
   * correct degradation for a host that has no projects surface.
   */
  readonly projectFilingSlot?: ReactNode;
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
   * The run-scoped Tools pill and its anchored popover. It sits beside the
   * model control; hosts own its data wiring while the trigger itself portals
   * above the overflow-hidden composer frame.
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
   * Model-popover footer "Add a provider key" → Settings → Provider keys.
   * Host-owned navigation (the package never navigates). Preferred over
   * `providerKeysPort` when both are set, so the footer navigates to the one
   * Settings surface instead of opening an inline form. Forwarded to {@link ModelPill}.
   */
  onAddProviderKey?: () => void;
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
 * `aui-*`-classed bottom bar plus the selected-skills top-bar pills, and —
 * where the host has a `WorkspaceGrantPort` and the chat has not started —
 * the {@link WorkspaceFolderBar} above the frame.
 *
 * Control row, left to right (PRD-FS-10 §4.2):
 *   `+` · connectors · Tools · bypass · … · model · mic · send
 *
 * The chat-surface Composer owns text state, attachments, and the imperative
 * handle (setText/appendText/addAttachment/submit). `@` stays plain text; `/`
 * on an empty composer opens the skills workspace pane (host-owned via
 * onInputKeyDown).
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
    dictationPort,
    filePicker,
    workspaceGrantPort,
    hasSentFirstMessage = true,
    projectFilingSlot,
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
    bypassTrigger,
    // activeModelLabel is still typed on the prop surface (callers haven't
    // been migrated) but the composer no longer surfaces it — the model
    // name lives in <ModelPill> only (Phase 9 dedup).
    activeModelLabel: _activeModelLabel,
    models,
    selectedModel,
    onModelChange,
    onAddCustomModel,
    onAddProviderKey,
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

  // Called unconditionally (hook rules); a null port makes it inert. Only the
  // RENDER is gated on the capability — see `folderControlsVisible` below.
  const folderGrants = useWorkspaceFolderGrants(workspaceGrantPort);
  const folderControlsVisible =
    workspaceGrantPort !== undefined && workspaceGrantPort !== null;

  // Depend on the stable callback, not the state object the hook rebuilds each
  // render, so the bar's handler identity doesn't churn on every keystroke.
  const requestFolderGrant = folderGrants.requestGrant;
  const revokeFolderGrant = folderGrants.revokeGrant;
  const attachFolder = useCallback((): void => {
    void requestFolderGrant();
  }, [requestFolderGrant]);
  const revokeFolder = useCallback(
    (grantId: string): void => {
      void revokeFolderGrant(grantId);
    },
    [revokeFolderGrant],
  );

  // The bar NAMES one folder, so which one has to be decided here rather than
  // taken from the broker's list order — see `mostRecentFirst`.
  const barGrants = useMemo(
    () => mostRecentFirst(folderGrants.grants, folderGrants.lastGrantedId),
    [folderGrants.grants, folderGrants.lastGrantedId],
  );

  // The bar is a capability + a moment: the host must have a grant port, and
  // the chat must not have started yet.
  const folderBarVisible = folderControlsVisible && !hasSentFirstMessage;

  // Absent means ABSENT, for `undefined` (the host never wired filing) and for
  // `null` alike (the host wired it but has nothing to file into yet) — a host
  // that computes `projects.length > 0 ? <chip/> : null` is the natural binder
  // shape, and it must not leave an empty row under the composer.
  const filingZoneVisible =
    projectFilingSlot !== undefined && projectFilingSlot !== null;

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

  // The frame has up to two satellites, and which side each takes is the design
  // (see `projectFilingSlot`): the folder line sits ABOVE it, not inside it
  // (PRD-FS-10 §4.1) — context for what follows, in the place Claude Code and
  // Codex put the working folder — and the filing zone sits BELOW it. When
  // EITHER is shown the group is wrapped in `.aui-composer-stack` (see the
  // return), so the page's gap lands outside the group rather than between its
  // parts; with neither, this element is returned bare and every mount that has
  // no satellite is untouched.
  const composer = (
    <Composer
      ref={setComposerRef}
      className="aui-composer"
      disabled={disabled}
      running={running}
      attachmentAdapter={attachmentAdapter}
      dictationPort={dictationPort}
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
      bottomBarRender={({
        text,
        running: isRunning,
        attachmentsCount,
        dictation,
      }) => (
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
                // Left-most control in the row: a centred tooltip would hang
                // past the composer's left edge (and, in the Run cockpit's
                // narrow chat column, past the column itself). `start` grows it
                // inward instead. Same reason `end` is set on the right cluster.
                data-tooltip-align="start"
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
                    onEscape={dismissMenu}
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
            {/* PRD-FS-10 §4.2 — execution mode takes the slot the model pill
             * VACATED (the model now sits in the right cluster, left of the
             * mic): this is the first place the eye lands, and "will this run
             * ask me?" is the decision a user re-makes per task where model
             * choice is set-and-forget.
             *
             * A SLOT, gated on a CAPABILITY. The host owns the master switch
             * (server-held) and the mode/scope selection, so this core never
             * learns to fetch either — but the gate stays here, because it is a
             * property of the composer and not of any one host: bypass only
             * ever applies inside a folder the user granted with write
             * permission, so with no grant port there is nothing to ask about
             * and nothing bypass could permit. On web that makes it ABSENT
             * rather than a control that changes nothing. */}
            {folderControlsVisible ? (bypassTrigger ?? null) : null}
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
           * `margin-left: auto` used to do the pushing — is gone.
           *
           * ORDER (PRD-FS-10 §4.2): … model · mic · send. The model pill moved
           * out of the left cluster but stays LEFT of the mic — mic and send are
           * the trailing action pair and read as one group, so nothing is
           * dropped between them. DOM order is visual order, which is also the
           * tab order: keyboard focus still walks left to right. */}
          <div className="aui-composer-action-wrapper__right">
            {dictation.message !== null ? (
              <span
                className="aui-composer-dictation-status"
                role={dictation.state === "error" ? "alert" : "status"}
                data-testid="assistant-composer-dictation-status"
                data-state={dictation.state}
              >
                {dictation.message}
              </span>
            ) : null}
            {models && selectedModel !== undefined && onModelChange ? (
              <ModelPill
                models={models}
                value={selectedModel}
                onChange={onModelChange}
                disabled={controlsDisabled}
                onAddCustom={onAddCustomModel}
                onAddProviderKey={onAddProviderKey}
                providerKeysPort={providerKeysPort}
                onProviderKeyAdded={onProviderKeyAdded}
                onGetLocalModels={onGetLocalModels}
                localModelSizes={localModelSizes}
              />
            ) : null}
            <button
              type="button"
              className="aui-icon-button ui-cicon atlas-composer-mic"
              aria-label={
                dictation.active
                  ? "Stop voice input"
                  : dictation.state === "unavailable"
                    ? "Voice input unavailable"
                    : "Voice input"
              }
              aria-pressed={dictation.active}
              data-dictation-state={dictation.state}
              data-tooltip={
                dictation.active
                  ? "Stop voice input"
                  : dictation.state === "unavailable"
                    ? "Voice input unavailable"
                    : "Voice input"
              }
              data-tooltip-align="end"
              onClick={dictation.toggle}
              disabled={
                dictation.state === "unavailable" ||
                dictation.state === "stopping" ||
                disabled ||
                controlsDisabled
              }
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

  // The stack is the wrapper for the frame AND its satellites, so it is needed
  // as soon as EITHER exists — gating it on the bar alone (which is what this
  // was) would have dropped the filing zone on web, where there is no
  // `WorkspaceGrantPort` and therefore never a bar.
  if (!folderBarVisible && !filingZoneVisible) {
    return composer;
  }

  return (
    // ONE element, not a fragment. The frame and its satellites are a single
    // unit — the bar describes what THIS composer can reach, the filing zone
    // where its output belongs — but as sibling fragment children they became
    // independent flex children of whatever laid the composer out, so the
    // PAGE's gap landed between them. In the FTUE that is
    // `.fr-compose { gap: var(--space-lg) }`, which pushed the bar ~16px clear
    // of the frame and made it read as unrelated page furniture. Wrapping makes
    // the group take the page gap ONCE, from outside, and lets each satellite
    // sit tight to the frame it belongs to (`.aui-composer-stack`'s own 6px gap
    // is the whole distance for both joins).
    <div className="aui-composer-stack">
      {folderBarVisible ? (
        <WorkspaceFolderBar
          grants={barGrants}
          error={folderGrants.error}
          busy={folderGrants.busy}
          onAttach={attachFolder}
          onRevoke={revokeFolder}
        />
      ) : null}
      {composer}
      {filingZoneVisible ? projectFilingSlot : null}
    </div>
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
        data-tooltip-align="end"
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
      // Right-most control in the row — see the `+` button's `start` note.
      data-tooltip-align="end"
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
