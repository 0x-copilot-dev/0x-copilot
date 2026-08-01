export {
  Composer,
  type ComposerProps,
  type ComposerHandle,
  type ComposerMode,
  type ComposerSubmitPayload,
  type ComposerSlotCtx,
  type ComposerDictationControl,
  type ComposerDictationState,
  type AttachmentAdapter,
  type AttachmentContentPart,
  type CompleteAttachment,
  type PendingAttachment,
} from "./Composer";
export {
  ToolPicker,
  type ToolPickerProps,
  type ComposerToolDescriptor,
  type ComposerToolKind,
} from "./ToolPicker";
export {
  ModelPicker,
  type ModelPickerProps,
  type ModelDescriptor,
  type Depth,
  listModelDescriptors,
  listDepthDescriptors,
} from "./ModelPicker";
export {
  MentionPopover,
  type MentionPopoverProps,
  type MentionCandidate,
} from "./MentionPopover";
// === Phase 1 (PR-1.2) composer sub-controls ===
// Advanced-composer / topbar leaf controls hoisted from apps/frontend behind
// props (no substrate globals; the host keeps ChatScreen's data wiring). The
// AssistantComposer shell + AttachmentPill land in PR-1.3.
export { ModelPill, type ModelPillProps } from "./ModelPill";
export {
  ThinkingDepthControl,
  type ThinkingDepthControlProps,
} from "./ThinkingDepthControl";
export { ComposerPlusMenu, type ComposerMenuView } from "./ComposerPlusMenu";
export {
  ComposerConnectorsButton,
  type ComposerConnectorsButtonProps,
} from "./ComposerConnectorsButton";
// `ThinkingDepth` (the advanced-composer reasoning-depth model) coexists with
// the base Composer's `Depth` / `listDepthDescriptors` above — FR-1.7 flags
// the duplication as intentional for Phase 1; reconciliation is deferred to
// Phase 3E. See composer/depth.ts for the boundary note.
export {
  THINKING_DEPTHS,
  DEFAULT_THINKING_DEPTH,
  isThinkingDepth,
  depthLabel,
  depthLabelForModel,
  depthDescription,
  modelSupportsDepth,
  type ThinkingDepth,
} from "./depth";
// === end Phase 1 (PR-1.2) ===
// === Phase 1 (PR-1.3) AssistantComposer shell ===
// The advanced-composer shell (topbar skill pills, bottom-bar tools row,
// send/stop, slash-cue) hoisted behind ports/slots: the file picker is a
// `FilePickerPort`, the `+` menu portal + outside-click is the host
// `renderPlusMenu` slot, and the runtime attachment bridge + instruction-prompt
// builders are host-bound. `AttachmentPill` + `fileAttachmentAccept` move too.
export {
  AssistantComposer,
  type AssistantComposerProps,
  type AssistantComposerPlusMenuSlotArgs,
  type DetailsPanelKind,
} from "./AssistantComposer";
export { AttachmentPill } from "./AttachmentPill";
export { fileAttachmentAccept } from "./fileAttachmentAccept";
// === end Phase 1 (PR-1.3) ===
// === Workspace folder grants — the folder bar above the composer ===
// The folder affordance is the BAR on the composer frame (PRD-FS-10), not a `+`
// menu row: a grant copies nothing into the message and outlives it. Both are
// mounted by `AssistantComposer` from the optional `workspaceGrantPort` prop.
// The hook is the state half, exported because Settings shows the same list: the
// broker stays the source of truth (every change re-reads `listGrants`) and a
// failure is never rendered as an empty list. The port itself is host-owned.
export {
  WorkspaceFolderBar,
  WORKSPACE_FOLDER_BAR_EMPTY_LABEL,
  type WorkspaceFolderBarProps,
} from "./WorkspaceFolderBar";
export {
  mostRecentFirst,
  useWorkspaceFolderGrants,
  type WorkspaceFolderGrantsState,
} from "./useWorkspaceFolderGrants";
// === end Workspace folder grants ===
// === Filesystem bypass (PRD-FS-10 §4.3 the control, PRD-FS-11 the behaviour) ===
// The composer's execution-mode pill plus the two client-side questions it
// implies: may the control be offered (master switch), and is a selection
// spent after a send (scope). The AUTHORITY is server-side — the runtime folds
// master ▸ run ▸ message and re-checks the grant bound — so nothing exported
// here can widen anything.
export {
  BypassPill,
  type BypassPillProps,
  BYPASS_BOUND_NOTE,
  BYPASS_BOUND_SUB,
  BYPASS_DISABLED_TOOLTIP,
} from "./BypassPill";
export {
  MANUAL_BYPASS_STATE,
  bypassSelectionForSend,
  bypassStateAfterSend,
  type FilesystemBypassMode,
  type FilesystemBypassScope,
  type FilesystemBypassSelection,
  type FilesystemBypassState,
} from "./filesystemBypass";
// === end Filesystem bypass ===
// === Model-pill memory — the pick survives a remount ===
// `KeyValueStore`-backed memory of the model the user picked (per chat + a
// last-used fallback). The pill itself stays presentational: the HOST binder
// reads a remembered id when resolving its initial selection and writes back on
// every pick. Substrate-free — the port is the only storage touchpoint.
export {
  createComposerModelPreference,
  COMPOSER_MODEL_PREFERENCE_KEY,
  COMPOSER_MODEL_PREFERENCE_CHAT_LIMIT,
  type ComposerModelPreference,
  type ComposerModelPreferenceOptions,
} from "./modelPreference";
// === end Model-pill memory ===
