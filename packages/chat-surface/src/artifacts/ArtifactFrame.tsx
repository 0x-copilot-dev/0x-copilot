import type { ArtifactRenderState } from "@0x-copilot/api-types";
import type { ReactNode, ReactElement } from "react";
import type { Transport } from "@0x-copilot/chat-transport";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";
import { ArtifactDownloadAction } from "./ArtifactDownloadAction";

export function ArtifactFrame(props: {
  readonly artifact: ArtifactRenderState | null;
  readonly status: "loading" | "ready" | "error" | "deleted";
  readonly transport: Transport;
  readonly downloadPort?: ArtifactDownloadPort;
  readonly children?: ReactNode;
}): ReactElement {
  if (
    props.status === "loading" ||
    (props.artifact === null && props.status !== "deleted")
  )
    return (
      <section
        className="ui-card"
        data-testid="artifact-loading"
        aria-busy="true"
      >
        Loading artifact…
      </section>
    );
  if (props.status === "deleted")
    return (
      <section className="ui-card" data-testid="artifact-deleted" role="status">
        This artifact was deleted or is no longer available.
      </section>
    );
  if (props.artifact === null)
    return (
      <section className="ui-card" data-testid="artifact-error" role="status">
        Artifact details could not be loaded. The original artifact was not
        changed.
      </section>
    );
  const artifact = props.artifact;
  return (
    <section className="ui-card" data-testid="artifact-frame">
      <header>
        <p className="ui-eyebrow">{artifact.kind} artifact</p>
        <h2 className="ui-title">{artifact.title}</h2>
        <p className="ui-caption">
          {artifact.filename} · revision {artifact.revision} ·{" "}
          {artifact.byteSize.toLocaleString()} bytes ·{" "}
          {artifact.digest.slice(0, 12)}
        </p>
        <ArtifactDownloadAction
          transport={props.transport}
          artifactId={artifact.artifactId}
          revision={artifact.revision}
          filename={artifact.filename}
          port={props.downloadPort}
        />
      </header>
      {props.children}
    </section>
  );
}
