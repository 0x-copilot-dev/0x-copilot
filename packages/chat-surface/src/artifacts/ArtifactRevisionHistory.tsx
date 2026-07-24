import type { ArtifactRevision } from "@0x-copilot/api-types";
import type { ReactElement } from "react";

export function ArtifactRevisionHistory(props: {
  readonly revisions: readonly ArtifactRevision[];
  readonly activeRevision: number;
  readonly onSelect: (revision: number) => void;
  readonly hasOlderHistory: boolean;
  readonly onLoadOlder: () => void;
}): ReactElement {
  return (
    <section className="ui-card" data-testid="artifact-revision-history">
      <p className="ui-section-label">Revision history</p>
      <ol>
        {props.revisions.map((revision) => (
          <li key={revision.revision}>
            <button
              className="ui-button ui-button--ghost"
              type="button"
              aria-pressed={revision.revision === props.activeRevision}
              onClick={() => props.onSelect(revision.revision)}
            >
              r{revision.revision}
            </button>
            <span className="ui-caption">
              {" "}
              {revision.author} · {revision.created_at} ·{" "}
              {revision.byte_size.toLocaleString()} bytes ·{" "}
              {revision.content_digest.slice(0, 12)}
              {revision.source_ref ? ` · ${revision.source_ref}` : ""}
            </span>
          </li>
        ))}
      </ol>
      {props.hasOlderHistory ? (
        <button
          className="ui-button ui-button--ghost"
          type="button"
          onClick={props.onLoadOlder}
        >
          Load older revisions
        </button>
      ) : null}
    </section>
  );
}
