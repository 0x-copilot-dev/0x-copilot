import type { CSSProperties, ReactElement } from "react";
import type { ArtifactAuthor } from "@0x-copilot/api-types";
import { DiffText } from "../textdiff/DiffText";
import { wordDiff } from "../textdiff/wordDiff";
import type { ArtifactTextComparison } from "./ArtifactRevisionCompare";

/** The authors whose revision a reader did not make, and so did not expect. */
export const REVIEWED_ARTIFACT_AUTHORS: ReadonlySet<ArtifactAuthor> =
  new Set<ArtifactAuthor>(["model", "subagent"]);

/**
 * An agent revision that landed on top of the revision the reader had on
 * screen. `baseRevision` is the one that was being read — the comparison base,
 * and the one a revert appends a copy of. It is the landed revision's parent in
 * the usual case, but not when a turn writes several revisions and the tab
 * jumps past them (PRD-03 D1).
 */
export interface ArtifactRevisionReviewState {
  readonly baseRevision: number;
  readonly revision: number;
  readonly author: ArtifactAuthor;
}

/**
 * Post-hoc review of an agent revision (PRD-03 D1). Deliberately not an
 * approval gate: the revision is already current by the time this renders, and
 * artifact writes stay ungated. The panel exists so the change is announced
 * instead of swapping underneath the reader, and it offers the two
 * version-control outcomes — keep it, or append a copy of the parent. Neither
 * rewrites history.
 */
export function ArtifactRevisionReview(props: {
  readonly review: ArtifactRevisionReviewState | null;
  readonly comparison: ArtifactTextComparison | null;
  readonly status: "idle" | "loading" | "ready" | "error";
  /**
   * The artifact's own renderer is showing the change in its own language — a
   * dataset diffing cells (PRD-03 D4). The panel then keeps the announcement
   * and the two actions and drops its text diff, rather than putting a second,
   * poorer reading of the same change on screen.
   */
  readonly changeShownInSurface?: boolean;
  readonly onKeep: () => void;
  readonly onRevert: () => void;
  readonly revertDisabled: boolean;
}): ReactElement | null {
  const { review } = props;
  if (review === null) return null;
  return (
    <section
      className="ui-artifact-subpanel"
      aria-label="Agent revision review"
      data-testid="artifact-revision-review"
    >
      <p className="ui-section-label">
        {authorLabel(review.author)} revised this artifact: r
        {review.baseRevision} → r{review.revision}
      </p>
      <ReviewBody
        baseRevision={review.baseRevision}
        comparison={props.comparison}
        status={props.status}
        changeShownInSurface={props.changeShownInSurface === true}
      />
      <div style={actionsStyle}>
        <button
          className="ui-button ui-button--sm ui-button--secondary"
          type="button"
          onClick={props.onKeep}
        >
          Keep this revision
        </button>
        <button
          className="ui-button ui-button--sm ui-button--ghost"
          type="button"
          disabled={props.revertDisabled}
          onClick={props.onRevert}
        >
          Revert to r{review.baseRevision}
        </button>
      </div>
    </section>
  );
}

function ReviewBody(props: {
  readonly baseRevision: number;
  readonly comparison: ArtifactTextComparison | null;
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly changeShownInSurface: boolean;
}): ReactElement {
  if (props.status === "loading")
    return (
      <p className="ui-caption" aria-live="polite">
        Loading what changed…
      </p>
    );
  if (props.status !== "ready" || props.comparison === null)
    // The revision still stands and revert is still offered; only the preview
    // of what changed is unavailable, and saying so beats an empty diff.
    return (
      <p className="ui-caption" role="alert">
        This change cannot be shown as bounded UTF-8 text. Download the exact
        bytes to compare, or revert to r{props.baseRevision}.
      </p>
    );
  if (props.changeShownInSurface)
    return (
      <p className="ui-caption">
        What changed is shown in the dataset view above.
      </p>
    );
  const { comparison } = props;
  return (
    <>
      <p className="ui-caption">
        {comparison.removed.length} removed line
        {comparison.removed.length === 1 ? "" : "s"}; {comparison.added.length}{" "}
        added line{comparison.added.length === 1 ? "" : "s"}.
        {comparison.truncated
          ? " Change output is capped for safe rendering."
          : ""}
      </p>
      <div aria-label="Revision change details">
        {/* Word diff of the changed region only — `compareArtifactText` has
            already trimmed the shared prefix and suffix and capped the line
            count, so the render budget is bounded before `wordDiff` sees it. */}
        <DiffText
          hunks={wordDiff(
            comparison.removed.join("\n"),
            comparison.added.join("\n"),
          )}
        />
      </div>
    </>
  );
}

function authorLabel(author: ArtifactAuthor): string {
  return author === "subagent" ? "A subagent" : "The model";
}

// No design-system rule covers a two-button row inside `ui-artifact-subpanel`
// (a grid), and this package cannot add one. Kept inline rather than shipping a
// class name with no stylesheet behind it.
const actionsStyle: CSSProperties = { display: "flex", gap: 4 };
