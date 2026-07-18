import { z } from "zod";

// Zod contracts for the capability IPC channels (AC5 slice 1). Main validates
// every inbound renderer payload against these; the outbound renderer view is
// validated against `RendererGrantSchema` so an accidental extra field (a host
// path, say) fails closed instead of leaking.

export const GrantModeSchema = z.enum([
  "read_only",
  "read_write_no_delete",
  "read_write",
]);

// capability.request-folder-grant — the renderer picks a mode and may suggest
// a display label. It NEVER submits a path; main owns the folder selection.
export const RequestFolderGrantParamsSchema = z
  .object({
    mode: GrantModeSchema,
    // Optional display hint. Omit → main derives a sanitized label from the
    // chosen folder's basename. Sanitized again in main regardless.
    label: z.string().min(1).max(120).optional(),
  })
  .strict();
export type RequestFolderGrantParams = z.infer<
  typeof RequestFolderGrantParamsSchema
>;

// capability.list-grants — no params.
export const ListGrantsParamsSchema = z.object({}).strict();

// capability.revoke-grant — the grantId is a v4 uuid minted by main.
export const RevokeGrantParamsSchema = z
  .object({
    grantId: z.string().uuid(),
  })
  .strict();
export type RevokeGrantParams = z.infer<typeof RevokeGrantParamsSchema>;

// The ONLY grant shape allowed to cross the IPC boundary. `.strict()` is the
// structural guarantee that no host `root` (or any other field) leaks: parsing
// an internal Grant through this schema throws on the extra key.
export const RendererGrantSchema = z
  .object({
    grantId: z.string().min(1),
    mode: GrantModeSchema,
    label: z.string(),
    status: z.enum(["active", "revoked"]),
  })
  .strict();
export type RendererGrantOut = z.infer<typeof RendererGrantSchema>;
