import { useEffect, useMemo, useState, type ReactElement } from "react";

import type { ArtifactRenderState } from "./model";
import { previewNotice } from "./model";

const VISIBLE_LINE_WINDOW = 1_000;

/** Fixed inert source viewer. It never evaluates, highlights via HTML, or mounts a preview iframe. */
export function CodeArtifactRenderer(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const { artifact } = props;
  const notice = previewNotice(artifact);
  const text = artifact.text;
  if (notice !== null || text === undefined) {
    return (
      <div className="ui-card ui-body" data-testid="artifact-code-fallback">
        {notice ?? "Loading source…"}
      </div>
    );
  }
  return <CodeSourceViewer artifact={artifact} text={text} />;
}

function CodeSourceViewer(props: {
  readonly artifact: ArtifactRenderState;
  readonly text: string;
}): ReactElement {
  const { artifact, text } = props;
  const lines = useMemo(() => text.split("\n"), [text]);
  const [start, setStart] = useState(0);
  const [query, setQuery] = useState("");
  const [wrap, setWrap] = useState(false);
  useEffect(() => {
    setStart(0);
    setQuery("");
  }, [artifact.artifactId, artifact.revision]);
  const end = Math.min(start + VISIBLE_LINE_WINDOW, lines.length);
  const windowed = lines.slice(start, end);
  const isWindowed = lines.length > VISIBLE_LINE_WINDOW;
  const findNext = (): void => {
    const needle = query.trim().toLocaleLowerCase();
    if (needle === "") return;
    const next = lines.findIndex(
      (line, index) =>
        index >= end && line.toLocaleLowerCase().includes(needle),
    );
    const wrapped = lines.findIndex((line) =>
      line.toLocaleLowerCase().includes(needle),
    );
    const match = next === -1 ? wrapped : next;
    if (match >= 0)
      setStart(Math.floor(match / VISIBLE_LINE_WINDOW) * VISIBLE_LINE_WINDOW);
  };
  return (
    <section
      className="ui-card"
      data-testid="artifact-code-renderer"
      data-windowed={isWindowed ? "true" : "false"}
    >
      <div className="ui-toolbar" aria-label="Source viewer controls">
        <label
          className="ui-caption"
          htmlFor={`artifact-search-${artifact.artifactId}-${artifact.revision}`}
        >
          Search source
        </label>
        <input
          id={`artifact-search-${artifact.artifactId}-${artifact.revision}`}
          className="ui-input"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") findNext();
          }}
        />
        <button
          className="ui-button ui-button--ghost"
          type="button"
          onClick={findNext}
        >
          Find next
        </button>
        <label className="ui-caption">
          <input
            type="checkbox"
            checked={wrap}
            onChange={(event) => setWrap(event.target.checked)}
          />{" "}
          Wrap lines
        </label>
      </div>
      <pre
        className="ui-code-block"
        aria-label={`${artifact.filename} source`}
        style={{ whiteSpace: wrap ? "pre-wrap" : "pre" }}
      >
        <code>
          <ol start={start + 1}>
            {windowed.map((line, index) => (
              <li key={start + index}>{line}</li>
            ))}
          </ol>
        </code>
      </pre>
      {isWindowed ? (
        <p className="ui-caption">
          {start === 0
            ? `Showing the first ${VISIBLE_LINE_WINDOW.toLocaleString()} lines.`
            : `Showing lines ${(start + 1).toLocaleString()}–${end.toLocaleString()}.`}{" "}
          Download the artifact for the exact bytes.
        </p>
      ) : null}
      {isWindowed ? (
        <div className="ui-toolbar" aria-label="Source window navigation">
          <button
            className="ui-button ui-button--ghost"
            type="button"
            disabled={start === 0}
            onClick={() => setStart(Math.max(0, start - VISIBLE_LINE_WINDOW))}
          >
            Previous lines
          </button>
          <button
            className="ui-button ui-button--ghost"
            type="button"
            disabled={end === lines.length}
            onClick={() =>
              setStart(Math.min(lines.length - 1, start + VISIBLE_LINE_WINDOW))
            }
          >
            Next lines
          </button>
        </div>
      ) : null}
    </section>
  );
}
