import { z } from "zod";

import { TransportHttpError, UnauthorizedError } from "../types";

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
  // Read-only production/dev posture flag for the renderer. Lets SignInGate
  // hide the "Use locally, no account" (dev-mint) option in production posture
  // — a real install must only offer wallet + Google. Carries no secret.
  authGetPosture: "auth.get-posture",
  authSignIn: "auth.sign-in",
  // "Continue with Google" via the facade-brokered OIDC flow: main opens
  // the system browser at {facade}/v1/auth/oidc/google/start and receives
  // the bearer via the loopback + /v1/auth/oidc/callback JSON handoff.
  // Same bearer-never-crosses-IPC rule as the other auth channels.
  authSignInGoogle: "auth.sign-in-google",
  // "Connect wallet" (SIWE): main opens the system browser at the
  // facade-served /wallet.html with a loopback ?handoff= target and
  // receives the bearer via the loopback redirect. Same
  // bearer-never-crosses-IPC rule as the other auth channels.
  authSignInWallet: "auth.sign-in-wallet",
  // Cancel the pending system-browser sign-in (Google or wallet). Main
  // closes the armed loopback listener so the pending sign-in promise
  // rejects and the port frees — the renderer treats that rejection as a
  // quiet return to the pick screen, not a failure. No-op when nothing is
  // pending. Carries no payload and returns nothing.
  authCancelSignIn: "auth.cancel-sign-in",
  // Account-linking (PRD FR-L1/L2) — authenticated LINK flows driven from
  // Settings. Main opens the system browser (Google OAuth link / wallet
  // signing), completes the link against the caller's existing session, and
  // returns ONLY a renderer-safe outcome (status + provider). Same
  // bearer-never-crosses-IPC rule as the sign-in channels.
  authLinkGoogle: "auth.link-google",
  authLinkWallet: "auth.link-wallet",
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
  // Main → renderer push: ServiceSupervisor boot progress for the packaged
  // desktop (postgres + migrations + the three python services). The
  // renderer's BootProgress gate listens on this channel and only mounts
  // the app shell after a `phase: "ready"` payload arrives. In dev
  // (unsupervised) mode main immediately pushes a synthetic ready payload.
  bootStatus: "boot.status",
  // Main → renderer push: electron-updater lifecycle (GitHub Releases). The
  // renderer can surface an "update ready — restart to apply" affordance; the
  // actual install only happens on quit so migrations never run under the old
  // version. No-op on unsigned/dev builds. Renderer never triggers installs.
  updateStatus: "update.status",
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

// Return shape for CHANNELS.authGetPosture. Renderer-safe: a single boolean,
// no bearer, no identity.
export const AuthPosturePayloadSchema = z
  .object({
    productionPosture: z.boolean(),
  })
  .strict();
export type AuthPosturePayload = z.infer<typeof AuthPosturePayloadSchema>;

export const RendererSessionSchema = z
  .object({
    workspaceId: z.string().min(1),
    expiresAt: z.number(),
    displayName: z.string().nullable(),
    email: z.string().nullable(),
  })
  .strict();
export type RendererSession = z.infer<typeof RendererSessionSchema>;

// --- Account-linking IPC (PRD FR-L1/L2) ---------------------------------
// Params for CHANNELS.authLinkWallet: which workspace + whether the user has
// consented to a merge (FR-U2). Google link takes only the workspace.
export const AuthLinkWalletParamsSchema = z
  .object({
    workspaceId: z.string().min(1).max(256),
    confirmMerge: z.boolean(),
  })
  .strict();
export type AuthLinkWalletParams = z.infer<typeof AuthLinkWalletParamsSchema>;

// Renderer-safe outcome of a LINK flow — NO bearer, NO absorbed-account ids.
// `merge_required` means the identity is owned by another account (FR-M1);
// the renderer re-invokes with `confirmMerge: true` after the user consents.
export const LinkOutcomeStatusSchema = z.enum([
  "linked",
  "already_linked",
  "merged",
  "merge_required",
  "error",
]);
export type LinkOutcomeStatus = z.infer<typeof LinkOutcomeStatusSchema>;

export const AuthLinkOutcomeSchema = z
  .object({
    status: LinkOutcomeStatusSchema,
    provider: z.string().nullable().optional(),
    emailUpgraded: z.boolean().optional(),
    /** User-safe message for the `error` status (never leaks account ids). */
    message: z.string().nullable().optional(),
  })
  .strict();
export type AuthLinkOutcome = z.infer<typeof AuthLinkOutcomeSchema>;

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

// === Desktop supervisor boot status ===

