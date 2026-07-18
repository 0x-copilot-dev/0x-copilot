export type { ArtifactRoute, NavigateOptions, Router } from "./routing/router";
export {
  ARTIFACT_SCHEMES,
  buildArtifactUri,
  isArtifactScheme,
  parseArtifactUri,
  type ArtifactScheme,
  type ParsedArtifactUri,
} from "./routing/uri";
export {
  clearRegistry,
  registerSurface,
  resolveSurface,
  type PendingDiff,
  type SurfaceRendererProps,
} from "./surfaces";
export {
  TcInlineDiff,
  type InlineDiffState,
  type TcInlineDiffProps,
} from "./thread-canvas";
export { TransportProvider, useTransport } from "./providers/TransportProvider";
export { RouterProvider, useRouter } from "./providers/RouterProvider";
export {
  KeyValueStoreProvider,
  useKeyValueStore,
} from "./providers/KeyValueStoreProvider";
export {
  LocalStorageKeyValueStore,
  type KeyValueStore,
  type LocalStorageKeyValueStoreConfig,
} from "./storage/key-value-store";
export {
  SecretStorageProvider,
  useSecretStorage,
} from "./providers/SecretStorageProvider";
export {
  WebSecretStorage,
  type SecretStorage,
  type WebSecretStorageConfig,
} from "./storage/secret-storage";
export {
  PresenceSignalProvider,
  usePresenceSignal,
} from "./providers/PresenceSignalProvider";
export {
  DocumentPresenceSignal,
  type PresenceSignal,
  type PresenceState,
} from "./presence/presence-signal";
// === Phase 0 (PR-0.4) DeploymentProfile port ===
// Runtime profile supplied by the host substrate; gates team-only surfaces
// (Workspace / Members / Billing) off on single-user desktop. Consolidation
// targets land under messages/composer/citations/subagents/approvals/workspace.
export {
  DeploymentProfileProvider,
  useDeploymentProfile,
  type DeploymentProfile,
} from "./providers/DeploymentProfileProvider";
// === end Phase 0 (PR-0.4) ===
export { ChatShell } from "./shell/ChatShell";
export { CopyIcon } from "./icons/CopyIcon";
export { RetryIcon } from "./icons/RetryIcon";
export { ThinkingIcon } from "./icons/ThinkingIcon";
export { PlainText } from "./messages/PlainText";
export { Reasoning } from "./messages/Reasoning";
export { markdownLinkLabel } from "./messages/markdownLinks";
// === Phase 1 (PR-1.1) message/markdown renderer ===
// Hoisted streaming-markdown renderer. The host binds `components.a` (its
// citation-chip dispatcher via `createMarkdownLink`) and `onMatch` (its
// diagnostics sink); chat-surface stays app-import-free.
export { MarkdownText, type MarkdownTextProps } from "./messages/MarkdownText";
export {
  createMarkdownLink,
  isExternalHref,
  type MarkdownLinkChips,
} from "./messages/MarkdownLink";
export {
  ReasoningGroup,
  type ReasoningGroupProps,
} from "./messages/ReasoningGroup";
// === end Phase 1 (PR-1.1) ===
// === Phase 1 (PR-1.6) approvals ===
// Presentational consent card + collapsed receipt (+ their inset param /
// details / undo-countdown leaves). The approval routing/wiring — the
// ApprovalTool dispatcher, useApprovalsQueue, ApprovalFocusContext, the
// forward/undo POST plumbing — stays host-owned in apps/frontend; the host
// renders these behind its own Approve/Reject/Forward/Undo callbacks.
export {
  ApprovalCard,
  type ApprovalCardProps,
  ApprovalReceipt,
  type ApprovalReceiptProps,
  type ApprovalReceiptKind,
  ActivityDetails,
  ActivityParams,
  useUndoCountdown,
  type UndoCountdownState,
  type ActivityParam,
} from "./approvals";
// === end Phase 1 (PR-1.6) ===
// === Phase 1 (PR-1.5) subagent / fleet cards ===
// Hoisted subagent presentation family. The host keeps the data-binding
// (reducers, activity builders, fleet context, jump-to-approval wiring) and
// passes normalised data + callbacks in as props; chat-surface stays
// app-import-free.
export {
  SubagentCard,
  type SubagentCardProps,
  FleetSubagentRow,
  type FleetSubagentRowProps,
  SubagentFleetCard,
  type SubagentFleetCardProps,
  subagentCardFromArgs,
  subagentCardFromEntry,
  type SubagentCardStatus,
  type SubagentCardViewModel,
  type SubagentPauseReason,
  formatSubagentDuration,
  pauseAriaLabel,
  pauseFullLabel,
  pauseJumpLabel,
  pauseShortLabel,
  ActivityStatusIcon,
  SubagentActivityList,
  useElapsedSeconds,
  type SubagentActivityRecord,
} from "./subagents";
// === end Phase 1 (PR-1.5) ===
export { CitationChip, type CitationChipProps } from "./citations/CitationChip";
export {
  OrdinalCitationChip,
  type OrdinalCitationChipProps,
  type OrdinalResolution,
} from "./citations/OrdinalCitationChip";
export { humanizeConnector } from "./citations/connectorLabel";
export {
  formatRelative,
  isLiveConnector,
  sourceFreshnessLabel,
} from "./citations/sourceFreshness";
export {
  SourceFavicon,
  type SourceFaviconProps,
} from "./citations/SourceFavicon";
export { SourceRow, type SourceRowProps } from "./citations/SourceRow";
export {
  SourceSkeletonRow,
  type SourceSkeletonRowProps,
} from "./citations/SourceSkeletonRow";
export {
  scrollChatToCitation,
  scrollChatToEvent,
} from "./citations/scrollChatToCitation";
export {
  citationsByOrdinal,
  citationsForRun,
  emptyCitationRegistry,
  upsertCitation,
  upsertCitations,
  type CitationRegistryByRun,
} from "./citations/registry";
export {
  anyLinkForOrdinalInRun,
  applyCitationLinkEvent,
  buildCitationLinkRegistry,
  emptyCitationLinkRegistry,
  isCitationLink,
  linkForOrdinal,
  linksForMessage,
  linksForRun,
  upsertCitationLink,
  type CitationLinkDebug,
  type CitationLinkRegistryByRun,
  type CitationLinksByMessage,
  type CitationLinksByOffset,
} from "./citations/linkReducer";
// === Phase 1 (PR-1.4) citations subsystem ===
// Run-scoped citation read context + Sources surfaces. The host binds the
// concrete citation/link registries as provider props (the resolution seam);
// `SourcesPanel` takes host-ordered sources + a `SourceRowComponent` slot so
// the web hover-preview portal stays host-side.
export {
  CitationsProvider,
  useCitation,
  useRunCitations,
  useOrdinalCitation,
  useResolvedOrdinalCitation,
  type CitationLookup,
  type CitationsProviderProps,
  type ResolvedOrdinalCitation,
} from "./citations/CitationsContext";
export {
  MessageSourcesStrip,
  type MessageSourcesStripProps,
} from "./citations/MessageSourcesStrip";
export { SourcesPanel, type SourcesPanelProps } from "./citations/SourcesPanel";
// === end Phase 1 (PR-1.4) ===
export {
  CITATION_HREF_PREFIX,
  CITATION_ORDINAL_HREF_PREFIX,
  citationIdFromHref,
  isCitationHref,
  isOrdinalCitationHref,
  ordinalFromHref,
} from "./messages/citationHrefs";
export {
  createRemarkCitations,
  type RemarkCitationsOptions,
} from "./messages/citationRemarkPlugin";
export {
  streamingCursorProps,
  type StreamingCursorProps,
} from "./messages/streamingCursor";
export type {
  MessagePartState,
  MessagePartStatus,
  ReasoningMessagePart,
  ReasoningMessagePartProps,
  TextMessagePart,
  TextMessagePartProps,
} from "./messages/types";

