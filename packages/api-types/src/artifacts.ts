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
import { wireKeys } from "./wireKeys";

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

const ARTIFACT_KEYS = wireKeys<Artifact>({
  artifact_id: true,
  org_id: true,
  user_id: true,
  conversation_id: true,
  run_id: true,
  kind: true,
  title: true,
  media_type: true,
  current_revision: true,
  created_by: true,
  accent: true,
  created_at: true,
  updated_at: true,
  deleted_at: true,
});

function isArtifact(value: unknown): value is Artifact {
  if (!isRecord(value) || !hasArtifactId(value.artifact_id)) return false;
  if (!hasOnlyKeys(value, ARTIFACT_KEYS)) {
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
    // Shape only, deliberately not pinned to the `SurfaceAccent` union: an
    // unknown hue name degrades to the `kind`-derived default, whereas a closed
    // value set here would reject the whole artifact the first time the server
    // ships a new hue — the same failure this guard's key set just stopped
    // having.
    isOptionalString(value.accent) &&
    isNonEmptyString(value.created_at) &&
    isNonEmptyString(value.updated_at) &&
    isOptionalString(value.deleted_at)
  );
}

const ARTIFACT_REVISION_KEYS = wireKeys<ArtifactRevision>({
  artifact_id: true,
  revision: true,
  parent_revision: true,
  content_ref: true,
  content_digest: true,
  byte_size: true,
  author: true,
  source_ref: true,
  created_at: true,
});

function isArtifactRevision(value: unknown): value is ArtifactRevision {
  if (
    !isRecord(value) ||
    !hasArtifactId(value.artifact_id) ||
    !isPositiveInteger(value.revision) ||
    !isNonEmptyString(value.content_ref)
  ) {
    return false;
  }
  if (!hasOnlyKeys(value, ARTIFACT_REVISION_KEYS)) {
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

const ARTIFACT_REVISION_RESPONSE_KEYS = wireKeys<ArtifactRevisionResponse>({
  revision: true,
  range_supported: true,
});

export function isArtifactRevisionResponse(
  value: unknown,
): value is ArtifactRevisionResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ARTIFACT_REVISION_RESPONSE_KEYS) &&
    isArtifactRevision(value.revision) &&
    typeof value.range_supported === "boolean"
  );
}

const ARTIFACT_DETAIL_RESPONSE_KEYS = wireKeys<ArtifactDetailResponse>({
  artifact: true,
  current_revision: true,
  suggested_filename: true,
  range_supported: true,
});

export function isArtifactDetailResponse(
  value: unknown,
): value is ArtifactDetailResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ARTIFACT_DETAIL_RESPONSE_KEYS) &&
    isArtifact(value.artifact) &&
    isArtifactRevision(value.current_revision) &&
    value.current_revision.artifact_id === value.artifact.artifact_id &&
    value.current_revision.revision === value.artifact.current_revision &&
    isOptionalString(value.suggested_filename) &&
    typeof value.range_supported === "boolean"
  );
}

const ARTIFACT_MUTATION_RESPONSE_KEYS = wireKeys<ArtifactMutationResponse>({
  artifact: true,
  current_revision: true,
  suggested_filename: true,
  range_supported: true,
  replayed: true,
});

export function isArtifactMutationResponse(
  value: unknown,
): value is ArtifactMutationResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ARTIFACT_MUTATION_RESPONSE_KEYS) &&
    isArtifact(value.artifact) &&
    isArtifactRevision(value.current_revision) &&
    value.current_revision.artifact_id === value.artifact.artifact_id &&
    value.current_revision.revision === value.artifact.current_revision &&
    isOptionalString(value.suggested_filename) &&
    typeof value.range_supported === "boolean" &&
    typeof value.replayed === "boolean"
  );
}

const ARTIFACT_LIST_RESPONSE_KEYS = wireKeys<ArtifactListResponse>({
  artifacts: true,
  next_cursor: true,
});

export function isArtifactListResponse(
  value: unknown,
): value is ArtifactListResponse {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ARTIFACT_LIST_RESPONSE_KEYS) &&
    Array.isArray(value.artifacts) &&
    value.artifacts.every(isArtifactDetailResponse) &&
    isOptionalString(value.next_cursor)
  );
}
