export type {
  HttpMethod,
  QueryParamValue,
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "./Transport";
export { UnauthorizedError } from "./Transport";

export type { ArtifactRoute, NavigateOptions, Router } from "./Router";
export type { KeyValueStore } from "./KeyValueStore";
export type { PresenceSignal, PresenceState } from "./PresenceSignal";
export type { SurfaceEvent, SurfaceHandle, SurfaceHost } from "./SurfaceHost";

// === Phase 0.5 shared primitives — additional substrate ports ===
export type { BadgePort } from "./BadgePort";
export type { NotificationPort, NotifyPayload } from "./NotificationPort";
export type {
  FilePickerOptions,
  FilePickerPort,
  FilePickerSelection,
} from "./FilePickerPort";
export type { ClipboardPort } from "./ClipboardPort";
// === end Phase 0.5 ===

// === Phase 12 — palette search port ===
export type { PaletteSearchPort } from "./PaletteSearchPort";
// === end Phase 12 ===

// === PRD-07 — project detail Chats + Files data seam ===
export type { ProjectDataPort } from "./ProjectDataPort";
// === end PRD-07 ===