// === Phase 0-A adapter contract ===
export type {
  SaaSRendererAdapter,
  SaaSRendererAdapterMetadata,
  SaaSRendererAdapterOrigin,
} from "./surfaces";
export {
  TIER3_SCHEME,
  markBroken,
  registerAdapter,
  resolveAdapter,
  unregisterAdapter,
} from "./surfaces";
export { TcSurfaceMount, type TcSurfaceMountProps } from "./thread-canvas";
// === end Phase 0-A ===

// === Phase 4-B tier3 generic-diff ===
export {
  GenericStructuredDiff,
  registerGenericStructuredDiff,
  type GenericCurrentState,
  type GenericFieldChange,
  type GenericStructuredDiffPayload,
} from "./surfaces";
// === end Phase 4-B ===

// === Phase 6-A tier-2 loader ===
export {
  Tier2Loader,
  type Tier2LoaderProps,
  type Tier2WorkerLike,
  type Tier2WorkerRequest,
  type Tier2WorkerResponse,
  type Tier2JsonElement,
} from "./surfaces/Tier2Loader";
// === end Phase 6-A ===

// === Phase 0-B ports facade ===
export * from "./ports";
// === end Phase 0-B ===

// === Phase 1-B chat-shell-layout ===
export {
  AppRail,
  ContextPanel,
  Topbar,
  RightRail,
  APP_RAIL_WIDTH,
  CONTEXT_PANEL_WIDTH,
  RIGHT_RAIL_WIDTH,
  TOPBAR_HEIGHT,
  DEFAULT_SHELL_DESTINATION,
  DestinationPlaceholder,
  SHELL_DESTINATIONS,
  defaultDestinationForProfile,
  destinationsForProfile,
  type AppRailProps,
  type ChatShellProps,
  type ContextPanelPrimaryAction,
  type ContextPanelProps,
  type ContextPanelSearch,
  type DestinationPlaceholderBridge,
  type DestinationPlaceholderProps,
  type ShellDestination,
  type ShellDestinationSlug,
  type TopbarProps,
} from "./shell";
// === end Phase 1-B ===

