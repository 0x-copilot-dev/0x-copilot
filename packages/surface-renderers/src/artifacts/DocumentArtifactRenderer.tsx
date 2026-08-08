import type { ReactElement } from "react";
import { MarkdownText } from "@0x-copilot/chat-surface";

import { EditableDocument } from "./EditableDocument";
import type { ArtifactRenderState } from "./model";
import { hostEditorActions, previewNotice } from "./model";

/**
 * The product's reviewed Markdown pipeline owns parsing. It disables raw HTML
 * and keeps links/media on its established safe-URL policy; artifact content
 * never gets an executable renderer path of its own.
 *
 * Two renderings, one pipeline. Read-only is the default and the only thing a
 * surface gets unless the HOST attached `documentEditor` — an object it built
 * around its own transport, never a field decoded from content, never something
 * a model-authored `SurfaceSpec` could carry. When it is present the document
 * renders as editable BLOCKS, which is the only way a table in it becomes
 * clickable: the read-only path is one string of markdown, so there is no table
 * component to click into, just text shaped like a grid.
 */
export function DocumentArtifactRenderer(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const { artifact } = props;
  const notice = previewNotice(artifact);
  if (notice !== null || artifact.text === undefined) {
    return (
      <div className="ui-card ui-body" data-testid="artifact-document-fallback">
        {notice ?? "Loading document…"}
      </div>
    );
  }
  const actions = hostEditorActions(artifact, "documentEditor");
  if (actions === null) {
    return (
      <article className="ui-card" data-testid="artifact-document-renderer">
        <MarkdownText
          type="text"
          text={artifact.text}
          status={{ type: "complete" }}
        />
      </article>
    );
  }
  return (
    <article
      className="ui-card"
      data-testid="artifact-document-renderer"
      data-editable="true"
    >
      <EditableDocument
        source={artifact.text}
        actions={actions}
        documentKey={`${artifact.artifactId}@${artifact.revision}`}
        {...(actions.idPrefix === undefined
          ? {}
          : { idPrefix: actions.idPrefix })}
      />
    </article>
  );
}
