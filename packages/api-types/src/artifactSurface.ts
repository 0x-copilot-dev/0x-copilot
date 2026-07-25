/** Fixed-renderer artifact snapshot (B2). It carries inert display data only. */
import type { ArtifactAuthor, ArtifactKind } from "./ledger";

export type ArtifactPreviewState =
  | "loading"
  | "ready"
  | "too_large"
  | "binary"
  | "error"
  | "deleted";

export interface ArtifactRenderState {
  readonly artifactId: string;
  readonly kind: ArtifactKind;
  readonly title: string;
  readonly mediaType: string;
  readonly filename: string;
  readonly revision: number;
  readonly digest: string;
  readonly byteSize: number;
  readonly author: ArtifactAuthor;
  readonly createdAt: string;
  readonly preview: ArtifactPreviewState;
  /** Text is UTF-8 validated before it reaches any renderer. */
  readonly text?: string;
  /** Safe client-owned status; never raw backend/model error text. */
  readonly notice?: string;
}

const KINDS = new Set<ArtifactKind>(["code", "document", "dataset", "file"]);
const PREVIEW_STATES = new Set<ArtifactPreviewState>([
  "loading",
  "ready",
  "too_large",
  "binary",
  "error",
  "deleted",
]);

export function isArtifactRenderState(
  value: unknown,
): value is ArtifactRenderState {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const item = value as Record<string, unknown>;
  return (
    typeof item.artifactId === "string" &&
    KINDS.has(item.kind as ArtifactKind) &&
    typeof item.title === "string" &&
    typeof item.mediaType === "string" &&
    typeof item.filename === "string" &&
    Number.isInteger(item.revision) &&
    typeof item.digest === "string" &&
    Number.isSafeInteger(item.byteSize) &&
    typeof item.author === "string" &&
    typeof item.createdAt === "string" &&
    PREVIEW_STATES.has(item.preview as ArtifactPreviewState) &&
    (item.text === undefined || typeof item.text === "string") &&
    (item.notice === undefined || typeof item.notice === "string")
  );
}
