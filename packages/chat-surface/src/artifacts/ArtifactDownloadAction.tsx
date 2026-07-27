import { useState, type ReactElement } from "react";
import {
  isArtifactTransport,
  type Transport,
} from "@0x-copilot/chat-transport";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";

export function ArtifactDownloadAction(props: {
  readonly transport: Transport;
  readonly artifactId: string;
  readonly revision: number;
  readonly filename: string;
  readonly port?: ArtifactDownloadPort;
}): ReactElement {
  const [status, setStatus] = useState<"idle" | "downloading" | "error">(
    "idle",
  );
  const available =
    props.port !== undefined && isArtifactTransport(props.transport);
  const download = (): void => {
    if (!available || props.port === undefined) return;
    setStatus("downloading");
    void props.transport
      .getArtifactContent({
        artifactId: props.artifactId,
        revision: props.revision,
      })
      .then((content) =>
        props.port!.saveArtifact({
          filename: content.filename ?? props.filename,
          contentType: content.contentType,
          body: content.body,
        }),
      )
      .then(() => setStatus("idle"))
      .catch(() => setStatus("error"));
  };
  return (
    <span className="ui-artifact-download">
      <button
        className="ui-button ui-button--ghost ui-button--sm"
        type="button"
        disabled={!available || status === "downloading"}
        onClick={download}
      >
        {status === "downloading" ? "Downloading…" : "Download"}
      </button>
      {status === "error" ? (
        <span className="ui-caption" role="status">
          Download failed. Your artifact was not changed.
        </span>
      ) : null}
    </span>
  );
}
