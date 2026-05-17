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

// === Phase 0-B ports facade ===
export * from "./ports";
// === end Phase 0-B ===
