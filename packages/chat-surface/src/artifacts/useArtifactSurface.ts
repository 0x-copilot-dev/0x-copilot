import { useCallback, useEffect, useState } from "react";
import {
  isArtifactDetailResponse,
  isArtifactRevisionResponse,
  type ArtifactDetailResponse,
  type ArtifactRenderState,
  type ArtifactRevision,
} from "@0x-copilot/api-types";
import {
  isArtifactTransport,
  isTransportHttpError,
  type Transport,
} from "@0x-copilot/chat-transport";

const CODE_DOCUMENT_LIMIT = 5 * 1024 * 1024;
const DATASET_LIMIT = 10 * 1024 * 1024;
const HISTORY_PAGE_SIZE = 25;

export interface ArtifactSurfaceData {
  readonly state: ArtifactRenderState | null;
  readonly detail: ArtifactDetailResponse | null;
  /** Authoritative latest revision from artifact metadata, even when viewing history. */
  readonly latestRevision: number | null;
  readonly etag: string | null;
  readonly status: "loading" | "ready" | "error" | "deleted";
  readonly revisions: readonly ArtifactRevision[];
  readonly hasOlderHistory: boolean;
  readonly loadOlderHistory: () => void;
  readonly reload: () => void;
}

export function useArtifactSurface(
  transport: Transport,
  artifactId: string,
  wantedRevision: number,
  enabled: boolean,
): ArtifactSurfaceData {
  const [state, setState] = useState<ArtifactRenderState | null>(null);
  const [detail, setDetail] = useState<ArtifactDetailResponse | null>(null);
  const [latestRevision, setLatestRevision] = useState<number | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [status, setStatus] =
    useState<ArtifactSurfaceData["status"]>("loading");
  const [revisions, setRevisions] = useState<readonly ArtifactRevision[]>([]);
  const [nonce, setNonce] = useState(0);
  const [historyDepth, setHistoryDepth] = useState(HISTORY_PAGE_SIZE);
  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const loadOlderHistory = useCallback(
    () => setHistoryDepth((value) => value + HISTORY_PAGE_SIZE),
    [],
  );

  useEffect(() => setHistoryDepth(HISTORY_PAGE_SIZE), [artifactId]);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const abort = new AbortController();
    setStatus("loading");
    setState(null);
    setDetail(null);
    setLatestRevision(null);
    setEtag(null);
    void transport
      .request<unknown>({
        method: "GET",
        path: `/v1/agent/artifacts/${encodeURIComponent(artifactId)}`,
        signal: abort.signal,
      })
      .then(async (response) => {
        if (!isArtifactDetailResponse(response))
          throw new Error("Invalid artifact metadata");
        if (!live) return;
        const target =
          response.current_revision.revision === wantedRevision
            ? response.current_revision
            : await revisionFor(
                transport,
                artifactId,
                wantedRevision,
                abort.signal,
              );
        if (!live) return;
        setLatestRevision(response.current_revision.revision);
        setDetail({
          ...response,
          current_revision: target,
          artifact: { ...response.artifact, current_revision: target.revision },
        });
        const base = stateFrom(response, target, "loading");
        if (response.artifact.kind === "file") {
          setState({ ...base, preview: "ready" });
          setStatus("ready");
          return;
        }
        if (!isArtifactTransport(transport)) {
          setState({
            ...base,
            preview: "error",
            notice: "This host cannot stream artifact contents yet.",
          });
          setStatus("error");
          return;
        }
        const limit =
          response.artifact.kind === "dataset"
            ? DATASET_LIMIT
            : CODE_DOCUMENT_LIMIT;
        if (target.byte_size > limit) {
          setState({
            ...base,
            preview: "too_large",
            notice: `Preview is limited to ${formatBytes(limit)}. Download the exact artifact bytes instead.`,
          });
          setStatus("ready");
          return;
        }
        const content = await transport.getArtifactContent({
          artifactId,
          revision: target.revision,
          signal: abort.signal,
        });
        const bytes = await readBounded(content.body, limit, abort.signal);
        const text = decodeUtf8(bytes);
        if (text === null) {
          setState({
            ...base,
            preview: "binary",
            notice:
              "This revision is not valid UTF-8 text. Download the exact artifact bytes instead.",
          });
          setStatus("ready");
          return;
        }
        setEtag(content.etag);
        setState({ ...base, preview: "ready", text });
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!live || abort.signal.aborted) return;
        if (isTransportHttpError(error) && error.status === 404) {
          setStatus("deleted");
          setState(null);
          return;
        }
        setStatus("error");
        setState(null);
      });
    return () => {
      live = false;
      abort.abort();
    };
  }, [transport, artifactId, wantedRevision, enabled, nonce]);

  useEffect(() => {
    if (detail === null || latestRevision === null || !enabled) {
      setRevisions([]);
      return;
    }
    let live = true;
    const abort = new AbortController();
    const current = latestRevision;
    const start = Math.max(1, current - historyDepth + 1);
    void Promise.all(
      Array.from({ length: current - start + 1 }, (_, offset) =>
        revisionFor(
          transport,
          detail.artifact.artifact_id,
          start + offset,
          abort.signal,
        ).catch(() => null),
      ),
    )
      .then((items) => {
        if (live)
          setRevisions(
            items
              .filter((item): item is ArtifactRevision => item !== null)
              .reverse(),
          );
      })
      .catch(() => {});
    return () => {
      live = false;
      abort.abort();
    };
  }, [transport, detail, latestRevision, enabled, historyDepth]);

  return {
    state,
    detail,
    latestRevision,
    etag,
    status,
    revisions,
    hasOlderHistory:
      latestRevision !== null && revisions.length < latestRevision,
    loadOlderHistory,
    reload,
  };
}

async function revisionFor(
  transport: Transport,
  artifactId: string,
  revision: number,
  signal: AbortSignal,
): Promise<ArtifactRevision> {
  const response = await transport.request<unknown>({
    method: "GET",
    path: `/v1/agent/artifacts/${encodeURIComponent(artifactId)}/revisions/${revision}`,
    signal,
  });
  if (!isArtifactRevisionResponse(response))
    throw new Error("Invalid artifact revision");
  return response.revision;
}

function stateFrom(
  detail: ArtifactDetailResponse,
  revision: ArtifactRevision,
  preview: ArtifactRenderState["preview"],
): ArtifactRenderState {
  return {
    artifactId: detail.artifact.artifact_id,
    kind: detail.artifact.kind,
    title: detail.artifact.title,
    mediaType: detail.artifact.media_type,
    filename: detail.suggested_filename ?? `${detail.artifact.title}`,
    revision: revision.revision,
    digest: revision.content_digest,
    byteSize: revision.byte_size,
    author: revision.author,
    createdAt: revision.created_at,
    preview,
  };
}

async function readBounded(
  body: ReadableStream<Uint8Array>,
  maxBytes: number,
  signal: AbortSignal,
): Promise<Uint8Array> {
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        throw new RangeError("Artifact preview exceeds the safe limit");
      }
      chunks.push(next.value);
    }
  } finally {
    reader.releaseLock();
  }
  const joined = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    joined.set(chunk, at);
    at += chunk.byteLength;
  }
  return joined;
}

function decodeUtf8(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}
function formatBytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MiB`;
}