// === Phase 1-D routing-palette ===
export { HashRouter } from "./routing/HashRouter";
export { ROUTE_TABLE, type RouteEntry } from "./routing/route-table";
// Phase-1 placeholder palette (route-jumper). Phase 12 supersedes it
// with the substrate-shared palette in `./shell`; we keep the older
// component exported under a distinct name so any in-tree consumer
// can migrate without a flag day.
export { CommandPalette as RouteJumpPalette } from "./palette";
// === end Phase 1-D ===

// === Phase 12 — substrate-shared global ⌘K palette ===
export {
  CommandPalette,
  CommandPaletteTrigger,
  PaletteHitRow,
  useCommandPaletteHotkey,
  type CommandPaletteProps,
  type CommandPaletteTriggerProps,
  type PaletteHitRowProps,
  type UseCommandPaletteHotkeyOptions,
} from "./shell";
export type { PaletteSearchPort } from "./ports/PaletteSearchPort";
// === end Phase 12 ===

// === Phase 2-B thread-canvas ===
export {
  ThreadCanvas,
  type ThreadCanvasProps,
  TcTabs,
  type TcTabsProps,
  type TcTab,
} from "./thread-canvas";
// === end Phase 2-B ===

// === Phase 2-C swimlanes ===
export {
  TcSwimlanes,
  type Playhead,
  type TcSwimlanesProps,
} from "./thread-canvas";
// === end Phase 2-C ===

// === Phase 2-D tc-chat ===
export {
  TcChat,
  type TcChatProps,
  SwimlaneScrubProvider,
  useSwimlaneScrub,
  type SwimlaneScrubState,
} from "./thread-canvas";
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
  ToolPicker,
  type ToolPickerProps,
  type ComposerToolDescriptor,
  type ComposerToolKind,
  ModelPicker,
  type ModelPickerProps,
  type ModelDescriptor,
  type Depth,
  listDepthDescriptors,
  MentionPopover,
  type MentionPopoverProps,
  type MentionCandidate,
} from "./composer";
// === end Phase 2-D ===

