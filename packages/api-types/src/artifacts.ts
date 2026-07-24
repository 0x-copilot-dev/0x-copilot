/** Public Artifact Repository HTTP contracts (PRD-A2).
 *
 * A1's `Artifact` and `ArtifactRevision` remain canonical. This module adds
 * only request fields, response envelopes, and defensive public-API guards.
 */

import {
  ArtifactContentRefCodec,
  ArtifactIdCodec,
  type Artifact,
  type ArtifactAuthor,
  type ArtifactKind,
  type ArtifactRevision,
} from "./ledger";

export interface ArtifactCreateMultipartFields {
  readonly kind: ArtifactKind;
  readonly title: string;
  readonly media_type: string;
  readonly suggested_filename?: string;
  readonly expected_digest?: string;
}

export interface ArtifactRevisionMultipartFields {
  readonly parent_revision: number;
  readonly expected_digest?: string;
}

export interface ArtifactPromotionRequest {
  readonly run_id: string;
  readonly source_ref: string;
  readonly kind: ArtifactKind;
  readonly title?: string;
  readonly media_type?: string;
  readonly suggested_filename?: string;
}

export interface ArtifactRevisionResponse {
  readonly revision: ArtifactRevision;
  readonly range_supported: boolean;
}

export interface ArtifactDetailResponse {
  readonly artifact: Artifact;
  readonly current_revision: ArtifactRevision;
  readonly suggested_filename?: string;
  readonly range_supported: boolean;
}

export interface ArtifactMutationResponse extends ArtifactDetailResponse {
  readonly replayed: boolean;
}

export interface ArtifactListResponse {
  readonly artifacts: readonly ArtifactDetailResponse[];
  readonly next_cursor?: string;
}

const ARTIFACT_KINDS = new Set<ArtifactKind>([
  "code",
  "document",
  "dataset",
  "file",
]);
const ARTIFACT_AUTHORS = new Set<ArtifactAuthor>([
  "model",
  "subagent",
  "user",
  "system",
  "import",
]);
const SHA256 = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isOptionalString(value: unknown): value is string | undefined {
  return value === undefined || typeof value === "string";
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0;
}

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function hasArtifactId(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    ArtifactIdCodec.parse(value);
    return true;
  } catch {
    return false;
  }
}

function isArtifact(value: unknown): value is Artifact {
  if (!isRecord(value) || !hasArtifactId(value.artifact_id)) return false;
  if (
    !hasOnlyKeys(
      value,
      new Set([
        "artifact_id",
        "org_id",
        "user_id",
        "conversation_id",
        "run_id",
        "kind",
        "title",
        "media_type",
        "current_revision",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
      ]),
    )
  ) {
    return false;
  }
  return (
    isNonEmptyString(value.org_id) &&
    isNonEmptyString(value.user_id) &&
    isNonEmptyString(value.conversation_id) &&
    isNonEmptyString(value.run_id) &&
    ARTIFACT_KINDS.has(value.kind as ArtifactKind) &&
    isNonEmptyString(value.title) &&
    isNonEmptyString(value.media_type) &&
    isPositiveInteger(value.current_revision) &&
    ARTIFACT_AUTHORS.has(value.created_by as ArtifactAuthor) &&
    isNonEmptyString(value.created_at) &&
    isNonEmptyString(value.updated_at) &&
    isOptionalString(value.deleted_at)
  );
}

function isArtifactRevision(value: unknown): value is ArtifactRevision {
  if (
    !isRecord(value) ||
    !hasArtifactId(value.artifact_id) ||
    !isPositiveInteger(value.revision) ||
    !isNonEmptyString(value.content_ref)
  ) {
    return false;
  }
  if (
    !hasOnlyKeys(
      value,
      new Set([
        "artifact_id",
        "revision",
        "parent_revision",
        "content_ref",
        "content_digest",
        "byte_size",
        "author",
        "source_ref",
        "created_at",
      ]),
    )
  ) {
    return false;
  }
  try {
    const parsed = ArtifactContentRefCodec.parse(value.content_ref);
    if (
      parsed.artifact_id !== value.artifact_id ||
      parsed.revision !== value.revision
    ) {
      return false;
    }
  } catch {
    return false;
  }
  return (
    (value.parent_revision === undefined ||
      (isPositiveInteger(value.parent_revision) &&
        value.parent_revision < value.revision)) &&
    typeof value.content_digest === "string" &&
    SHA256.test(value.content_digest) &&
    Number.isInteger(value.byte_size) &&
    Number(value.byte_size) >= 0 &&
    ARTIFACT_AUTHORS.has(value.author as ArtifactAuthor) &&
    isOptionalString(value.source_ref) &&
    isNonEmptyString(value.created_at)
  );
}

export function isArtifactRevisionResponse(
  value: unknown,
): value is ArtifactRevisionResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, new Set(["revision", "range_supported"])) &&
    isArtifactRevision(value.revision) &&
    typeof value.range_supported === "boolean"
  );
}

export function isArtifactDetailResponse(
  value: unknown,
): value is ArtifactDetailResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(
      value,
      new Set([
        "artifact",
        "current_revision",
        "suggested_filename",
        "range_supported",
      ]),
    ) &&
    isArtifact(value.artifact) &&
    isArtifactRevision(value.current_revision) &&
    value.current_revision.artifact_id === value.artifact.artifact_id &&
    value.current_revision.revision === value.artifact.current_revision &&
    isOptionalString(value.suggested_filename) &&
    typeof value.range_supported === "boolean"
  );
}

export function isArtifactMutationResponse(
  value: unknown,
): value is ArtifactMutationResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(
      value,
      new Set([
        "artifact",
        "current_revision",
        "suggested_filename",
        "range_supported",
        "replayed",
      ]),
    ) &&
    isArtifact(value.artifact) &&
    isArtifactRevision(value.current_revision) &&
    value.current_revision.artifact_id === value.artifact.artifact_id &&
    value.current_revision.revision === value.artifact.current_revision &&
    isOptionalString(value.suggested_filename) &&
    typeof value.range_supported === "boolean" &&
    typeof value.replayed === "boolean"
  );
}

export function isArtifactListResponse(
  value: unknown,
): value is ArtifactListResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, new Set(["artifacts", "next_cursor"])) &&
    Array.isArray(value.artifacts) &&
    value.artifacts.every(isArtifactDetailResponse) &&
    isOptionalString(value.next_cursor)
  );
}
