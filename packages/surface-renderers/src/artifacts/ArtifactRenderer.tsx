import type { ReactElement } from "react";
import { CodeArtifactRenderer } from "./CodeArtifactRenderer";
import { DatasetArtifactRenderer } from "./DatasetArtifactRenderer";
import { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
import { FileArtifactRenderer } from "./FileArtifactRenderer";
import { RawArtifactFallback } from "./RawArtifactFallback";
import type { ArtifactRenderState } from "./model";

export function ArtifactRenderer(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  switch (props.artifact.kind) {
    case "code":
      return <CodeArtifactRenderer {...props} />;
    case "document":
      return <DocumentArtifactRenderer {...props} />;
    case "dataset":
      return <DatasetArtifactRenderer {...props} />;
    case "file":
      return <FileArtifactRenderer {...props} />;
    default:
      return <RawArtifactFallback {...props} />;
  }
}