// === Phase 1 (PR-1.2) composer sub-controls ===
// Advanced-composer / topbar leaf controls (model pill incl. custom
// OpenRouter slug, thinking-depth radiogroup, `+` menu views, connectors
// trigger) hoisted from apps/frontend behind props. `ThinkingDepth` is the
// advanced-composer reasoning-depth model — distinct from the base
// Composer's `Depth` above; FR-1.7 flags the duplication (deferred to 3E).
export {
  ModelPill,
  type ModelPillProps,
  ThinkingDepthControl,
  type ThinkingDepthControlProps,
  ComposerPlusMenu,
  type ComposerMenuView,
  ComposerConnectorsButton,
  type ComposerConnectorsButtonProps,
  THINKING_DEPTHS,
  DEFAULT_THINKING_DEPTH,
  isThinkingDepth,
  depthLabel,
  depthLabelForModel,
  depthDescription,
  modelSupportsDepth,
  type ThinkingDepth,
} from "./composer";
// === end Phase 1 (PR-1.2) ===

// === Phase 1 (PR-1.3) AssistantComposer shell ===
// The advanced-composer shell hoisted behind ports/slots. The host binds the
// runtime attachment bridge, the `FilePickerPort` (real-`File` web picker), the
// `+` menu portal + outside-click (`renderPlusMenu` slot), and the
// instruction-prompt builders — the moved core stays substrate-agnostic.
export {
  AssistantComposer,
  type AssistantComposerProps,
  type AssistantComposerPlusMenuSlotArgs,
  type DetailsPanelKind,
  AttachmentPill,
  fileAttachmentAccept,
} from "./composer";
// === end Phase 1 (PR-1.3) ===

// === Phase 2-E inline-diff state-machine ===
export {
  nextInlineDiffState,
  useInlineDiffReducer,
  InvalidInlineDiffTransitionError,
  type InlineDiffEvent,
  __dev__inlineDiffFixtures,
  type __dev__InlineDiffFixture,
} from "./thread-canvas";
// === end Phase 2-E ===

// === Phase 0.5 shared primitives — branded IDs + cross-destination refs ===
// Source of truth: @0x-copilot/api-types/src/{brands,refs}.ts.
// Re-exported here so chat-surface consumers can keep their single import
// site, but the types themselves are NOT redeclared.
export type {
  AgentId,
  ApprovalId,
  ConnectorId,
  ConversationId,
  InboxItemId,
  ItemKind,
  ItemRef,
  ItemRefSnapshot,
  LibraryDatasetId,
  LibraryEntityId,
  LibraryFileId,
  LibraryItemId,
  LibraryPageId,
  MeetingExternalId,
  MemoryItemId,
  ProjectId,
  RoutineId,
  RunId,
  SectionResult,
  SkillId,
  SubagentId,
  TenantId,
  TodoExtractionId,
  TodoId,
  ToolId,
  ToolResultId,
  UserId,
} from "@0x-copilot/api-types";

// ItemLink registry + renderer (cross-audit §3.3).
export {
  ItemLink,
  ItemRefResolverAlreadyRegistered,
  ItemRefResolverNotRegistered,
  __resetItemRefRegistryForTests,
  hasItemRefResolver,
  registerItemRefResolver,
  resolveItemRef,
  unregisterItemRefResolver,
  type ItemLinkProps,
  type ItemRefResolved,
  type ItemRefResolver,
} from "./refs";

// Time formatting (cross-audit §3.4).
export { formatRelativeTime } from "./util/time";

// Shell primitives.
export {
  ActivityList,
  CardGrid,
  DocList,
  EmptyState,
  FilterTabs,
  PageHeader,
  StatusPill,
  type ActivityListProps,
  type ActivityRow,
  type CardGridProps,
  type EmptyStateAction,
  type EmptyStateProps,
  type FilterTabOption,
  type FilterTabsProps,
  type PageHeaderPrimaryAction,
  type PageHeaderProps,
  type StatusPillProps,
  type StatusTone,
} from "./shell";
// === end Phase 0.5 ===

