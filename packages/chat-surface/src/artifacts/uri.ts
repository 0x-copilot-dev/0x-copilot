import type { ArtifactKind } from "@0x-copilot/api-types";

const PREFIX: Record<ArtifactKind, string> = {
  code: "artifact-code",
  document: "artifact-document",
  dataset: "artifact-dataset",
  file: "artifact-file",
};
const URI =
  /^(artifact-code|artifact-document|artifact-dataset|artifact-file):\/\/([^@/]+)@([1-9][0-9]*)$/;

export function artifactUri(
  kind: ArtifactKind,
  artifactId: string,
  revision: number,
): string {
  return `${PREFIX[kind]}://${artifactId}@${revision}`;
}

export function parseArtifactSurfaceUri(uri: string): {
  readonly kind: ArtifactKind;
  readonly artifactId: string;
  readonly revision: number;
} | null {
  const match = URI.exec(uri);
  if (match === null) return null;
  const kind = match[1]!.slice("artifact-".length) as ArtifactKind;
  const revision = Number(match[3]);
  return Number.isSafeInteger(revision)
    ? { kind, artifactId: match[2]!, revision }
    : null;
}
