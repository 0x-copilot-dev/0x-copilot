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
  // The placeholder is for a fetch still in flight, and NOTHING else. It used
  // to double as the fallback for `artifact === null`, which meant a fetch that
  // failed outright — `status: "error"`, artifact nulled — rendered as a
  // spinner, and `artifact-error` below was unreachable. A wire-contract drift
  // that stopped every artifact from loading therefore read as "slow" rather
  // than "broken", and took a live A/B to tell apart.
  //
  // `error` is deliberately NOT the second check: `useArtifactSurface` also
  // reports it with the artifact intact, for a host that has the metadata but
  // cannot stream content. That state owns a frame plus its notice, so the
  // error card is keyed on having nothing to show, not on the status alone.
  if (props.status === "deleted")
    return (
      <section className="ui-card" data-testid="artifact-deleted" role="status">
        This artifact was deleted or is no longer available.
      </section>
    );
  if (props.status === "loading")
    return (
      <section
        className="ui-card"
        data-testid="artifact-loading"
        aria-busy="true"
      >
        Loading artifact…
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
