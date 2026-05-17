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

// === Phase 0-B ports facade ===
export * from "./ports";
// === end Phase 0-B ===

// === Phase 1-B chat-shell-layout ===
export { AppRail, ContextPanel, Topbar, RightRail } from "./shell";
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
  ToolPicker,
  type ToolPickerProps,
  type ToolDescriptor,
  ModelPicker,
  type ModelPickerProps,
  type ModelDescriptor,
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

// === Phase 2-A / 3 destinations ===
export {
  ChatsDestination,
  ChatsSidebar,
  type ChatsSidebarProps,
} from "./destinations/chats";
export {
  HomeDestination,
  type ConversationId,
  type FavoriteTool,
  type HomePayload,
  type PinnedChat,
  type RecentRun,
  type RecentRunStatus,
  type RunId,
  type SkillId,
} from "./destinations/home";
export {
  InboxDestination,
  type InboxFilter,
  type InboxItem,
  type InboxItemId,
  type InboxItemKind,
  type InboxPayload,
} from "./destinations/inbox";
export {
  TodosDestination,
  type Todo,
  type TodoId,
  type TodoStatusFilter,
  type TodosPayload,
} from "./destinations/todos";
export {
  ProjectsDestination,
  type Project,
  type ProjectId,
} from "./destinations/projects";
export {
  LibraryDestination,
  type LibraryItem,
  type LibraryItemId,
  type LibraryItemKind,
} from "./destinations/library";
export { AgentsDestination, type AgentRunRow } from "./destinations/agents";
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
export {
  MemoryDestination,
  type Memory,
  type MemoryDestinationProps,
  type MemoryType,
} from "./destinations/memory";
// === end Phase 2-A / 3 destinations ===
