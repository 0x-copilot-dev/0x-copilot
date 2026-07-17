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
  // "Continue with Google" via the facade-brokered OIDC flow: main opens
  // the system browser at {facade}/v1/auth/oidc/google/start and receives
  // the bearer via the loopback + /v1/auth/oidc/callback JSON handoff.
  // Same bearer-never-crosses-IPC rule as the other auth channels.
  authSignInGoogle: "auth.sign-in-google",
  authSignOut: "auth.sign-out",
  authRefresh: "auth.refresh",
  // Phase 6C tier-2 adapter lifecycle. Main owns the install pipeline
  // (Q1-Q5); the renderer owns the chat-surface registry. Adapter source
  // crosses the boundary, never adapter objects (functions cannot be
  // structured-cloned across Electron IPC).
  tier2Install: "tier2.install",
  tier2Uninstall: "tier2.uninstall",
  tier2MarkBroken: "tier2.mark-broken",
  // Renderer → main: forwarded when the renderer's error boundary catches
  // a live tier-2 render throw (Q6 trip).
  tier2BoundaryError: "tier2.boundary-error",
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

// === Phase 6C tier-2 lifecycle ===

const Tier2RenderMethodSchema = z.enum(["renderCurrent", "renderDiff"]);

export const Tier2InstallPayloadSchema = z
  .object({
    scheme: z.string().min(1),
    version: z.number().int().nonnegative(),
    source: z.string().min(1),
    generatedAt: z.string().min(1),
    generatorModel: z.string().min(1),
  })
  .strict();
export type Tier2InstallPayload = z.infer<typeof Tier2InstallPayloadSchema>;

export const Tier2UninstallPayloadSchema = z
  .object({
    scheme: z.string().min(1),
    version: z.number().int().nonnegative(),
  })
  .strict();
export type Tier2UninstallPayload = z.infer<typeof Tier2UninstallPayloadSchema>;

export const Tier2MarkBrokenPayloadSchema = z
  .object({
    scheme: z.string().min(1),
    version: z.number().int().nonnegative(),
    method: Tier2RenderMethodSchema,
    reason: z.string().min(1),
  })
  .strict();
export type Tier2MarkBrokenPayload = z.infer<
  typeof Tier2MarkBrokenPayloadSchema
>;

export const Tier2BoundaryErrorPayloadSchema = z
  .object({
    scheme: z.string().min(1),
    version: z.number().int().nonnegative(),
    method: Tier2RenderMethodSchema,
    message: z.string().min(1),
  })
  .strict();
export type Tier2BoundaryErrorPayload = z.infer<
  typeof Tier2BoundaryErrorPayloadSchema
>;

// === end Phase 6C ===

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
