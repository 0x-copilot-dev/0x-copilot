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
export { ChatShell } from "./shell/ChatShell";
export { CopyIcon } from "./icons/CopyIcon";
export { RetryIcon } from "./icons/RetryIcon";
export { ThinkingIcon } from "./icons/ThinkingIcon";
export { PlainText } from "./messages/PlainText";
export { Reasoning } from "./messages/Reasoning";
export { markdownLinkLabel } from "./messages/markdownLinks";
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
export { CommandPalette } from "./palette";
// === end Phase 1-D ===

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
  type ToolDescriptor,
  type ToolKind,
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
// Source of truth: @enterprise-search/api-types/src/{brands,refs}.ts.
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
} from "@enterprise-search/api-types";

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
export { ToolsDestination } from "./destinations/tools";
export {
  ConnectorsDestination,
  type McpServerRow,
} from "./destinations/connectors";
export {
  TeamDestination,
  type Member,
  type MemberRole,
  type TeamDestinationProps,
} from "./destinations/team";
export { MemoryDestination } from "./destinations/memory";
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
