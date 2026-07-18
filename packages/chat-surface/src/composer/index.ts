export {
  Composer,
  type ComposerProps,
  type ComposerHandle,
  type ComposerMode,
  type ComposerSubmitPayload,
  type ComposerSlotCtx,
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
