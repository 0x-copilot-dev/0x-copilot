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
  type AuthWorkspaceParams,
  type ChannelName,
  type RendererSession,
  type StreamEventKind,
  type StreamEventPayload,
  type TransportRequestParams,
  type TransportSubscribeParams,
  type TransportUnsubscribeParams,
  type WindowBridge,
} from "./ipc";
// === end Phase 1-C ===
