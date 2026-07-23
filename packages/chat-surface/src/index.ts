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
  useOptionalDeploymentProfile,
  type DeploymentProfile,
} from "./providers/DeploymentProfileProvider";
// === end Phase 0 (PR-0.4) ===
export { ChatShell } from "./shell/ChatShell";
// === PRD-03 — host binding contract + shared projections ===
// The TOTAL props boundary between chat-surface and its two hosts. Binding
// types (required, `undefined`-free) + the type-derived field manifests both
// host conformance tests iterate. `toChatArchiveRow` is the single per-row
// chats projection both hosts now call (was duplicated per binder).
export type {
  ProjectsDetailBinding,
  ProjectsHostBinding,
  ShellHostBinding,
  ExhaustiveBindingManifest,
} from "./contract";
export { PROJECTS_BINDING_FIELDS, SHELL_BINDING_FIELDS } from "./contract";
export { toChatArchiveRow } from "./projections/chats";
// === end PRD-03 ===
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

// === PRD-11 registry scoping groundwork ===
// Isolated-instance factory + React scoping. Default remains the module-global
// registry (zero behaviour change); a provider scopes a subtree for per-tenant
// resolution later. See surfaces/SurfaceRegistry.ts header for the invariant.
export {
  createSurfaceRegistry,
  globalSurfaceRegistry,
  SurfaceRegistryProvider,
  useSurfaceRegistry,
  type SurfaceRegistry,
  type SurfaceRegistryProviderProps,
} from "./surfaces";
// === end PRD-11 registry scoping groundwork ===

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
export {
  createTier2WorkerFactory,
  executeAdapterRender,
  TIER2_WORKER_SOURCE,
  TIER2_WORKER_DS_COMPONENT_NAMES,
  type Tier2WorkerFactory,
} from "./surfaces/tier2Worker";
// === end Phase 6-A ===

// === Phase 0-B ports facade ===
export * from "./ports";
// === end Phase 0-B ===

