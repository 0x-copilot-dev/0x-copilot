import { useCallback, useState, type ReactElement } from "react";
import {
  isArtifactMutationResponse,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import {
  isArtifactTransport,
  isTransportHttpError,
  type Transport,
} from "@0x-copilot/chat-transport";
import { TcSurfaceMount } from "../thread-canvas/TcSurfaceMount";
import type { SurfaceHue } from "../surfaces/surfaceHue";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";
import { ArtifactEditor } from "./ArtifactEditor";
import { ArtifactFrame } from "./ArtifactFrame";
import {
  ArtifactRevisionCompare,
  compareArtifactText,
  type ArtifactTextComparison,
} from "./ArtifactRevisionCompare";
import { ArtifactRevisionHistory } from "./ArtifactRevisionHistory";
import {
  decodeArtifactUtf8,
  readBoundedArtifactBytes,
  revisionRestoreLimit,
  textPreviewLimit,
} from "./artifactContent";
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
  /**
   * The artifact's chosen identity hue — `publish_artifact`'s `accent`, as the
   * conversation-canvas record carries it. The caller holds that record (this
   * surface fetches content and revisions, not the canvas), so the choice is
   * passed in rather than re-fetched here, and it is the SAME value the caller
   * gives the artifact's tab. Omitted means no choice was made: the hue is then
   * derived from the artifact URI's scheme, which is what every artifact
   * rendered before this prop existed.
   *
   * Typed as the closed hue set, not `string`. Untrusted values are narrowed
   * with `isSurfaceHue` where they enter — `useConversationCanvas` does exactly
   * that when parsing the record — so every real caller already holds a
   * `SurfaceHue`, and `string` here only discarded a check they could satisfy.
   * The runtime fallback in `resolveSurfaceHue` stays regardless: this prop is
   * a compile-time guarantee, and the renderer must remain total over a value
   * that reached it without one.
   */
  readonly hue?: SurfaceHue;
  readonly downloadPort?: ArtifactDownloadPort;
  readonly onNavigateRevision?: (uri: string) => void;
}): ReactElement {
  const parsed = parseArtifactSurfaceUri(props.uri);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [comparison, setComparison] = useState<ArtifactTextComparison | null>(
    null,
  );
  const [compareStatus, setCompareStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [restoreStatus, setRestoreStatus] = useState<
    "idle" | "restoring" | "conflict" | "error" | "too_large"
  >("idle");
  const artifactId = parsed?.artifactId ?? "";
  const kind = parsed?.kind ?? "file";
  const revision = selectedRevision ?? parsed?.revision ?? 1;
  const data = useArtifactSurface(
    props.transport,
    artifactId,
    revision,
    parsed !== null,
  );
  const appendRevision = useCallback(
    async (
      content: Uint8Array,
      parent: ArtifactRevision,
      etag?: string,
    ): Promise<"saved" | "conflict" | "error"> => {
      if (
        parsed === null ||
        data.detail === null ||
        !isArtifactTransport(props.transport)
      )
        return "error";
      try {
        const response = await props.transport.createArtifactRevision({
          artifactId: data.detail.artifact.artifact_id,
          parentRevision: parent.revision,
          expectedDigest: parent.content_digest,
          ...(etag !== undefined ? { etag } : {}),
          content,
          contentType: data.detail.artifact.media_type,
          filename:
            data.detail.suggested_filename ?? data.detail.artifact.title,
          idempotencyKey: idempotencyKey(),
          // Deliberately claims no run. This surface only ever produces
          // user-authored revisions, which the server attributes to the
          // conversation — a subject that never seals. Naming a run here is what
          // made saving a cell edit fail once the viewed run had finished.
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
        // Only report a lost update when the server actually said the parent is
        // stale. Three unrelated causes share 409, and treating them all as
        // staleness is how the surface came to claim "a newer revision exists"
        // when the artifact had exactly one revision. A server that sent no code
        // keeps the prior behaviour; anything else falls through to a plain
        // failure, which is honest rather than confidently wrong.
        if (!isTransportHttpError(error) || error.status !== 409)
          return "error";
        return error.code === null || error.code === "artifact_conflict"
          ? "conflict"
          : "error";
      }
    },
    [data, props, parsed],
  );
  const save = useCallback(
    async (text: string): Promise<"saved" | "conflict" | "error"> => {
      if (data.detail === null) return "error";
      return appendRevision(
        new TextEncoder().encode(text),
        data.detail.current_revision,
        data.etag ?? undefined,
      );
    },
    [appendRevision, data.detail, data.etag],
  );
  const selectRevision = (next: number): void => {
    setComparison(null);
    setCompareStatus("idle");
    setSelectedRevision(next);
    props.onNavigateRevision?.(artifactUri(kind, artifactId, next));
  };
  const editable =
    data.state?.preview === "ready" &&
    data.state.text !== undefined &&
    kind !== "file" &&
    kind !== "dataset";
  const mountedState =
    data.state?.kind === "dataset"
      ? {
          ...data.state,
          datasetEditor: {
            disabled: !isArtifactTransport(props.transport),
            saveRevision: save,
          },
        }
      : data.state;
  const compareToCurrent = useCallback(
    async (targetRevision: number): Promise<void> => {
      if (
        parsed === null ||
        data.detail === null ||
        data.latestRevision === null ||
        !isArtifactTransport(props.transport)
      ) {
        setCompareStatus("error");
        return;
      }
      const current = data.revisions.find(
        (item) => item.revision === data.latestRevision,
      );
      const target = data.revisions.find(
        (item) => item.revision === targetRevision,
      );
      const limit = textPreviewLimit(parsed.kind);
      if (
        current === undefined ||
        target === undefined ||
        current.byte_size > limit ||
        target.byte_size > limit
      ) {
        setComparison(null);
        setCompareStatus("error");
        return;
      }
      setCompareStatus("loading");
      try {
        const [targetContent, currentContent] = await Promise.all([
          props.transport.getArtifactContent({
            artifactId: data.detail.artifact.artifact_id,
            revision: target.revision,
          }),
          props.transport.getArtifactContent({
            artifactId: data.detail.artifact.artifact_id,
            revision: current.revision,
          }),
        ]);
        const [targetBytes, currentBytes] = await Promise.all([
          readBoundedArtifactBytes(targetContent.body, limit),
          readBoundedArtifactBytes(currentContent.body, limit),
        ]);
        const targetText = decodeArtifactUtf8(targetBytes);
        const currentText = decodeArtifactUtf8(currentBytes);
        if (targetText === null || currentText === null) throw new Error();
        setComparison(
          compareArtifactText(
            targetText,
            currentText,
            target.revision,
            current.revision,
          ),
        );
        setCompareStatus("ready");
      } catch {
        setComparison(null);
        setCompareStatus("error");
      }
    },
    [data, parsed, props.transport],
  );
  const restore = useCallback(
    async (targetRevision: number): Promise<void> => {
      if (
        parsed === null ||
        data.detail === null ||
        data.latestRevision === null ||
        !isArtifactTransport(props.transport)
      ) {
        setRestoreStatus("error");
        return;
      }
      const current = data.revisions.find(
        (item) => item.revision === data.latestRevision,
      );
      const target = data.revisions.find(
        (item) => item.revision === targetRevision,
      );
      if (current === undefined || target === undefined) {
        setRestoreStatus("error");
        return;
      }
      const maxBytes = revisionRestoreLimit(parsed.kind);
      if (target.byte_size > maxBytes) {
        setRestoreStatus("too_large");
        return;
      }
      setRestoreStatus("restoring");
      try {
        const content = await props.transport.getArtifactContent({
          artifactId: data.detail.artifact.artifact_id,
          revision: target.revision,
        });
        const bytes = await readBoundedArtifactBytes(content.body, maxBytes);
        const outcome = await appendRevision(bytes, current);
        setRestoreStatus(outcome === "saved" ? "idle" : outcome);
      } catch {
        setRestoreStatus("error");
      }
    },
    [appendRevision, data, parsed, props.transport],
  );
  if (parsed === null)
    return (
      <section className="ui-card" role="status">
        Invalid artifact reference.
      </section>
    );
  return (
    <ArtifactFrame
      artifact={data.state}
      status={data.status}
      transport={props.transport}
      downloadPort={props.downloadPort}
    >
      {mountedState !== null ? (
        <TcSurfaceMount
          uri={artifactUri(
            mountedState.kind,
            mountedState.artifactId,
            mountedState.revision,
          )}
          // Forwarded unresolved. The mount resolves it through the same helper
          // the tab strip uses, so an artifact's card and its tab cannot end up
          // claiming different sources.
          hue={props.hue}
          transport={props.transport}
          state={mountedState}
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
      <ArtifactRevisionCompare
        comparison={comparison}
        status={compareStatus}
        onClose={() => {
          setComparison(null);
          setCompareStatus("idle");
        }}
      />
      <ArtifactRevisionHistory
        revisions={data.revisions}
        activeRevision={revision}
        onSelect={selectRevision}
        latestRevision={data.latestRevision}
        onCompareToCurrent={compareToCurrent}
        onRestore={restore}
        restoreDisabled={restoreStatus === "restoring"}
        hasOlderHistory={data.hasOlderHistory}
        onLoadOlder={data.loadOlderHistory}
      />
      {restoreStatus === "conflict" ? (
        <p className="ui-caption" role="alert">
          A newer revision exists. Restore did not overwrite it; compare and try
          again from the latest revision.
        </p>
      ) : null}
      {restoreStatus === "too_large" ? (
        <p className="ui-caption" role="alert">
          This revision is too large for a bounded in-browser restore. Download
          the exact bytes instead.
        </p>
      ) : null}
      {restoreStatus === "error" ? (
        <p className="ui-caption" role="alert">
          This revision could not be restored. No artifact history was changed.
        </p>
      ) : null}
    </ArtifactFrame>
  );
}
