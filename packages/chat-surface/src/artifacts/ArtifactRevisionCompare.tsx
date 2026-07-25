import type { ReactElement } from "react";

const MAX_DIFF_LINES = 500;

export interface ArtifactTextComparison {
  readonly baseRevision: number;
  readonly currentRevision: number;
  readonly removed: readonly string[];
  readonly added: readonly string[];
  readonly truncated: boolean;
}

/**
 * Bounded line comparison for an explicitly selected immutable revision and
 * the current head. The labels carry the semantic change; no color is needed
 * to understand additions or removals.
 */
export function compareArtifactText(
  base: string,
  current: string,
  baseRevision: number,
  currentRevision: number,
): ArtifactTextComparison {
  const before = base.split("\n");
  const after = current.split("\n");
  let prefix = 0;
  while (
    prefix < before.length &&
    prefix < after.length &&
    before[prefix] === after[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < before.length - prefix &&
    suffix < after.length - prefix &&
    before[before.length - suffix - 1] === after[after.length - suffix - 1]
  ) {
    suffix += 1;
  }
  const removed = before.slice(prefix, before.length - suffix);
  const added = after.slice(prefix, after.length - suffix);
  const truncated = removed.length + added.length > MAX_DIFF_LINES;
  return {
    baseRevision,
    currentRevision,
    removed: removed.slice(0, MAX_DIFF_LINES),
    added: added.slice(0, MAX_DIFF_LINES),
    truncated,
  };
}

export function ArtifactRevisionCompare(props: {
  readonly comparison: ArtifactTextComparison | null;
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly onClose: () => void;
}): ReactElement | null {
  if (props.status === "idle") return null;
  if (props.status === "loading") {
    return (
      <section className="ui-card" role="status" aria-live="polite">
        Loading revision comparison…
      </section>
    );
  }
  if (props.status === "error" || props.comparison === null) {
    return (
      <section className="ui-card" role="alert">
        These revisions cannot be compared as bounded UTF-8 text. Download the
        exact bytes instead.
      </section>
    );
  }
  const { comparison } = props;
  return (
    <section className="ui-card" aria-label="Artifact revision comparison">
      <p className="ui-section-label">
        Compare r{comparison.baseRevision} to current r
        {comparison.currentRevision}
      </p>
      <p className="ui-caption" role="status" aria-live="polite">
        {comparison.removed.length} removed line
        {comparison.removed.length === 1 ? "" : "s"}; {comparison.added.length}{" "}
        added line{comparison.added.length === 1 ? "" : "s"}.
        {comparison.truncated
          ? " Diff output is capped for safe rendering."
          : ""}
      </p>
      <pre className="ui-code-block" aria-label="Text change details">
        <code>
          {comparison.removed.map((line, index) => (
            <span key={`removed-${index}`}>{`Removed: ${line}\n`}</span>
          ))}
          {comparison.added.map((line, index) => (
            <span key={`added-${index}`}>{`Added: ${line}\n`}</span>
          ))}
        </code>
      </pre>
      <button
        className="ui-button ui-button--ghost"
        type="button"
        onClick={props.onClose}
      >
        Close comparison
      </button>
    </section>
  );
}
