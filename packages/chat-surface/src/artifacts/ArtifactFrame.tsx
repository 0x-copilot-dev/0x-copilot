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
    <section className="ui-artifact-surface" data-testid="artifact-frame">
      <header className="ui-artifact-header">
        <div className="ui-artifact-header__identity">
          <h2 className="ui-artifact-title">{artifact.title}</h2>
          <span className="ui-badge">{artifact.kind} artifact</span>
        </div>
        <p className="ui-artifact-meta">
          r{artifact.revision} · {artifact.byteSize.toLocaleString()} bytes ·{" "}
          {artifact.digest.slice(0, 12)}
        </p>
        <div className="ui-artifact-header__actions">
          <ArtifactDownloadAction
            transport={props.transport}
            artifactId={artifact.artifactId}
            revision={artifact.revision}
            filename={artifact.filename}
            port={props.downloadPort}
          />
        </div>
      </header>
      <div className="ui-artifact-content">{props.children}</div>
    </section>
  );
}