// === Phase 2-A / 3 destinations ===
export {
  ChatsDestination,
  ChatsSidebar,
  type ChatsSidebarProps,
} from "./destinations/chats";
export {
  HomeDestination,
  HomePanel,
  type HomeDestinationProps,
  type HomePanelProps,
} from "./destinations/home";
export {
  InboxDestination,
  InboxPanel,
  bucketInbox,
  type InboxDestinationProps,
  type InboxItem,
  type InboxItemKind,
  type InboxItemPriority,
  type InboxItemStatus,
  type InboxPanelCounts,
  type InboxPanelFilterSlug,
  type InboxPanelProps,
  type InboxSectionKey,
  type InboxSender,
  type InboxSenderKind,
  type InboxSystemOrigin,
  type RenderDetailSlot,
} from "./destinations/inbox";
export {
  TodosDestination,
  TodosPanel,
  bucketTodos,
  type Todo,
  type TodoExtraction,
  type TodoPriority,
  type TodoSectionKey,
  type TodoSource,
  type TodosDestinationProps,
  type TodosFilterSlug,
  type TodosPanelProps,
  type TodosPayload,
  type TodosProjectChip,
  type TodosSavedFilter,
} from "./destinations/todos";
export {
  ProjectFilterChip,
  ProjectsDestination,
  ProjectsPanel,
  type ProjectActivityCounts,
  type ProjectColorHue,
  type ProjectFilterChipOption,
  type ProjectFilterChipProps,
  type ProjectIconEmoji,
  type ProjectRole,
  type ProjectStatus,
  type ProjectSummary,
  type ProjectsDestinationProps,
  type ProjectsFilterCounts,
  type ProjectsFilterSlug,
  type ProjectsPanelProps,
  type RenderProjectDetailSlot,
} from "./destinations/projects";
export {
  LibraryDestination,
  type LibraryDestinationProps,
  LibraryPanel,
  type LibraryPanelProps,
  type LibrarySourceFilterCounts,
  type LibrarySourceFilterSlug,
  SaveToLibraryPopover,
  type SaveToLibraryPopoverProps,
  type SaveToLibrarySubmit,
  type LibraryDatasetSummary,
  type LibraryFileKind,
  type LibraryFileSummary,
  type LibraryIndexStatus,
  type LibraryItemKind,
  type LibraryItemSummary,
  type LibraryKindFilterCounts,
  type LibraryKindFilterSlug,
  type LibraryPageSummary,
  type LibrarySortSlug,
  type LibrarySource,
  type LibrarySourceKind,
  type LibraryViewMode,
  type SaveToLibraryDefaultKind,
  type SaveToLibrarySource,
} from "./destinations/library";
export {
  AgentCard,
  AgentsDestination,
  AgentsPanel,
  AGENTS_PANEL_WIDTH,
  AGENT_COST_LABELS,
  AGENT_FILTER_LABELS,
  STARTER_RECOMMENDATIONS,
  filterAgents,
  resolveAgentItemRef,
  searchAgents,
  type AgentCardProps,
  type AgentCostTier,
  type AgentFilter,
  type AgentItemDisplay,
  type AgentItemRef,
  type AgentOrigin,
  type AgentStub,
  type AgentsDestinationProps,
  type AgentsPanelProps,
} from "./destinations/agents";
export {
  ToolsDestination,
  ToolsPanel,
  ToolCard,
  filterTools,
  isInstalled,
  searchTools,
  sortTools,
  statusTone,
  TOOLS_FILTER_LABELS,
  TOOLS_FILTER_ORDER,
  TOOLS_KIND_LABELS,
  TOOLS_KIND_ORDER,
  TOOLS_SCOPE_LABELS,
  TOOLS_SCOPE_ORDER,
  TOOLS_SORT_LABELS,
  TOOLS_SORT_ORDER,
  TOOLS_STATUS_LABELS,
  ONBOARD_KIND_TILES,
  type KindOnboardTile,
  type ToolCardProps,
  type ToolsDestinationProps,
  type ToolsFilterContext,
  type ToolsFilterSlug,
  type ToolsPanelProps,
  type ToolsSortSlug,
} from "./destinations/tools";
export {
  ConnectorCard,
  ConnectorsDestination,
  ConnectorsPanel,
  RevealOnce,
  ConnectorDetailView,
  ScopeReviewTab,
  ConsumersTab,
  ReadAuditTab,
  WebhooksDestination,
  WebhookCard,
  WebhookDetailView,
  WebhookCreateWizard,
  WEBHOOK_VERIFICATION_SNIPPET,
} from "./destinations/connectors";
export type {
  ConnectorsDestinationProps,
  ConnectorsFilterCounts,
  ConnectorsFilterSlug,
  ConnectorCardProps,
  ConnectorsPanelProps,
  RevealOnceProps,
  ConnectorDetailViewProps,
  ConnectorDetailTabId,
  ScopeReviewTabProps,
  ConsumersTabProps,
  ReadAuditTabProps,
  WebhooksDestinationProps,
  WebhookCardProps,
  WebhookDetailViewProps,
  WebhookCreateWizardProps,
  WebhookCreateWizardRequest,
} from "./destinations/connectors";
export {
  applyRoleFilter,
  applySearch,
  applySort,
  OffboardingWizard,
  PersonCard,
  PersonDetailView,
  TeamDestination,
  TeamInviteWizard,
  TeamPanel,
  type OffboardingAsset,
  type OffboardingWizardProps,
  type PersonCardProps,
  type PersonDetailTabId,
  type PersonDetailViewProps,
  type PresenceFilterCounts,
  type PresenceFilterSlug,
  type TeamDestinationProps,
  type TeamFilterCounts,
  type TeamFilterSlug,
  type TeamInviteWizardProps,
  type TeamInviteWizardResult,
  type TeamPanelProps,
  type TeamSortSlug,
} from "./destinations/team";
// === Phase 12 P12-B2 — Memory destination (presentation layer) ===
// MemoryDestination is the 13th destination's shell. The data binder
// (host) lands in P12-C2; until then App.tsx mounts the shell with no
// `items` prop and the destination renders an unwired-state explanation.
export {
  MemoryDestination,
  MemoryPanel,
  MemoryDetailView,
  MemoryEditor,
  MemoryProposalToast,
  MemoryProposalToastStack,
  MemoryProposalCard,
  type MemoryDestinationProps,
  type MemoryDetailTabSlug,
  type MemoryDetailViewProps,
  type MemoryEditorProps,
  type MemoryEditorSavePayload,
  type MemoryKindFilterCounts,
  type MemoryKindFilterSlug,
  type MemoryPanelProps,
  type MemoryPanelTagChip,
  type MemoryProposalCardProps,
  type MemoryProposalToastProps,
  type MemoryProposalToastStackProps,
  type MemoryScopeFilterSlug,
  type RenderMemoryDetailSlot,
} from "./destinations/memory";
// === end Phase 12 Memory ===
// === end Phase 2-A / 3 destinations ===

