import { useCallback, useState, type ReactElement } from "react";
import { isArtifactMutationResponse } from "@0x-copilot/api-types";
import {
  isArtifactTransport,
  isTransportHttpError,
  type Transport,
} from "@0x-copilot/chat-transport";
import { TcSurfaceMount } from "../thread-canvas/TcSurfaceMount";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";
import { ArtifactEditor } from "./ArtifactEditor";
import { ArtifactFrame } from "./ArtifactFrame";
import { ArtifactRevisionHistory } from "./ArtifactRevisionHistory";
import { parseArtifactSurfaceUri, artifactUri } from "./uri";
import { useArtifactSurface } from "./useArtifactSurface";

function idempotencyKey(): string {
  return typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `artifact-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function ArtifactSurface(props: {
  readonly uri: string;
  readonly transport: Transport;
  readonly downloadPort?: ArtifactDownloadPort;
  readonly onNavigateRevision?: (uri: string) => void;
}): ReactElement {
  const parsed = parseArtifactSurfaceUri(props.uri);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const artifactId = parsed?.artifactId ?? "";
  const kind = parsed?.kind ?? "file";
  const revision = selectedRevision ?? parsed?.revision ?? 1;
  const data = useArtifactSurface(
    props.transport,
    artifactId,
    revision,
    parsed !== null,
  );
  const save = useCallback(
    async (text: string): Promise<"saved" | "conflict" | "error"> => {
      if (
        parsed === null ||
        data.detail === null ||
        data.state === null ||
        !isArtifactTransport(props.transport)
      )
        return "error";
      try {
        const response = await props.transport.createArtifactRevision({
          artifactId: data.detail.artifact.artifact_id,
          parentRevision: data.detail.current_revision.revision,
          expectedDigest: data.detail.current_revision.content_digest,
          ...(data.etag !== null ? { etag: data.etag } : {}),
          content: new TextEncoder().encode(text),
          contentType: data.detail.artifact.media_type,
          filename: data.detail.suggested_filename ?? data.state.filename,
          idempotencyKey: idempotencyKey(),
        });
        if (!isArtifactMutationResponse(response)) return "error";
        setSelectedRevision(response.current_revision.revision);
        data.reload();
        props.onNavigateRevision?.(
          artifactUri(
            parsed.kind,
            parsed.artifactId,
            response.current_revision.revision,
          ),
        );
        return "saved";
      } catch (error) {
        return isTransportHttpError(error) && error.status === 409
          ? "conflict"
          : "error";
      }
    },
    [data, props, parsed],
  );
  if (parsed === null)
    return (
      <section className="ui-card" role="status">
        Invalid artifact reference.
      </section>
    );
  const selectRevision = (next: number): void => {
    setSelectedRevision(next);
    props.onNavigateRevision?.(artifactUri(kind, artifactId, next));
  };
  const editable =
    data.state?.preview === "ready" &&
    data.state.text !== undefined &&
    parsed.kind !== "file";
  return (
    <ArtifactFrame
      artifact={data.state}
      status={data.status}
      transport={props.transport}
      downloadPort={props.downloadPort}
    >
      {data.state !== null ? (
        <TcSurfaceMount
          uri={artifactUri(
            data.state.kind,
            data.state.artifactId,
            data.state.revision,
          )}
          transport={props.transport}
          state={data.state}
        />
      ) : null}
      {editable && data.state !== null ? (
        <ArtifactEditor
          initialText={data.state.text!}
          revision={data.state.revision}
          disabled={!isArtifactTransport(props.transport)}
          onSave={save}
        />
      ) : null}
      <ArtifactRevisionHistory
        revisions={data.revisions}
        activeRevision={revision}
        onSelect={selectRevision}
        hasOlderHistory={data.hasOlderHistory}
        onLoadOlder={data.loadOlderHistory}
      />
    </ArtifactFrame>
  );
}
