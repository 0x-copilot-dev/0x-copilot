import { z } from "zod";

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SHA256_HEX = /^[a-f0-9]{64}$/u;

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
// a display label.
//
// `path` is the ONE case where a folder is named rather than chosen, and it is
// narrow on purpose. It exists for the mid-run ask: the agent hit a folder it
// has no grant for, the backend raised a card NAMING THAT FOLDER, the user read
// it and chose "always allow". Sending them to a free picker at that point is
// the widening this whole subsystem exists to prevent — they could land on the
// parent, and the pill would then claim access to a tree nobody agreed to.
//
// It does not weaken main's ownership of authority. Main re-resolves the path
// (realpath, must be a directory), re-derives the label from the resolved
// basename, forces `read_only`, and still runs `assertGrantableRoot` — so a
// renderer naming `/`, `~`, the app's own userData, or a credential directory
// is refused exactly as a bypassed picker would be. See
// `CapabilityService.requestFolderGrant`.
export const RequestFolderGrantParamsSchema = z
  .object({
    mode: GrantModeSchema,
    // Optional display hint. Omit → main derives a sanitized label from the
    // chosen folder's basename. Sanitized again in main regardless, and
    // IGNORED entirely when `path` is present (see the service).
    label: z.string().min(1).max(120).optional(),
    // Optional exact folder. Omit → the native picker, unchanged.
    path: z.string().min(1).max(1024).optional(),
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

// capability.decide-workspace-approval — C3's entire renderer-controlled
// input. It intentionally has no target ref, physical root, prepared ref,
// permit, content reference, file handle, or generic operation field. Main
// forwards only the exact snapshot values that A4 verifies against its ledger.
export const WorkspaceApprovalStageSnapshotSchema = z
  .object({
    runId: z.string().regex(OPAQUE_ID),
    stageId: z.string().regex(OPAQUE_ID),
    revision: z.number().int().positive(),
    proposalDigest: z.string().regex(SHA256_HEX),
    targetDigest: z.string().regex(SHA256_HEX),
  })
  .strict();
export type WorkspaceApprovalStageSnapshot = z.infer<
  typeof WorkspaceApprovalStageSnapshotSchema
>;

export const WorkspaceApprovalHostDecisionRequestSchema = z
  .object({
    snapshot: WorkspaceApprovalStageSnapshotSchema,
    decision: z.enum(["approve", "reject"]),
  })
  .strict();
export type WorkspaceApprovalHostDecisionRequest = z.infer<
  typeof WorkspaceApprovalHostDecisionRequestSchema
>;

// Renderer result remains deliberately smaller than the trusted receipt. The
// decision ledger id is retained main-side for C2 authorization, and neither a
// permit nor any host-local reference can cross this boundary.
export const WorkspaceApprovalHostDecisionResultSchema = z
  .object({
    stageId: z.string().regex(OPAQUE_ID),
    revision: z.number().int().positive(),
    decision: z.enum(["approve", "reject"]),
    status: z.enum(["approved", "rejected", "cancelled"]),
  })
  .strict();
export type WorkspaceApprovalHostDecisionResult = z.infer<
  typeof WorkspaceApprovalHostDecisionResultSchema
>;
