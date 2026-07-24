import type { ReactElement } from "react";
import type { ArtifactRenderState } from "./model";

export function RawArtifactFallback(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const { artifact } = props;
  return (
    <section className="ui-card" data-testid="artifact-raw-fallback">
      <p className="ui-title">{artifact.title}</p>
      <p className="ui-caption">
        {artifact.mediaType} · revision {artifact.revision}
      </p>
      <p className="ui-body">
        {artifact.notice ??
          "No safe fixed renderer is available for this artifact."}
      </p>
    </section>
  );
}
