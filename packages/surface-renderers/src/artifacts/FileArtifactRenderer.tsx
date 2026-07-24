import type { ReactElement } from "react";
import type { ArtifactRenderState } from "./model";

export function FileArtifactRenderer(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const { artifact } = props;
  return (
    <section className="ui-card" data-testid="artifact-file-renderer">
      <p className="ui-title">{artifact.filename}</p>
      <p className="ui-caption">
        {artifact.mediaType} · {artifact.byteSize.toLocaleString()} bytes ·
        revision {artifact.revision} · {artifact.author} ·{" "}
        {artifact.digest.slice(0, 12)}
      </p>
      <p className="ui-body">
        Raw file preview is intentionally unavailable. Download the exact
        artifact bytes.
      </p>
    </section>
  );
}