// === Phase 5 — Routines (12th destination) ===
export {
  RoutinesDestination,
  RoutinesPanel,
  nextFireDisplay,
  uniqueTriggerKinds,
  type RenderRoutineDetailSlot,
  type Routine,
  type RoutineAutonomy,
  type RoutineBehavior,
  type RoutineConnectorConfig,
  type RoutineDataResidency,
  type RoutineManualFire,
  type RoutineMissedFirePolicy,
  type RoutineOutputTarget,
  type RoutinePermissions,
  type RoutineScope,
  type RoutineStatus,
  type RoutineTrigger,
  type RoutineTriggerKind,
  type RoutinesDestinationProps,
  type RoutinesFilterCounts,
  type RoutinesFilterSlug,
  type RoutinesPanelProjectChip,
  type RoutinesPanelProps,
  type RoutinesPanelTriggerCounts,
  type RoutinesPanelTriggerSlug,
  type TriggerId,
} from "./destinations/routines";
// === end Phase 5 ===

// === Phase 12 — Settings pages (NOT a destination) ===
export {
  NotificationsPage,
  NOTIFICATION_DESTINATION_ROWS,
  WebhookSecurityPage,
  MAX_SECRET_AGE_DAY_VALUES,
  clampMaxSecretAgeDays,
  ProfilePage,
  QuietHoursEditor,
  validateQuietHoursWindow,
  type DestinationRowDescriptor,
  type NotificationsPageProps,
  type NotificationsPageTabSlug,
  type ProfilePagePerson,
  type ProfilePageProps,
  type QuietHoursEditorProps,
  type WebhookSecurityPageProps,
} from "./settings";
// === end Phase 12 Settings ===

