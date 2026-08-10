// The inline artifact card — an artifact where it was produced, in the thread.
//
// Focus mode used to answer "an artifact exists" with a pinned card above the
// transcript whose only move was `Open in Studio`. That made reading an
// artifact a mode switch: leave Focus, read, come back, and find the thread
// scrolled away. This card is the replacement — collapsed it is one row of
// identity, expanded it mounts the REAL `ArtifactSurface`, which is the same
// component Studio mounts and therefore the same renderer and the same editor.
//
// What this component deliberately does NOT do:
//   · fetch. `ArtifactSurface` owns the content/revision fetch, and it only
//     mounts once expanded, so a collapsed card costs nothing but a row.
//   · re-derive artifact state. Everything on the collapsed row arrives as
//     props from one projection the host already holds.
//   · offer a decision. Artifacts are ungated — there is no approve/reject on
//     an artifact revision (see `ArtifactRevisionReview`), and inventing one
//     here would render an audit trail that does not exist.

import { useState, type ReactElement } from "react";

import type { ArtifactKind } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import { ArtifactSurface } from "../artifacts/ArtifactSurface";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";
import { resolveSurfaceHue, type SurfaceHue } from "../surfaces/surfaceHue";

/**
 * One artifact's place in the transcript. A flat view model, built once by the
 * host (`buildInlineArtifacts`) from the projection it already holds — the card
 * never reaches back for anything.
 */
export interface InlineArtifactEntry {
  readonly artifactId: string;
  readonly kind: ArtifactKind;
  /** `artifact-{kind}://{id}@{rev}` — what `ArtifactSurface` resolves. */
  readonly uri: string;
  /** The record's real name, WITHOUT the ` · r{n}` suffix the tab label adds. */
  readonly name: string;
  readonly revision: number;
  /**
   * Where this artifact belongs in the thread: the `sequence_no` it was
   * PUBLISHED at, never the one it was last revised at.
   */
  readonly createdSeq: number;
  /** The author's chosen accent, when there was one. */
  readonly hue?: SurfaceHue;
  /** The lifecycle subject key, so the host can open this exact tab in Studio. */
  readonly subjectKey: string;
}

export interface TcInlineArtifactCardProps {
  readonly artifact: InlineArtifactEntry;
  readonly transport: Transport;
  readonly downloadPort?: ArtifactDownloadPort;
  /** Hands the reader the full Studio workspace. Never the ONLY way to look. */
  readonly onOpenInStudio?: (subjectKey: string) => void;
}

export function TcInlineArtifactCard(
  props: TcInlineArtifactCardProps,
): ReactElement {
  const { artifact, transport, downloadPort, onOpenInStudio } = props;
  // Expansion is pure local view state. It is deliberately NOT lifted: nothing
  // outside this card acts on it, and holding it in the host would make every
  // expand re-render the whole transcript.
  const [open, setOpen] = useState(false);
  const hue = resolveSurfaceHue({ uri: artifact.uri, choice: artifact.hue });
  const label = `${artifact.name} · r${artifact.revision}`;

  return (
    <article
      className="tc-inline-artifact"
      data-testid="tc-inline-artifact"
      data-artifact-id={artifact.artifactId}
      data-artifact-kind={artifact.kind}
      data-surface-hue={hue}
      data-open={open ? "true" : "false"}
    >
      <div className="tc-inline-artifact__row">
        <span aria-hidden="true" className="tc-inline-artifact__dot" />
        <span className="tc-inline-artifact__name" title={label}>
          {artifact.name}
        </span>
        <span className="tc-inline-artifact__meta">r{artifact.revision}</span>
        <span className="tc-inline-artifact__spacer" />
        {/* A labelled button, not a disclosure caret. A caret promises "more
            text"; this opens an editable workspace, and the difference is worth
            a word. `aria-expanded` still carries the disclosure semantics. */}
        <button
          type="button"
          className="tc-inline-artifact__toggle"
          data-testid="tc-inline-artifact-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </div>

      {open ? (
        <div className="tc-inline-artifact__body">
          <div className="tc-inline-artifact__actions">
            {onOpenInStudio === undefined ? null : (
              <button
                type="button"
                className="tc-inline-artifact__action"
                data-testid="tc-inline-artifact-open-studio"
                onClick={() => onOpenInStudio(artifact.subjectKey)}
              >
                Open in Studio
              </button>
            )}
          </div>
          {/* The real thing: same surface, same renderer registry, same
              `ArtifactEditor` — so "editable exactly as Studio is" costs no new
              save path. `idPrefix` scopes its DOM id, because inline there can
              be two expanded editors on one page. */}
          <ArtifactSurface
            uri={artifact.uri}
            transport={transport}
            idPrefix={`inline-${artifact.artifactId}`}
            {...(artifact.hue === undefined ? {} : { hue: artifact.hue })}
            {...(downloadPort === undefined ? {} : { downloadPort })}
          />
        </div>
      ) : null}
    </article>
  );
}
