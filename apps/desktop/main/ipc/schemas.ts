// Thin re-export wrapper. The Zod schemas live in chat-transport because
// the renderer also imports them (for the channel-name constants); main
// validates against the same source so both sides cannot drift.
export {
  CHANNELS,
  CHANNEL_VALUES,
  EmptyParamsSchema,
  IpcValidationError,
  isAllowedChannel,
  StreamEventKindSchema,
  StreamEventPayloadSchema,
  TransportRequestParamsSchema,
  TransportSubscribeParamsSchema,
  TransportUnsubscribeParamsSchema,
  type ChannelName,
  type StreamEventKind,
  type StreamEventPayload,
  type TransportRequestParams,
  type TransportSubscribeParams,
  type TransportUnsubscribeParams,
} from "@enterprise-search/chat-transport";