// Boot phases in the order the ServiceSupervisor walks them. "ready" is
// terminal-success; a payload with `fatal: true` is terminal-failure and
// keeps the phase that failed (e.g. `{ phase: "migrations", fatal: true }`).
export const BootPhaseSchema = z.enum([
  "secrets",
  "ports",
  "postgres",
  "migrations",
  "services",
  "health",
  "ready",
  "stopping",
]);
export type BootPhase = z.infer<typeof BootPhaseSchema>;

export const BootStatusPayloadSchema = z
  .object({
    phase: BootPhaseSchema,
    message: z.string(),
    percent: z.number().min(0).max(100),
    fatal: z.boolean().optional(),
  })
  .strict();
export type BootStatusPayload = z.infer<typeof BootStatusPayloadSchema>;

// === end desktop supervisor boot status ===

// === Desktop auto-update status ===

// electron-updater lifecycle, surfaced Main → renderer on CHANNELS.updateStatus.
// "downloaded" means an update is staged and will install on the NEXT quit.
// "error" carries a human-readable message; it is never fatal to the running
// app. Unsigned/dev builds never emit anything (the updater no-ops).
export const UpdateStatusKindSchema = z.enum([
  "checking",
  "available",
  "not-available",
  "downloaded",
  "error",
]);
export type UpdateStatusKind = z.infer<typeof UpdateStatusKindSchema>;

export const UpdateStatusPayloadSchema = z
  .object({
    kind: UpdateStatusKindSchema,
    /** Target version when known (available/downloaded). */
    version: z.string().optional(),
    /** Human-readable detail (error message, release notes summary). */
    message: z.string().optional(),
  })
  .strict();
export type UpdateStatusPayload = z.infer<typeof UpdateStatusPayloadSchema>;

// === end desktop auto-update status ===

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

// === Transport request result envelope ===
//
// Electron's ipcMain.handle rejection path flattens thrown errors to a
// mangled `message` string — structured HTTP failures (status + FastAPI
// `detail`, e.g. the account-linking 409 `merge_required` /
// `last_sign_in_method` codes) would not survive the hop. Main therefore
// RESOLVES transport.request with this envelope; the renderer-side
// IpcTransport unwraps it and rehydrates the typed error so renderer code
// branches identically on web and desktop. Non-HTTP errors (network,
// validation) still reject the invoke and flatten — they carry no
// structure worth preserving.

export interface TransportHttpErrorWire {
  readonly status: number;
  readonly message: string;
  readonly detail: unknown;
}

const TRANSPORT_RESULT_KIND = "transport-result" as const;

export type TransportRequestResult =
  | {
      readonly kind: typeof TRANSPORT_RESULT_KIND;
      readonly ok: true;
      readonly value: unknown;
    }
  | {
      readonly kind: typeof TRANSPORT_RESULT_KIND;
      readonly ok: false;
      readonly error: TransportHttpErrorWire;
    };

export function wrapTransportValue(value: unknown): TransportRequestResult {
  return { kind: TRANSPORT_RESULT_KIND, ok: true, value };
}

/**
 * Wire-encode a typed HTTP error for the envelope, or null when the error
 * carries no HTTP structure (caller should rethrow those unchanged).
 */
export function toTransportHttpErrorWire(
  err: unknown,
): TransportHttpErrorWire | null {
  if (err instanceof TransportHttpError) {
    return { status: err.status, message: err.message, detail: err.detail };
  }
  if (err instanceof UnauthorizedError) {
    return { status: 401, message: err.message, detail: null };
  }
  return null;
}

export function wrapTransportError(
  error: TransportHttpErrorWire,
): TransportRequestResult {
  return { kind: TRANSPORT_RESULT_KIND, ok: false, error };
}

function isTransportRequestResult(raw: unknown): raw is TransportRequestResult {
  return (
    typeof raw === "object" &&
    raw !== null &&
    (raw as { kind?: unknown }).kind === TRANSPORT_RESULT_KIND &&
    typeof (raw as { ok?: unknown }).ok === "boolean"
  );
}

/**
 * Renderer-side inverse of the envelope: unwrap the value or throw the
 * rehydrated typed error. Raw (non-envelope) values pass through verbatim
 * so an older main / test double that returns the bare response keeps
 * working.
 */
export function unwrapTransportResult<T>(raw: unknown): T {
  if (!isTransportRequestResult(raw)) {
    return raw as T;
  }
  if (raw.ok) {
    return raw.value as T;
  }
  const { status, message, detail } = raw.error;
  if (status === 401) {
    throw new UnauthorizedError(message);
  }
  throw new TransportHttpError(status, message, detail);
}

// === end transport request result envelope ===

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
