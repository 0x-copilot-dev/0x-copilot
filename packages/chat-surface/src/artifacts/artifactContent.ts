import type { ArtifactKind } from "@0x-copilot/api-types";

const CODE_DOCUMENT_LIMIT = 5 * 1024 * 1024;
const DATASET_LIMIT = 10 * 1024 * 1024;
const CODE_RESTORE_LIMIT = 10 * 1024 * 1024;
const DOCUMENT_RESTORE_LIMIT = 20 * 1024 * 1024;
const DATASET_RESTORE_LIMIT = 100 * 1024 * 1024;
const FILE_RESTORE_LIMIT = 250 * 1024 * 1024;

export function textPreviewLimit(kind: ArtifactKind): number {
  return kind === "dataset" ? DATASET_LIMIT : CODE_DOCUMENT_LIMIT;
}

/**
 * Matches A2's per-kind revision admission limits. Restore therefore handles
 * every valid revision, while still refusing corrupt metadata before a large
 * browser allocation.
 */
export function revisionRestoreLimit(kind: ArtifactKind): number {
  switch (kind) {
    case "code":
      return CODE_RESTORE_LIMIT;
    case "document":
      return DOCUMENT_RESTORE_LIMIT;
    case "dataset":
      return DATASET_RESTORE_LIMIT;
    case "file":
      return FILE_RESTORE_LIMIT;
  }
}

/** Reads a response stream without ever materializing bytes beyond the caller's limit. */
export async function readBoundedArtifactBytes(
  body: ReadableStream<Uint8Array>,
  maxBytes: number,
  signal?: AbortSignal,
): Promise<Uint8Array> {
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        throw new RangeError("Artifact content exceeds the safe client limit");
      }
      chunks.push(next.value);
    }
  } finally {
    reader.releaseLock();
  }
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return joined;
}

/** Preserves a UTF-8 BOM rather than silently changing canonical source text. */
export function decodeArtifactUtf8(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
      bytes,
    );
  } catch {
    return null;
  }
}
