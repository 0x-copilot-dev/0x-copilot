import type { Transport } from "./transport";

/** Exact source selected by a user for B1 artifact promotion. */
export interface PromoteArtifactRequest {
  readonly runId: string;
  readonly sourceRef: string;
  readonly kind: "code" | "document" | "dataset" | "file";
  readonly title?: string;
  readonly mediaType?: string;
  readonly suggestedFilename?: string;
  /** Caller-generated retry key; it is never derived from source bytes. */
  readonly idempotencyKey: string;
}

/**
 * Call the one A2 promotion API through the substrate transport port.
 *
 * The browser/desktop sends only an approved logical reference and metadata.
 * It never reads a host path or supplies body bytes, so source authorization
 * and exact-byte resolution remain server-side.
 */
export async function promoteArtifact<TResponse>(
  transport: Transport,
  request: PromoteArtifactRequest,
): Promise<TResponse> {
  return transport.request<TResponse>({
    method: "POST",
    path: "/v1/agent/artifacts:promote",
    headers: { "Idempotency-Key": request.idempotencyKey },
    body: {
      run_id: request.runId,
      source_ref: request.sourceRef,
      kind: request.kind,
      ...(request.title === undefined ? {} : { title: request.title }),
      ...(request.mediaType === undefined
        ? {}
        : { media_type: request.mediaType }),
      ...(request.suggestedFilename === undefined
        ? {}
        : { suggested_filename: request.suggestedFilename }),
    },
  });
}
