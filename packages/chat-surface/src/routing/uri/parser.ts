import { isArtifactScheme, type ArtifactScheme } from "./schemes";

export interface ParsedArtifactUri {
  readonly scheme: ArtifactScheme;
  readonly body: string;
}

const DELIMITER = "://";

export function parseArtifactUri(raw: string): ParsedArtifactUri | null {
  if (typeof raw !== "string" || raw.length === 0) {
    return null;
  }
  const idx = raw.indexOf(DELIMITER);
  if (idx <= 0) {
    return null;
  }
  const scheme = raw.slice(0, idx);
  const body = raw.slice(idx + DELIMITER.length);
  if (body.length === 0) {
    return null;
  }
  if (!isArtifactScheme(scheme)) {
    return null;
  }
  return { scheme, body };
}

export function buildArtifactUri(parts: ParsedArtifactUri): string {
  if (!isArtifactScheme(parts.scheme)) {
    throw new Error(`buildArtifactUri: unknown scheme "${parts.scheme}"`);
  }
  if (parts.body.length === 0) {
    throw new Error("buildArtifactUri: body must be non-empty");
  }
  return `${parts.scheme}${DELIMITER}${parts.body}`;
}