// === Phase 5 (PR-5.2) — settings design primitives (tokenized) ===
export {
  Modal,
  StepDots,
  MODAL_WIDTH,
  SetCard,
  SecHead,
  SetNote,
  Frow,
  Krow,
  SettingsNavItem,
  SaveBar,
  Toast,
  SegmentedControl,
  AccentSwatch,
  ThemeTile,
  ProgressBar,
  type ModalProps,
  type StepDotsProps,
  type SetCardProps,
  type SecHeadProps,
  type SetNoteProps,
  type SetNoteTone,
  type FrowProps,
  type KrowProps,
  type SettingsNavItemProps,
  type SaveBarProps,
  type ToastProps,
  type ToastTone,
  type SegmentedControlProps,
  type SegmentedOption,
  type AccentSwatchProps,
  type ThemeTileProps,
  type ProgressBarProps,
  type ProgressTone,
} from "./settings";
// === end Phase 5 (PR-5.2) ===

// === Phase 5 (PR-5.1) — settings shell (nav SSOT + profile gate + router) ===
export {
  SettingsSurface,
  useSettingsSurface,
  SETTINGS_NAV_WIDTH,
  SETTINGS_CONTENT_MAX_WIDTH,
  SETTINGS_NAV_GROUPS,
  SETTINGS_NAV_ITEMS,
  DEFAULT_SETTINGS_SLUG,
  SOLO_FOOTER_COPY,
  settingsNavForProfile,
  visibleSettingsSlugs,
  isSettingsSlugVisible,
  resolveSettingsSlug,
  showSoloFooter,
  settingsNavItem,
  type SettingsSurfaceProps,
  type SettingsSurfaceController,
  type SettingsDirtyState,
  type SettingsSurfaceToast,
  type SettingsSectionSlug,
  type SettingsNavGroupId,
  type SettingsNavGroupView,
  type SettingsNavIcon,
  type SettingsNavItemModel,
  type SettingsProfileGate,
} from "./settings";
// === end Phase 5 (PR-5.1) ===

// === Phase 1 (PR-1.7) workspace pane ===
// Hoisted right-rail pane + tablist + five tab bodies (Sources / Agents /
// Draft / Approvals / Skills). Composition shell only — the host keeps the
// data-binding hooks (useWorkspacePaneState / useApprovalsQueue / useSubagents
// / useSubagentActivities / useDrafts / useArchivedSources / auto-open signal)
// and passes their normalised outputs in as props. The tabs consume the
// already-hoisted citations (SourceRow, via the injected `SourceRowComponent`
// slot) and subagents (SubagentCard) families; every `chatModel`-typed prop is
// re-typed chat-surface-local (SourceEntryMap / SubagentSnapshotMap /
// ApprovalsQueueProjection / WorkspacePaneState / …).
export {
  WorkspacePane,
  type WorkspacePaneProps,
  WorkspaceTabs,
  workspaceTabPanelId,
  type WorkspaceTabsItem,
  type WorkspaceTabsProps,
  SourcesTab,
  type SourcesTabProps,
  type SourceRowSlot,
  AgentsTab,
  type AgentsTabProps,
  DraftTab,
  type DraftTabProps,
  ApprovalsTab,
  type ApprovalsTabProps,
  SkillsTab,
  type SkillsTabProps,
  pluralize,
  tabLabel,
  TAB_LABELS,
  type LabelForms,
  type WorkspacePaneState,
  type WorkspacePaneTabId,
  type WorkspacePaneCloseReason,
  type WorkspacePaneOpenOptions,
  type WorkspacePaneFocus,
  type ApprovalsQueueItem,
  type ApprovalsQueueProjection,
  type SubagentActivitiesByTask,
  type SubagentHistoryGroup,
  type SourceEntryMap,
  type SubagentSnapshotMap,
  type SourceConnectorGroup,
} from "./workspace";
// === end Phase 1 (PR-1.7) ===
