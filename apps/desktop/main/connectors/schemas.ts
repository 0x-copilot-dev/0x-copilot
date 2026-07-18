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

export const ConnectParamsSchema = z
  .object({
    slug: z.string().min(1),
    productScope: z.enum(["read", "draft"]).optional(),
  })
  .strict();

export type ConnectParams = z.infer<typeof ConnectParamsSchema>;

// -- Outbound (main → renderer) — SAFE views only ---------------------------

export const ConnectorConnectionResultSchema = z
  .object({
    server_id: z.string(),
    connector_slug: z.string(),
    display_group: z.string(),
    auth_state: z.string(),
  })
  .strict();

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
