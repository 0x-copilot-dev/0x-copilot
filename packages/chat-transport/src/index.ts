export type { Transport } from "./transport";
export type {
  HttpMethod,
  QueryParamValue,
  Session,
  SseSubscribeOptions,
  SseSubscription,
  TransportCapabilities,
  TypedRequest,
} from "./types";
export { UnauthorizedError } from "./types";
export { WebTransport, type WebTransportConfig } from "./web/WebTransport";
export {
  buildEmailEventSchedule,
  EMAIL_FIXTURE,
  MockTransport,
  type EmailFixture,
  type EmailFixtureDraft,
  type EmailFixturePendingDiff,
  type MockTransportConfig,
} from "./mock";
export {
  withBearerRefresh,
  type BearerRefreshFn,
  type BearerRefreshResult,
  type WithBearerRefreshOptions,
} from "./auth";

// === Phase 1-C IPC transport ===
export {
  IpcTransport,
  type IpcTransportConfig,
  CHANNELS,
  CHANNEL_VALUES,
  IpcValidationError,
  isAllowedChannel,
  EmptyParamsSchema,
  StreamEventKindSchema,
  StreamEventPayloadSchema,
  TransportRequestParamsSchema,
  TransportSubscribeParamsSchema,
  TransportUnsubscribeParamsSchema,
  AuthWorkspaceParamsSchema,
  RendererSessionSchema,
  Tier2InstallPayloadSchema,
  Tier2UninstallPayloadSchema,
  Tier2MarkBrokenPayloadSchema,
  Tier2BoundaryErrorPayloadSchema,
  BootPhaseSchema,
  BootStatusPayloadSchema,
  UpdateStatusKindSchema,
  UpdateStatusPayloadSchema,
  type AuthWorkspaceParams,
  type BootPhase,
  type BootStatusPayload,
  type UpdateStatusKind,
  type UpdateStatusPayload,
  type ChannelName,
  type RendererSession,
  type StreamEventKind,
  type StreamEventPayload,
  type Tier2BoundaryErrorPayload,
  type Tier2InstallPayload,
  type Tier2MarkBrokenPayload,
  type Tier2UninstallPayload,
  type TransportRequestParams,
  type TransportSubscribeParams,
  type TransportUnsubscribeParams,
  type WindowBridge,
} from "./ipc";
// === end Phase 1-C ===
