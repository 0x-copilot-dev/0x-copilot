import type { ArtifactRevision } from "@0x-copilot/api-types";
import type { ReactElement } from "react";

export function ArtifactRevisionHistory(props: {
  readonly revisions: readonly ArtifactRevision[];
  readonly activeRevision: number;
  readonly onSelect: (revision: number) => void;
  readonly latestRevision: number | null;
  readonly onCompareToCurrent: (revision: number) => void;
  readonly onRestore: (revision: number) => void;
  readonly restoreDisabled: boolean;
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
            {props.latestRevision !== null &&
            revision.revision !== props.latestRevision ? (
              <span>
                <button
                  className="ui-button ui-button--ghost"
                  type="button"
                  onClick={() => props.onCompareToCurrent(revision.revision)}
                >
                  Compare to current
                </button>
                <button
                  className="ui-button ui-button--ghost"
                  type="button"
                  disabled={props.restoreDisabled}
                  onClick={() => props.onRestore(revision.revision)}
                >
                  Restore as new revision
                </button>
              </span>
            ) : null}
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
