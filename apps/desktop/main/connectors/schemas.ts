// AC9 — connector IPC payload schemas (Zod).
//
// Inbound: strict-parse the renderer's request so only a stable slug (+ an
// optional product scope) crosses into main — never a redirect URI, port, or
// token. Outbound: strict-parse the connection result / catalog so an
// accidental extra key (a token field, say) throws here instead of reaching
// the renderer. Mirrors the capability grant strict-parse defense.

import { z } from "zod";

// -- Inbound (renderer → main) ----------------------------------------------

export const ListCatalogParamsSchema = z.object({}).strict();

/**
 * The one authorization request. A caller supplies whichever identity it holds
 * — a marketing `slug` (catalog rows, FTUE), an MCP `serverId` (an installed
 * server, a run's consent gate), or both — and main decides the mechanism.
 *
 * `.refine` rather than a union: "at least one identity" is the real invariant,
 * and encoding it here means the IPC boundary rejects an empty request instead
 * of `authorize` having to. Still no redirect URI, port, or token — main
 * derives the loopback callback itself.
 */
const OAuthClientParamsSchema = z
  .object({
    client_id: z.string().min(1),
    client_secret: z.string().min(1).optional(),
    token_endpoint_auth_method: z.string().min(1).optional(),
    scope: z.string().min(1).optional(),
    authorization_endpoint: z.string().url().optional(),
    token_endpoint: z.string().url().optional(),
  })
  .strict();

export const AuthorizeParamsSchema = z
  .object({
    slug: z.string().min(1).optional(),
    serverId: z.string().min(1).optional(),
    productScope: z.enum(["read", "draft"]).optional(),
    /**
     * The ONE secret this boundary accepts inbound, and it is deliberate: it is
     * the user's own OAuth app credential, typed by them, for a vendor that
     * supports no dynamic registration. It travels renderer → main → facade and
     * is encrypted into the backend TokenVault on arrival; main neither stores
     * nor logs it. This does not weaken the rule the schema exists to enforce —
     * that rule is about what main will ACCEPT AS AUTHORITY (a redirect URI, a
     * port, a provider token) and what it will HAND BACK to the renderer, and
     * a client_id the user pasted is neither.
     */
    oauthClient: OAuthClientParamsSchema.optional(),
    /** Redirect style the provider was registered against — see
     *  `ConnectorConnectOptions.callbackMode`. Not a redirect URI: main still
     *  derives the actual callback itself, so this cannot be used to point the
     *  flow anywhere of the caller's choosing. */
    callbackMode: z.enum(["loopback", "deep_link"]).optional(),
  })
  .strict()
  .refine((value) => value.slug !== undefined || value.serverId !== undefined, {
    message: "authorize requires a slug or a serverId",
  });

export type AuthorizeParams = z.infer<typeof AuthorizeParamsSchema>;

// -- Outbound (main → renderer) — SAFE views only ---------------------------

export const ConnectorConnectionResultSchema = z
  .object({
    server_id: z.string(),
    connector_slug: z.string(),
    display_group: z.string(),
    auth_state: z.string(),
  })
  .strict();

/**
 * What `connector.authorize` returns — deliberately NARROWER than the profile
 * route's own result, because it must describe both topologies honestly. The
 * MCP route knows the server it authorized and nothing more, so `auth_state`
 * and `connector_slug` are nullable rather than padded with a plausible value.
 * `display_group` is dropped entirely: it is profile presentation metadata, and
 * no caller of this verb reads it.
 */
export const ConnectorAuthorizationResultSchema = z
  .object({
    server_id: z.string(),
    connector_slug: z.string().nullable(),
    auth_state: z.string().nullable(),
  })
  .strict();

/**
 * What crosses the IPC hop for `connector.authorize` — an OUTCOME, not a value
 * plus an exception channel.
 *
 * A cancel and a supersede are ordinary endings, so they resolve. Only a real
 * failure rejects. That is what stops `ipcMain.handle` printing
 * `Error occurred in handler for 'connector.authorize'` with a stack trace
 * every time a user presses Cancel or starts a second connect — output that
 * read as a crash for the two most ordinary things a user can do.
 */
export const ConnectorAuthorizationOutcomeSchema = z.discriminatedUnion(
  "outcome",
  [
    z
      .object({
        outcome: z.literal("connected"),
        result: ConnectorAuthorizationResultSchema,
      })
      .strict(),
    z.object({ outcome: z.literal("cancelled") }).strict(),
    z.object({ outcome: z.literal("superseded") }).strict(),
  ],
);

const CapabilitySummarySchema = z
  .object({
    id: z.string(),
    label: z.string(),
    status: z.enum(["supported", "scope_required", "unsupported"]),
    read_only: z.boolean(),
  })
  .strict();

const CatalogEntrySchema = z
  .object({
    slug: z.string(),
    display_name: z.string(),
    description: z.string(),
    display_group: z.string(),
    release_stage: z.enum(["stable", "preview"]),
    availability: z.string(),
    requested_permissions: z.array(z.string()),
    capabilities: z.array(CapabilitySummarySchema),
    unsupported_capabilities: z.array(z.string()),
    reference_urls: z.array(z.string()),
  })
  .strict();

export const ConnectorCatalogResponseSchema = z
  .object({ entries: z.array(CatalogEntrySchema) })
  .strict();
