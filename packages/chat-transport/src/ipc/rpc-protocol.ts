import { z } from "zod";

// Allowlisted IPC channel names. The preload (Agent 1-A) enforces this set
// at the contextBridge boundary; main (handlers.ts) registers exactly these.
// Both renderer and main import this constant — there is no other source.
export const CHANNELS = {
  transportRequest: "transport.request",
  transportSubscribe: "transport.subscribe",
  transportUnsubscribe: "transport.unsubscribe",
  // Single round-trip refresh of the cached session + capabilities. Phase 5
  // replaces this with a real reauthenticate flow; today's renderer uses the
  // bootstrap values handed at IpcTransport construction.
  transportSessionSnapshot: "transport.session-snapshot",
  streamEvent: "transport.stream-event",
  // Phase 5 auth channels. Bearer tokens NEVER cross the IPC boundary —
  // these channels only return a renderer-safe view (workspace + display
  // claims) and the actual bearer is attached in main when the transport
  // makes its outbound HTTP call. See PRD §6.7 / D24.
  authGetSession: "auth.get-session",
  authSignIn: "auth.sign-in",
  authSignOut: "auth.sign-out",
  authRefresh: "auth.refresh",
} as const;

export type ChannelName = (typeof CHANNELS)[keyof typeof CHANNELS];

export const CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CHANNELS),
);

export function isAllowedChannel(name: string): name is ChannelName {
  return CHANNEL_VALUES.has(name);
}

const HttpMethodSchema = z.enum(["GET", "POST", "PATCH", "PUT", "DELETE"]);

const QueryParamValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.undefined(),
]);

export const TransportRequestParamsSchema = z.object({
  method: HttpMethodSchema,
  path: z.string().min(1),
  query: z.record(z.string(), QueryParamValueSchema).optional(),
  body: z.unknown().optional(),
  headers: z.record(z.string(), z.string()).optional(),
});
export type TransportRequestParams = z.infer<
  typeof TransportRequestParamsSchema
>;

export const TransportSubscribeParamsSchema = z.object({
  subscriptionId: z.string().min(1),
  path: z.string().min(1),
  query: z.record(z.string(), QueryParamValueSchema).optional(),
  eventName: z.string().optional(),
});
export type TransportSubscribeParams = z.infer<
  typeof TransportSubscribeParamsSchema
>;

export const TransportUnsubscribeParamsSchema = z.object({
  subscriptionId: z.string().min(1),
});
export type TransportUnsubscribeParams = z.infer<
  typeof TransportUnsubscribeParamsSchema
>;

export const EmptyParamsSchema = z.object({}).strict();

export const AuthWorkspaceParamsSchema = z
  .object({
    workspaceId: z.string().min(1).max(256),
  })
  .strict();
export type AuthWorkspaceParams = z.infer<typeof AuthWorkspaceParamsSchema>;

export const RendererSessionSchema = z
  .object({
    workspaceId: z.string().min(1),
    expiresAt: z.number(),
    displayName: z.string().nullable(),
    email: z.string().nullable(),
  })
  .strict();
export type RendererSession = z.infer<typeof RendererSessionSchema>;

export const StreamEventKindSchema = z.enum([
  "open",
  "message",
  "error",
  "closed",
]);
export type StreamEventKind = z.infer<typeof StreamEventKindSchema>;

export const StreamEventPayloadSchema = z.object({
  subscriptionId: z.string().min(1),
  kind: StreamEventKindSchema,
  message: z.string().optional(),
  errorMessage: z.string().optional(),
});
export type StreamEventPayload = z.infer<typeof StreamEventPayloadSchema>;

// Thrown by handlers when an incoming IPC payload fails Zod validation.
// Crosses the IPC boundary as a rejected promise (Electron serialises
// `name` + `message`; `issues` is informational on the main side).
export class IpcValidationError extends Error {
  readonly channel: string;
  readonly issues: unknown;

  constructor(channel: string, issues: unknown) {
    super(`IPC payload validation failed for ${channel}`);
    this.name = "IpcValidationError";
    this.channel = channel;
    this.issues = issues;
  }
}
