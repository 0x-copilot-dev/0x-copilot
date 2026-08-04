// The cockpit's artifact list, shaped for the transcript.
//
// Pure, synchronous, and deliberately outside `RunDestination` — not because it
// is complex, but because it is the one place three sources of display identity
// get merged, and that merge is exactly what has gone wrong twice before:
// `accentByArtifactId` and `artifactTitleById` were both scoped inside a memo
// where only the tab strip could reach them, so the tab and the card disagreed
// about the same artifact's colour, then its name. Merging in one tested
// function keeps a third consumer from inventing a fourth answer.

import type { ArtifactSurfaceTab } from "../../artifacts/artifactProjection";
import type { SurfaceHue } from "../../surfaces/surfaceHue";
import type { InlineArtifactEntry } from "../../thread-canvas/TcInlineArtifactCard";

/** The lifecycle subject key an artifact tab is opened by, in Studio. */
export function artifactSubjectKey(artifactId: string): string {
  return `artifact:${artifactId}`;
}

/**
 * Merge the artifact fold with the conversation-canvas record's authoritative
 * title and accent.
 *
 * `titleById` wins over the fold's `name`. The fold can only ever synthesize
 * `"<kind> artifact"` — `artifact.created` carries no title on the wire — so
 * without this merge two CSVs published in one run both read "dataset
 * artifact", which is precisely the bug the Focus cards shipped with.
 */
export function buildInlineArtifacts(
  tabs: readonly ArtifactSurfaceTab[],
  titleById: ReadonlyMap<string, string>,
  accentById: ReadonlyMap<string, SurfaceHue>,
): readonly InlineArtifactEntry[] {
  return (
    tabs
      .map((tab) => {
        const accent = accentById.get(tab.artifactId);
        return {
          artifactId: tab.artifactId,
          kind: tab.kind,
          uri: tab.uri,
          name: titleById.get(tab.artifactId) ?? tab.name,
          revision: tab.revision,
          createdSeq: tab.createdSeq,
          subjectKey: artifactSubjectKey(tab.artifactId),
          ...(accent === undefined ? {} : { hue: accent }),
        };
      })
      // Publication order. `projectArtifactTabs` sorts newest-mutation-first for
      // the tab strip, which is the opposite of what a transcript wants: the
      // thread reads top-down, oldest first, and an artifact belongs after the
      // message that produced it.
      .sort((a, b) => a.createdSeq - b.createdSeq)
  );
}
