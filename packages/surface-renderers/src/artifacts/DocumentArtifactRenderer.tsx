import type { ReactElement } from "react";
import { MarkdownText } from "@0x-copilot/chat-surface";

import type { ArtifactRenderState } from "./model";
import { previewNotice } from "./model";

/**
 * The product's reviewed Markdown pipeline owns parsing. It disables raw HTML
 * and keeps links/media on its established safe-URL policy; artifact content
 * never gets an executable renderer path of its own.
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
