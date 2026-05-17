// Web substrate port implementations + the host-side provider.
//
// Mirrors the cross-audit.md §5.4 convention: every port is provided by
// the host via a React provider, with no-op / browser-API-backed web
// implementations here and (future) native implementations in
// `apps/desktop/src/main/ports/`. Destinations call `usePort(name)`
// without ever checking the substrate.

export { WebBadgePort } from "./BadgeWeb";
export { WebClipboardPort } from "./ClipboardWeb";
export { WebFilePickerPort } from "./FilePickerWeb";
export { WebNotificationPort } from "./NotificationWeb";
export type { WebNotificationPortConfig } from "./NotificationWeb";
export {
  PortProvider,
  usePort,
  usePorts,
  type PortBundle,
} from "./PortProvider";