// === Phase 1-B chat-shell-layout ===
export {
  AppRail,
  BrandMark,
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
  type BrandMarkProps,
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

// === Phase 1-D routing (HashRouter + route-table) ===
// The Phase-1 route-jumper palette that once lived here was superseded by the
// substrate-shared ⌘K palette in `./shell` and removed in Phase 6 (PR-6.1);
// only the HashRouter + route-table primitives remain.
export { HashRouter } from "./routing/HashRouter";
export { ROUTE_TABLE, type RouteEntry } from "./routing/route-table";
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

// === Phase 6 — shell keyboard shortcuts (SSOT table + hook) ===
// PR-6.6: surface the §6 shortcut hook + its callback/option types at the
// package root so the desktop bootstrap (and any other host) can wire the
// global chords through the single SSOT (`shell/shortcuts.ts`).
export {
  SHELL_SHORTCUTS,
  useShellShortcuts,
  type ShellShortcutCallbacks,
  type ShortcutIntent,
  type UseShellShortcutsOptions,
} from "./shell";
// === end Phase 6 shortcuts ===

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

// ItemLink route registry + renderer (cross-audit §3.3; PRD-04 route-only).
export {
  ItemLink,
  itemKindNoun,
  ItemRouteAlreadyRegistered,
  ItemRouteNotRegistered,
  __resetItemRouteRegistryForTests,
  hasItemRoute,
  registerItemRoute,
  resolveItemRoute,
  unregisterItemRoute,
  type ItemLinkProps,
  type ItemRouteResolver,
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
  // PRD-03 Move 1: `cacheProjectNames` is no longer a host duty — the
  // `ProjectsDestination` primes the cache from `items` itself, so it is
  // dropped from the public barrel (kept exported from `projectNameCache.ts`
  // for tests). `cacheProjectName` / `getCachedProjectName` stay for the
  // resolver's own use.
  cacheProjectName,
  getCachedProjectName,
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
  ConnectorAccessPort,
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
  type LinkWalletOutcome,
  type ProfileIdentityAnchor,
  type ProfileLinkedIdentity,
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
  SecTitle,
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
  type SecTitleProps,
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
  // D5 — slug → page ownership SSOT (nav-entry↔page bijection guard).
  SETTINGS_PAGE_OWNERSHIP,
  settingsPageOwner,
  chatSurfaceOwnedSlugs,
  hostOwnedSlugs,
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
  type SettingsPageOwner,
} from "./settings";
// === end Phase 5 (PR-5.1) ===

// === Phase 1 — new interaction families (subagents · approvals · workspace) ===
// These three families were introduced by Phase 1 and have no pre-Phase-1
// sibling exports, so they cluster here in PR order (1.5 → 1.6 → 1.7) rather
// than scattering through the messages/citations neighbourhood above.
// Presentational cores only — the host keeps every data-binding hook/reducer
// and passes normalised data + callbacks in as props (chat-surface stays
// app-import-free).

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

// === Phase 5 (PR-5.3…PR-5.9) — settings section bodies ===
// The section bodies that fill the SettingsSurface `renderSection` slot. They
// live in `./settings`; they are surfaced at the package root so a host (the
// desktop shell) can wire the whole surface through the public barrel rather
// than deep-importing `src/settings/*` (which would cross the package boundary).
// `ProfilePage`/`NotificationsPage`/`QuietHoursEditor` are already exported above
// (Phase 12 Settings block).
export {
  // Account (PR-5.3)
  AppearancePage,
  appearanceAttributes,
  splitAppearancePersistence,
  APPEARANCE_THEMES,
  APPEARANCE_ACCENTS,
  APPEARANCE_DENSITIES,
  ShortcutsPage,
  SHORTCUTS,
  type AppearancePageProps,
  type AppearanceValue,
  type AppearancePatch,
  type AppearanceTheme,
  type AppearanceAccentId,
  type AppearanceDensity,
  type AppearanceAttributes,
  type AppearancePersistenceSplit,
  type ShortcutRow,
  // Models & keys (PR-5.4 / PR-5.5 / PR-5.6)
  ProviderKeysPage,
  PROVIDER_KEYS_KEYCHAIN_NOTE,
  AddProviderKeyModal,
  createProviderKeysPort,
  checkProviderKeyFormat,
  providerCatalogEntry,
  PROVIDER_CATALOG,
  CUSTOM_ENDPOINT_ENTRY,
  type ProviderKeysPageProps,
  type AddProviderKeyModalProps,
  type AddProviderKeySubmit,
  type AddProviderKeyValidateContext,
  type ProviderKeysPort,
  type ProviderCatalogEntry,
  type ProviderKeyValidation,
  type SaveProviderKeyOptions,
  type ValidateProviderKeyOptions,
  // Models curation (PR-3D)
  ModelsPage,
  MODELS_PAGE_NOTE,
  createModelsPort,
  groupModelsByProvider,
  filterModels,
  providerLabel,
  priceLabel,
  contextLabel,
  type ModelsPageProps,
  type ModelsPort,
  type CatalogModel,
  type ModelGroup,
  LocalModelsPage,
  DownloadLocalModelModal,
  createLocalModelsPort,
  LOCAL_MODEL_CATALOG,
  LOCAL_MODEL_PULL_EVENT,
  DEFAULT_LOCAL_MODEL_QUANT,
  localModelInstalledTag,
  formatBytes,
  formatBytesPair,
  formatEta,
  humanStatus,
  placementLabel,
  type LocalModelsPageProps,
  type DownloadLocalModelModalProps,
  type AvailableLocalModel,
  type LocalModelPullHandle,
  type LocalModelPullHandlers,
  type StartLocalModelPull,
  type LocalModelDownloadResult,
  type LocalModelsPort,
  ModelBehaviorPage,
  REASONING_DEPTHS,
  ApprovalPolicy,
  READ_ONLY_APPROVAL_OPTIONS,
  WRITE_APPROVAL_OPTIONS,
  DANGER_APPROVAL_OPTIONS,
  APPROVAL_POLICY_CONNECTOR_NOTE,
  type ModelBehaviorPageProps,
  type ModelBehaviorValue,
  type ModelBehaviorPatch,
  type ModelBehaviorModelOption,
  type ReasoningDepth,
  type SpendGuardrailValue,
  // D4 — Spend-guardrail port (bound to /v1/budgets; owns $↔micro-USD).
  createSpendGuardrailPort,
  capUsdToMicro,
  microToCapUsd,
  type SpendGuardrailPort,
  type SpendGuardrailSnapshot,
  // D5 — Approval-policy port (bound to /v1/me/policies/tool-use; owns the
  // UI-axis↔wire-kind mapping). Both hosts wire `createToolUsePolicyPort`.
  createToolUsePolicyPort,
  approvalPolicyFromResponse,
  toolUsePolicyRequestFromValue,
  DEFAULT_APPROVAL_POLICY,
  type ApprovalPolicyPort,
  type ApprovalPolicyProps,
  type ApprovalPolicyValue,
  type ReadOnlyApprovalMode,
  type WriteApprovalMode,
  type DangerApprovalMode,
  // Data & privacy (PR-5.7)
  PrivacyPage,
  RETENTION_OPTIONS,
  PRIVACY_EXPORT_PATH,
  PRIVACY_DELETE_CONFIRM_PHRASE,
  type PrivacyPageProps,
  type RetentionChoice,
  // Advanced (PR-5.9)
  AppLockPage,
  APP_LOCK_AFTER_OPTIONS,
  APP_LOCK_KEYCHAIN_NOTE,
  TOUCH_ID_UNAVAILABLE_HINT,
  DeveloperTokensPage,
  DEVELOPER_TOKENS_ONCE_NOTE,
  createDeveloperTokensPort,
  maskDeveloperToken,
  lastUsedLabel,
  type AppLockPageProps,
  type AppLockValue,
  type AppLockPatch,
  type AppLockAfter,
  type KeychainProtectionValue,
  type DeveloperTokensPageProps,
  type DeveloperTokensPort,
} from "./settings";
// === end Phase 5 (PR-5.3…PR-5.9) ===

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

// === PRD-B1 (Generative Surfaces v2) client ledger fold + hydration ===
// Pure PEER of `projectSurfaceTabs` over the SAME `session.events` array (the
// one-projector invariant), + the Transport-fed content hydration hook.
export {
  projectLedger,
  tabUriForSurface,
  surfaceIdForTabUri,
  ledgerTabsAsSurfaceTabs,
  toParitySnapshot,
  type LedgerProjection,
  type LedgerSurface,
  type LedgerSurfaceKind,
  type LedgerSurfaceSource,
  type LedgerSurfaceView,
  type LedgerViewTier,
} from "./thread-canvas";
export {
  useSurfacesV2,
  type UseSurfacesV2Result,
} from "./destinations/run/useSurfacesV2";
// === end PRD-B1 ===

// === Surfaces v2 — PRD-B3 view lifecycle (generic/shaped, upgrade, regenerate) ===
// Per-surface view-state fold extensions + the non-modal upgrade toast and the
// persistent tier toggle / Regenerate cluster. All pure projections of the same
// ledger; callbacks ride the Transport port (no second SSE subscription).
export {
  ViewUpgradeToast,
  ViewTierToggle,
  MAX_REGEN_PER_SURFACE,
  type ViewUpgradeToastProps,
  type ViewTierToggleProps,
  type LedgerSurfaceViewState,
  type LedgerViewKeep,
} from "./thread-canvas";
// === end PRD-B3 ===
// === Surfaces v2 — PRD-C2 tool-access gate card + posture chip ===
export {
  TcGateCard,
  type TcGateCardProps,
  type LedgerGate,
  type LedgerGateAuthState,
  type LedgerGateOutcome,
  type LedgerGateOpClass,
  type LedgerGateWritePolicy,
} from "./thread-canvas";
export { PostureChip, type PostureChipProps } from "./destinations/run";
// === end PRD-C2 ===

// === Surfaces v2 — PRD-B2 provenance + honest states ===
// Provenance footer, skeleton/assembling state, lossless raw fallback, and the
// status strip — all pure projections of the Work Ledger, mounted strictly
// inside B1's v2 canvas subtree (flag-off byte-identical).
export {
  projectProvenance,
  resolveSurfaceOpenIn,
  formatLatency,
  formatAccessClass,
  projectStatusLine,
  resolveDotPath,
  isSafeHttpUrl,
  TcProvenanceFooter,
  TcSurfaceSkeleton,
  TcSurfaceFrame,
  TcStatusStrip,
  type SurfaceProvenance,
  type SurfaceOpenIn,
  type SurfaceAccessClass,
  type SurfaceViewTier,
  type StatusStripLine,
  type TcProvenanceFooterProps,
  type TcSurfaceSkeletonProps,
  type TcSurfaceFrameProps,
  type TcStatusStripProps,
} from "./thread-canvas";
export {
  RawFallbackView,
  RAW_RENDER_MAX_BYTES,
  type RawFallbackViewProps,
} from "./surfaces/raw/RawFallbackView";
// === end PRD-B2 ===

// === Phase 3 (PR-3.3) run-session host hook ===
// The Run cockpit's live-run host hook. Resolves the active/selected run for a
// conversation and subscribes to its SSE tail through the Transport port,
// exposing an append-only event array + session lifecycle status for the
// RunDestination (PR-3.5) to project. No UI — network I/O is port-only.
export {
  useRunSession,
  type RunSession,
  type RunSessionStatus,
  type RunListItem,
  type UseRunSessionOptions,
} from "./destinations/run/useRunSession";
// === end Phase 3 (PR-3.3) ===
// === Phase 3 (PR-3.4) run mode ===
// KeyValueStore-backed Studio/Focus mode owner for the Run destination +
// the global ⌘M / Ctrl+M toggle. Owns the persisted mode value; feeds
// ThreadCanvas.mode in PR-3.5 (RunDestination shell). RunMode is an alias
// of ThreadMode — single source of truth for the "studio" | "focus" union.
export {
  useRunMode,
  readRunMode,
  writeRunMode,
  runModeKey,
  DEFAULT_RUN_MODE,
  type RunMode,
  type UseRunModeOptions,
  type UseRunModeResult,
} from "./destinations/run/useRunMode";
// === end Phase 3 (PR-3.4) ===

// === Phase 4 (PR-4.9) — Skills destination (skill catalog) ===
// Presentational card grid of saved multi-step workflows (`/v1/skills`):
// name, description sub, `N runs`, Run / Edit per card + a "New skill"
// header action. Controlled by `SectionResult<SkillSummary[]> | null` with
// the 4-state machine. The redesigned Skills slug — NOT the MCP
// tool-integration catalog (`tools/`), which the PRD supersedes for this
// slug. Host binding (fetch + Run/Edit/New wiring) lands in PR-4.10.
export {
  SkillsDestination,
  SkillCard,
  runCountLabel,
  SKILLS_SUBTITLE_COPY,
  SKILLS_EMPTY_TITLE,
  type SkillsDestinationProps,
  type SkillCardProps,
} from "./destinations/skills";
// === end Phase 4 (PR-4.9) ===
// === Phase 4 (PR-4.2) chats archive destination ===
// Pure-presentation Chats archive component: takes a pre-bucketed
// `ChatsArchive` (`SectionResult`) + `onReopen`/`onNewChat` callbacks and
// renders the shared `.pg` list surface with the 4-state machine
// (loading / error+Retry / empty / ready). Reopen → Run, "New chat" →
// Run are host concerns (the callbacks); the host binder (PR-4.3) wires
// them. `ChatsDestination` (Phase 2-A/3 block above) now forwards to this
// component; its props type is re-exported here.
export {
  ChatsArchive,
  type ChatsArchiveProps,
  CHATS_SECTION_ORDER,
  CHATS_LEAD_COPY,
  type ChatsSectionKey,
  type ChatsDestinationProps,
} from "./destinations/chats";
// === end Phase 4 (PR-4.2) ===
// === Phase 4 (PR-4.5) — Activity destination (run-history recast) ===
// Presentational run-history feed that absorbs the former Agents / Inbox /
// audit-log surfaces: a flat `SectionResult<ActivityRunRow[]>` in, day-grouped
// rows out (grouping in-shell via the injected `now`). Running rows call
// `onOpenRun`; non-running rows navigate through the `"run"` ItemLink resolver.
// The host binder (PR-4.6) composes conversations + audit into the rows.
export {
  ActivityDestination,
  activityStatusTone,
  groupActivityByDay,
  projectActivityRows,
  mapRunStatus,
  formatActivityMeta,
  ACTIVITY_LEAD_COPY,
  ACTIVITY_RETENTION_PREFIX_COPY,
  ACTIVITY_RETENTION_LINK_COPY,
  ACTIVITY_RUN_STATUSES,
  type ActivityDayGroup,
  type ActivityDestinationProps,
  type ActivityMetaCounts,
  type ActivityRunRow,
  type ActivityRunStatus,
} from "./destinations/activity";
// === end Phase 4 (PR-4.5) ===
// === Phase 3 (PR-3.5) run cockpit shell ===
// The Run destination composition: `RunDestination` wires `useRunSession` +
// `useRunMode` + `ThreadCanvas` into the DESIGN-SPEC §2 cockpit, and `RunHeader`
// renders a state-aware kicker ("ACTIVE RUN" / "STANDBY") + goal + the Studio/Focus segmented control.
// `apps/desktop` mounts `RunDestination` on the `run` slug (via its
// DestinationOutlet); PR-3.6…3.11 fill the rail / timeline scrub / subagents /
// streaming / approvals / empty+multi-run seams left in the shell.
export {
  RunDestination,
  buildRunCreateBody,
  type RunDestinationProps,
  type RunStartRequest,
  type RunEmptyComposerCtx,
  RunHeader,
  type RunHeaderProps,
} from "./destinations/run";
// === end Phase 3 (PR-3.5) ===
// === Phase 4 (PR-4.7) — Tools access-mode segment ===
// The Connectors destination, relabeled "Tools" (FR-4.20): each connected
// tool row renders <AccessModeSegment> (Read / Read & act / Off — FR-4.21),
// changes persisted through the host-injected `ConnectorAccessPort` (PRD-06
// D4 — the destination owns the optimistic apply / revert / error banner),
// plus the approval-policy note pointing at Settings → Model & behavior
// (FR-4.25). `ConnectorsDestination` / `ConnectorCard` themselves are already
// surfaced above (Phase 2-A / 3 block); this block adds only the new symbols.
export {
  AccessModeSegment,
  TOOLS_SUBTITLE,
  TOOLS_POLICY_NOTE_COPY,
} from "./destinations/connectors";
export type { AccessModeSegmentProps } from "./destinations/connectors";
// Re-export the wire union so hosts can type the access-mode port / segment
// values without a second `@0x-copilot/api-types` import.
export type { ConnectorAccessMode } from "@0x-copilot/api-types";
// === end Phase 4 (PR-4.7) ===
// === Phase 4 (PR-4.4) — Projects detail files section ===
// The project detail view (chats/files/members/activity + legacy tabs) and
// its new Files tab. `ProjectDetailView` takes a projected
// `files: SectionResult<ProjectFileRow[]> | null` and renders the shared
// 4-state machine; each ready row opens its artifact via
// `<ItemLink kind="library_file">` (FR-4.12). Omitting the `files` prop
// degrades the tab to a "coming soon" empty state — never an error
// (FR-4.11, PRD §11 files gap). The `ProjectsRoute` host binder (follow-up)
// wires the `files` source + `onRetryFiles`. `ProjectFileRow` is a local
// non-branded presentational row until a `@0x-copilot/api-types` contract
// lands (see the type's TODO).
export {
  ProjectDetailView,
  ProjectFilesTab,
  type ProjectDetail,
  type ProjectDetailProfile,
  type ProjectDetailViewProps,
  type ProjectDetailTabId,
  type ProjectFileRow,
  type ProjectFilesResult,
} from "./destinations/projects";
// === end Phase 4 (PR-4.4) ===

// === Phase 4 (PR-4.8) — Tools ConnectModal ===
// Presentational "Connect a tool" flow on the shared <Modal> + <StepDots>
// chrome (DESIGN-SPEC §5, FR-4.23): catalog pick → OAuth spinner → permission
// (Read only / Read & act) → Connect. The host binder (PR-4.8b) performs the
// OAuth round-trip and persists the connection, driving the modal purely via
// props: `onSelectEntry` kicks off OAuth, `pending`/`error` drive the
// spinner + inline alert, and `onConnect(slug, permission)` fires on the
// terminal Connect. Reuses `ConnectorCatalogEntry` + `ConnectorAccessMode`
// from @0x-copilot/api-types (no re-declaration).
export {
  ConnectModal,
  CONNECT_PERMISSION_OPTIONS,
  type ConnectModalProps,
  type ConnectPermission,
  type ConnectPermissionOption,
  type CustomServerInput,
} from "./destinations/connectors";
// === end Phase 4 (PR-4.8) ===

// === Phase 3 (PR-3.6) run workspace rail ===
// The Run cockpit's tabbed right rail `[Chat · Sources · Agents · Approvals]`
// (Chat default). A recomposition — NOT a fork — of the hoisted WorkspacePane
// tab bodies (SourcesTab / AgentsTab / ApprovalsTab); Draft + Skills are
// omitted. Composition shell only: `chatSlot` (the single TcChat) + the
// Sources/Agents/Approvals inputs are controlled/injected by the host, so the
// rail opens no second event projection (FR-3.3). Focus mode collapses it to
// Chat-only (FR-3.13). `RunDestination` feeds it to `ThreadCanvas.rightRail`.
export {
  RunWorkspaceRail,
  type RunWorkspaceRailProps,
  type RunRailTabId,
} from "./destinations/run";
// === end Phase 3 (PR-3.6) ===

// === Phase 3 (PR-3.8) subagents ===
// `projectSubagents` is a PURE selector over the single canonical run event
// stream (`session.events`). It yields the subagent snapshot map (feeds the
// Agents-tab "N live" count) and the dispatched fleets (feed the inline
// `SubagentFleetCard`) — the two subagent consumers that live outside
// ThreadCanvas. It opens no SSE subscription and no second `useEventProjector`
// (FR-3.3); the per-subagent timeline lanes come from TcSwimlanes' own stream.
// `RunDestination` performs the wiring; hosts embedding it need nothing more.
export {
  projectSubagents,
  type FleetProjection,
  type SubagentProjection,
} from "./subagents";
// === end Phase 3 (PR-3.8) ===

// === Phase 3 (PR-3.10) approvals ===
// `projectApprovals` is a PURE selector over the single canonical run event
// stream (`session.events`) — the SAME array `projectSubagents` reads (FR-3.3;
// no second SSE subscription / projector). It yields the pending + resolved
// approvals that feed the two approval consumers living outside ThreadCanvas:
// the in-chat 4-zone `ApprovalCard` / Focus `.conf-card` (`TcChat.approvals`)
// and the Approvals-tab pending count (`RunWorkspaceRail.approvalsQueue`, via
// `toApprovalsQueue`). `overlayApprovalDecisions` folds the user's optimistic
// Approve/Reject in before the trailing `approval_resolved` frame. `RunDestination`
// owns the wiring; `TcChatApproval` is the presentational view-model the card
// consumes (structurally a subset of `RunApproval`). The on-surface per-row
// states (`Approve & sign` / `✓ Signed` / `Rejected` / `Queued`) live in
// `surface-renderers` `SheetDiff` (`SheetRowApproval`); the `TcInlineDiff`
// state machine (`idle → streaming → pending → accepted|rejected`) is exported
// from the Phase 2-E block above.
export {
  projectApprovals,
  overlayApprovalDecisions,
  toApprovalsQueue,
  type RunApproval,
  type RunApprovalDecision,
  type RunApprovalKind,
  type ApprovalProjection,
} from "./destinations/run";
export { type TcChatApproval } from "./thread-canvas";
// === end Phase 3 (PR-3.10) ===

// === WC-P5a (MCP-OAuth chat-surface half) ===
// The chat-surface half of mid-run MCP-OAuth (docs/plan/web-convergence/PRD.md
// AD-6/AD-7). `McpAuthPort` is the TYPE the host implements (web launcher over
// `createComposerConnectorsPort`; desktop over Electron IPC) — chat-surface
// defines it and the in-chat `mcp_auth` Connect card (`TcChat`) calls it, but the
// full-page redirect / `sessionStorage` stash / `/mcp/oauth/callback` route never
// enter this package (NFR-1/NFR-5). `RunDestination.mcpAuthPort` threads it to the
// card; a `mcp_auth` gate / `mcp_discovery:` suggestion resolves through this port
// (`beginAuth`/`skipAuth`), NEVER the `resolveApproval` `/decision` POST (which
// 404s on discovery). The `approvalKind` carried on `RunApproval`/`TcChatApproval`
// (above) is what the card branches on. The host launcher + OAuth resume land in
// P5b (apps/frontend), NOT here.
export type { McpAuthPort } from "./destinations/run";
// === end WC-P5a (MCP-OAuth chat-surface half) ===

// === WC-P6a — citation projection (in-chat chip resolution, AD-11) ===
// `projectCitations` is a PURE selector over the single canonical run event
// stream (a peer of `projectSubagents` / `projectApprovals`) — no second SSE
// subscription / projector (FR-3.3). It reduces `session.events` into the run-
// scoped `CitationsProvider` inputs (`[c<id>]` source registry + `[[N]]` link
// registry + active/terminal run ids). `RunDestination` performs the wiring
// (mounts the provider around the single TcChat, threads `markdownComponents`);
// the host supplies the nav-aware chip renderer + `onOrdinalSelect`.
export { projectCitations, type CitationProjection } from "./destinations/run";
// === end WC-P6a ===

// === Phase 3 (PR-3.11) run empty/multi-run ===
// The two prototype-gap states `RunDestination` mounts internally: the
// empty/idle goal composer (`RunEmptyState`, FR-3.25 — shown when the
// conversation has no active run; its submit starts a run the shell binds via
// the `runId` seam, no shell remount) and the multi-run selector
// (`RunMultiSelect`, FR-3.26 — shown when the conversation has >1 run; picking
// one rebinds the cockpit via `useRunSession.selectRun`, and it renders no
// chrome for ≤1 run). Both are presentational; `RunDestination` owns the wiring,
// so hosts embedding the cockpit need nothing more, but they are exported for
// standalone hosts / tests.
export {
  RunEmptyState,
  type RunEmptyStateProps,
  type StartRunError,
  RunMultiSelect,
  type RunMultiSelectProps,
} from "./destinations/run";
// === end Phase 3 (PR-3.11) ===

// === Notifications (SSOT in-app toast) ===
// One place converts a failed action mutation (run-start, connector connect,
// profile save) into a user-visible toast — no more silent 500s. In-package
// provider (pure React + timers), mounted once per host; render one <ToastStack/>.
export {
  NotificationCenterProvider,
  useNotify,
  useNotificationCenter,
  messageFromError,
  type NotifyInput,
  type NotifyTone,
  type NotifyAction,
  type AppNotification,
  type NotificationCenter,
} from "./providers/NotificationCenterProvider";
export { ToastStack } from "./shell/ToastStack";
// === end Notifications ===

// === Transport error parsing (shared) ===
// One structured parse of a rejected Transport/IPC request — recovers the
// facade `safe_message` / `code` / `correlation_id` from the raw (possibly
// Electron-prefixed) `err.message`. Consumers surface `safeMessage`, branch on
// `code` (e.g. `configuration_error` → an "Add a provider key" CTA), and demote
// the raw envelope behind a "Show details" affordance. `messageFromError`
// delegates to it.
export {
  parseTransportError,
  humanTransportMessage,
  type ParsedTransportError,
} from "./errors/transportError";
// === end Transport error parsing ===

// === Frontend parity v3 (PRD-A) — shared icon system ===
// The single source of truth for line iconography across the shell (rail,
// settings nav, ⌘K palette, destination rows). Glyphs ported byte-faithfully
// from the v3 design `Icon` registry; render via <Icon name="…" />. No surface
// should inline an <svg> again. See docs/plan/frontend-parity-v3/PRD-A-icon-system.md.
export { Icon, type IconProps } from "./icons/Icon";
export { ICON_PATHS, ICON_NAMES, hasIcon, type IconName } from "./icons/paths";
// === end Frontend parity v3 (PRD-A) ===

// === Frontend parity v3 (PRD-B) — run-status → chip presentation SSOT ===
// One map from a run/conversation status to its StatusPill tone + label + dot,
// so destinations can't disagree (done → jade, stopped → muted, dot on live
// only). See docs/plan/frontend-parity-v3/PRD-B-tokens-and-status-tone.md.
// Exposed as `runStatusTone` — `statusTone` is already taken by the Tools
// destination's tool-health mapping (a different concept).
export {
  statusTone as runStatusTone,
  type RunStatusPresentation,
} from "./shell/statusTone";
// === end Frontend parity v3 (PRD-B) ===

// === Frontend parity v3 (PRD-G) — list-surface primitives ===
// The design row anatomy defined once — a `.pg-lead` intro, a `.sect-h` mono
// section header, one bordered `.rowlist` card per group, and the `.lrow` row
// (leading icon + title/chip/sub + mono meta) — so Activity / Chats / Projects
// compose the same primitives and can't drift. See PRD-G-destination-parity.md.
export {
  PageLead,
  SectionHeader,
  RowList,
  Row,
  type PageLeadProps,
  type SectionHeaderProps,
  type RowListProps,
  type RowProps,
} from "./destinations/_shared";
// === end Frontend parity v3 (PRD-G) ===

// === Generative UI (PRD-06) — word-level text diff ===
// Dependency-free word diff (`wordDiff` → `DiffHunk[]`) + its presentational
// renderer (`DiffText`), the VSCode/Cursor-style red/green inline diff for
// text-shaped surfaces. Consumed by the surface-renderers email tier-1 renderer;
// the `DiffHunk.id` + `DiffText.onHunkToggle` seam is what PRD-09 keys per-hunk
// accept off of. See docs/plan/generative-ui/PRD-06-text-diff.md.
export {
  wordDiff,
  WORD_DIFF_CHAR_CAP,
  type DiffHunk,
  type DiffHunkKind,
} from "./textdiff/wordDiff";
export { DiffText, type DiffTextProps } from "./textdiff/DiffText";
// === end Generative UI (PRD-06) ===

// === Generative UI (PRD-09c) — edit-on-surface overlay ===
// The host-owned edit UI that `TcSurfaceMount` mounts in its `editSlot` OVER the
// pure adapter (adapters stay input-free — D28). `RunDestination` opens it from
// the on-surface "Suggest changes" control and submits `approve_with_edits`
// through the SAME resolveApproval POST machinery the plain approve/reject path
// uses. `MessageEditForm` edits the body (+ PRD-06 hunk toggles → `accepted_hunk_ids`);
// `RecordEditForm` edits changed fields → `fields`. `seedEdits` derives the
// initial draft from the proposal. See docs/plan/generative-ui/PRD-09-edit-and-commit.md.
export {
  EditOverlay,
  MessageEditForm,
  RecordEditForm,
  seedEdits,
  type EditOverlayProps,
  type MessageEditFormProps,
  type RecordEditFormProps,
} from "./surfaces/edit/EditOverlay";
// === end Generative UI (PRD-09c) ===

// === Frontend parity v3 (PRD-D) — ⌘K static command launcher ===
// The 13 v3 design commands shown on an empty query and merged above live
// search hits, so ⌘K works as a keyboard launcher. Hosts map each `intent` to
// navigation via CommandPalette's `onCommand`. See PRD-D-command-palette.md.
export {
  SHELL_COMMANDS,
  filterShellCommands,
  type ShellCommand,
  type ShellCommandIntent,
  type ShellCommandAction,
} from "./shell/shellCommands";
// === end Frontend parity v3 (PRD-D) ===

// === First-Run onboarding (P1) — gate surface + BYOK key form ===
// Presentational SSOT for the FTUE gate (3-state machine: choice → dl → ready,
// + sent → acknowledgment). I/O flows through the injected ProviderKeysPort /
// ModelsPort; skip/complete are host callbacks (the host owns FirstRunStore
// persistence). The dl/ready composer body (P3), the local-model card (P2), and
// the acknowledgment (P3) are injected slots. Hosts import the stylesheet
// directly: `@0x-copilot/chat-surface/src/onboarding/onboarding.css`.
// Design: docs/plan/first-run-onboarding/design-source/SPEC.md.
export {
  FirstRunSurface,
  Gate,
  KeyForm,
  FIRST_RUN_COPY,
  FIRST_RUN_KEY_PROVIDERS,
  checkFirstRunKeyFormat,
  type FirstRunSurfaceProps,
  type GateProps,
  type KeyFormProps,
  type KeyFormConnected,
  type KeyFormFormatCheck,
  type FirstRunStage,
  type FirstRunEngine,
  type FirstRunStore,
  type FirstRunKeyProvider,
  type FirstRunCompleteReason,
  type FirstRunComposerCtx,
  type FirstRunAckCtx,
  type FirstRunLocalCardCtx,
} from "./onboarding";
// === end First-Run onboarding (P1) ===

// === First-Run onboarding (P2) — local-model card + curated preset ===
// The State-A "Download the local model" card, wired to the shipped local-model
// SSE pipeline (`/v1/local-models/{status,pull,list}`). `FirstRunLocalCard`
// fills P1's `renderLocalCard` slot; `useFirstRunLocalModel` drives the pull and
// produces `localModelPct` (P1 flips `modelReady` at === 100);
// `createFirstRunLocalModelsPort(transport)` is the shared Transport-backed
// adapter. `QWEN3_4B_PRESET` is the curated download target (SSOT with Settings).
export {
  FirstRunLocalCard,
  useFirstRunLocalModel,
  createFirstRunLocalModelsPort,
  firstRunModelPillLabel,
  pullPercent,
  resolveInstalledTag,
  type FirstRunLocalCardProps,
  type FirstRunLocalStatus,
  type UseFirstRunLocalModelResult,
  type UseFirstRunLocalModelArgs,
  type FirstRunLocalModelsPort,
} from "./onboarding";
export { QWEN3_4B_PRESET } from "./settings";
// === end First-Run onboarding (P2) ===
// === First-Run onboarding (P3) — composer + chips + ack + launch ===
// State B (`OnboardingComposer` mounts the real AssistantComposer under the
// "What should we run first?" H1 + the 3 starter chips) and State C
// (`Acknowledgment`), plus the run-create orchestration: `useFirstRunLaunch`
// drives the two-step create through the host-injected `FirstRunRunsPort`
// (create only — the handoff target owns streaming), the "Queued — starts when
// the model lands" deferral, and the ~1.5s handoff. `firstRunAckLines` derives
// the three verbatim echo lines. Fills P1's `renderComposer`/`renderAcknowledgment`
// slots; bound by the desktop host (web binder deferred, as in P1).
// Design: docs/plan/first-run-onboarding/design-source/SPEC.md.
export {
  OnboardingComposer,
  ONBOARDING_COMPOSER_COPY,
  SuggestionChips,
  FIRST_RUN_SUGGESTIONS,
  Acknowledgment,
  FIRST_RUN_ACK_TITLES,
  FIRST_RUN_ACK_STALLED,
  firstRunAckLines,
  // P8 §7 — ack-state derivation for hosts that render `Acknowledgment`
  // themselves (both of them): phase → state, and the state's title/note/action.
  firstRunAckStateForPhase,
  firstRunAckTitle,
  firstRunAckNote,
  firstRunAckAction,
  useFirstRunLaunch,
  type OnboardingComposerProps,
  type FirstRunSuggestion,
  type SuggestionChipsProps,
  type AcknowledgmentProps,
  type AcknowledgmentVariant,
  type FirstRunAckEngine,
  type FirstRunToolsState,
  type FirstRunAckLines,
  type FirstRunAckState,
  type FirstRunLaunchPhase,
  type FirstRunLaunchPayload,
  type UseFirstRunLaunch,
  type UseFirstRunLaunchOptions,
  type FirstRunRunsPort,
  type FirstRunCreateRunInput,
  type FirstRunLaunchResult,
} from "./onboarding";
// === end First-Run onboarding (P3) ===

// === First-Run onboarding (P4 — tools popover) ===
// The connector-aware Tools popover that replaces the flat `ToolPicker` FOR the
// FTUE: a built-in web-search toggle (default on), connected connectors with a
// per-run active/paused toggle, curated 1-click "Connect" rows
// (`requiresPreRegisteredClient` → host routes to the custom-config form), and
// a Custom MCP entry. Data flows through the host-injected
// `FirstRunConnectorsPort` (the existing `/v1/mcp/*` facade routes — NOT
// rebuilt) and is classified by the pure `projectFirstRunConnectors` (a
// chat-surface copy of the web app's `projectChatConnectors`, since `apps/*`
// can't be imported). `ComposerToolsButton` is the composer trigger.
// Design: docs/plan/first-run-onboarding/design-source/SPEC.md.
export {
  ToolsPopover,
  TOOLS_POPOVER_COPY,
  ComposerToolsButton,
  COMPOSER_TOOLS_BUTTON_COPY,
  projectFirstRunConnectors,
  firstRunActiveToolCount,
  type ToolsPopoverProps,
  type ComposerToolsButtonProps,
  type FirstRunConnectorProjection,
  type FirstRunConnectedConnector,
  type FirstRunInstallableConnector,
  type FirstRunConnectorsPort,
} from "./onboarding";
// Neutral alias for host composer-chrome wiring (chat/run reuse the FTUE Tools
// popover's port, so the shape is the same — the alias is just a non-FTUE name).
export type { FirstRunConnectorsPort as ComposerConnectorsPort } from "./onboarding";
// === end First-Run onboarding (P4 — tools popover) ===
// === First-Run onboarding (P4 — wallet chip) ===
// Top-bar SIWE identity pill for the FirstRunSurface `walletChipSlot`:
// `WalletChip` is pure/presentational (`0x{4}…{4}` + jade dot, `truncateAddress`
// lives here because the server returns the FULL EIP-55 address);
// `FirstRunProfileProvider`/`useFirstRunProfile` load the identity ONCE through
// the host-injected `FirstRunProfilePort` (over `GET /v1/me/profile`);
// `FirstRunWalletChip` is the connected sink both hosts drop into the slot.
// Design: docs/plan/first-run-onboarding/design-source/SPEC.md.
export {
  WalletChip,
  truncateAddress,
  FirstRunProfileProvider,
  FirstRunWalletChip,
  useFirstRunProfile,
  type WalletChipProps,
  type FirstRunProfilePort,
  type WalletProfileView,
  type FirstRunProfileProviderProps,
  type FirstRunProfileState,
} from "./onboarding";
// === end First-Run onboarding (P4 — wallet chip) ===

// === Composer parity — provider brand marks ===
// The bundled identity of a model provider: `PROVIDER_BRAND_COLOR` /
// `providerBrandColor` give the composer pill's 6px dot its hue, and
// <ProviderMark> gives a popover row's 24px badge its glyph. Marks are inline
// SVG authored in this package — never an <img> or a favicon lookup, which
// would break offline AND leak the user's configured providers. Providers with
// no bundled mark (openrouter today) render `providerInitials` instead of a
// guessed logo. Hosts pass a display label for unknown providers so the
// initials read as a monogram ("Together AI" → "To").
export {
  PROVIDER_BRAND_COLOR,
  PROVIDER_BRAND_COLOR_FALLBACK,
  PROVIDER_MARK_IDS,
  ProviderMark,
  hasProviderMark,
  providerBrandColor,
  providerInitials,
  type ProviderMarkProps,
  type ProviderMarkTone,
} from "./icons/providerMarks";
// === end Composer parity — provider brand marks ===
