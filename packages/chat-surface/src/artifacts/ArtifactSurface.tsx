import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";
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
  ArtifactRevisionReview,
  REVIEWED_ARTIFACT_AUTHORS,
  type ArtifactRevisionReviewState,
} from "./ArtifactRevisionReview";
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

/**
 * SHA-256 of the bytes about to be uploaded, as the server expects.
 *
 * Returns an empty object when `crypto.subtle` is unavailable (a non-secure
 * context) rather than sending something wrong: the field is optional, and an
 * absent integrity check is honest where an incorrect one rejects every write.
 */
async function contentDigest(
  content: Uint8Array,
): Promise<{ expectedDigest?: string }> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) return {};
  try {
    const digest = await subtle.digest(
      "SHA-256",
      new Uint8Array(content).buffer,
    );
    return {
      expectedDigest: Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join(""),
    };
  } catch {
    return {};
  }
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
  const [review, setReview] = useState<ArtifactRevisionReviewState | null>(
    null,
  );
  const [reviewComparison, setReviewComparison] =
    useState<ArtifactTextComparison | null>(null);
  const [reviewStatus, setReviewStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  /** The revision last shown, so an arrival can be told from a first paint. */
  const shownRevision = useRef<number | null>(null);
  /** Set when the reader themselves moved — a move is never an arrival. */
  const navigated = useRef(false);
  /** The `base:revision` pair whose comparison is in flight or settled. */
  const reviewFetch = useRef<string | null>(null);
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
          // The digest of the bytes BEING UPLOADED, never the parent's.
          // The server hashes the incoming stream and compares, so sending the
          // parent digest made every real edit fail its own integrity check —
          // a 422 that was invisible while the sealed-run 409 fired first.
          // Optimistic concurrency is carried by `parentRevision` and the
          // If-Match etag; this field is only a transit-integrity check, so it
          // is omitted rather than faked when the platform cannot compute it.
          ...(await contentDigest(content)),
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
    navigated.current = true;
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
  // Bounded fetch-and-diff of one revision against the current head. Two callers
  // share it — the reader's explicit "Compare to current" and the automatic
  // review of an agent revision — so both read the same bytes through the same
  // per-kind limits. `null` means "not comparable as bounded UTF-8 text".
  const buildComparison = useCallback(
    async (targetRevision: number): Promise<ArtifactTextComparison | null> => {
      if (
        parsed === null ||
        data.detail === null ||
        data.latestRevision === null ||
        !isArtifactTransport(props.transport)
      ) {
        return null;
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
        return null;
      }
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
        if (targetText === null || currentText === null) return null;
        return compareArtifactText(
          targetText,
          currentText,
          target.revision,
          current.revision,
        );
      } catch {
        return null;
      }
    },
    [data.detail, data.latestRevision, data.revisions, parsed, props.transport],
  );
  const compareToCurrent = useCallback(
    async (targetRevision: number): Promise<void> => {
      setCompareStatus("loading");
      const next = await buildComparison(targetRevision);
      setComparison(next);
      setCompareStatus(next === null ? "error" : "ready");
    },
    [buildComparison],
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
  // A revision the reader did not make can land on top of the one they are
  // reading — the model revising an artifact mid-run. Announce it as a diff
  // instead of swapping the bytes underneath them (PRD-03 D1). This is not a
  // gate: the revision is already current, and nothing here blocks the write.
  //
  // Only the head qualifies. A revision that is not current has no honest
  // r(n-1)→r(n) reading, and the comparison below is always against the head.
  useEffect(() => {
    const shown = data.detail?.current_revision;
    if (shown === undefined) return;
    const previous = shownRevision.current;
    if (previous === shown.revision) return;
    shownRevision.current = shown.revision;
    const deliberate = navigated.current;
    navigated.current = false;
    setReviewComparison(null);
    reviewFetch.current = null;
    if (
      previous === null ||
      deliberate ||
      shown.revision !== data.latestRevision ||
      shown.parent_revision !== previous ||
      !REVIEWED_ARTIFACT_AUTHORS.has(shown.author)
    ) {
      // Includes the reader's own revision — a user edit, or the revert
      // appended just below — which needs no review of its own.
      setReview(null);
      setReviewStatus("idle");
      return;
    }
    setReview({
      baseRevision: previous,
      revision: shown.revision,
      author: shown.author,
    });
    setReviewStatus("loading");
  }, [data.detail, data.latestRevision]);
  // Deferred until both revisions are in the loaded history, because the
  // comparison reads their byte sizes to stay inside the per-kind bound. The
  // pair ref keeps one comparison in flight and drops a stale resolution.
  useEffect(() => {
    if (review === null || reviewStatus !== "loading") return;
    const loaded = (target: number): boolean =>
      data.revisions.some((item) => item.revision === target);
    if (!loaded(review.baseRevision) || !loaded(review.revision)) return;
    const pair = `${review.baseRevision}:${review.revision}`;
    if (reviewFetch.current === pair) return;
    reviewFetch.current = pair;
    void buildComparison(review.baseRevision).then((next) => {
      if (reviewFetch.current !== pair) return;
      setReviewComparison(next);
      setReviewStatus(next === null ? "error" : "ready");
    });
  }, [buildComparison, data.revisions, review, reviewStatus]);
  const keepRevision = useCallback((): void => {
    // Dismiss only. The reviewed revision is already current, so keeping it
    // appends nothing.
    setReview(null);
    setReviewComparison(null);
    setReviewStatus("idle");
    reviewFetch.current = null;
  }, []);
  const revertRevision = useCallback((): void => {
    if (review === null) return;
    // Appends a copy of the parent through the same bounded restore the history
    // uses; the reviewed revision stays in history. The review closes on its
    // own once that appended revision becomes the one on screen.
    void restore(review.baseRevision);
  }, [restore, review]);
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
      <ArtifactRevisionReview
        review={review}
        comparison={reviewComparison}
        status={reviewStatus}
        onKeep={keepRevision}
        onRevert={revertRevision}
        revertDisabled={restoreStatus === "restoring"}
      />
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
