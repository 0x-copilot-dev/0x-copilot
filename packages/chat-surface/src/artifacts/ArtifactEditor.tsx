import { useEffect, useState, type ReactElement } from "react";

export function ArtifactEditor(props: {
  readonly initialText: string;
  readonly revision: number;
  readonly disabled: boolean;
  readonly onSave: (text: string) => Promise<"saved" | "conflict" | "error">;
}): ReactElement {
  const [text, setText] = useState(props.initialText);
  const [status, setStatus] = useState<
    "idle" | "saving" | "conflict" | "error"
  >("idle");
  useEffect(() => {
    setText(props.initialText);
    setStatus("idle");
  }, [props.initialText, props.revision]);
  const save = (): void => {
    setStatus("saving");
    void props
      .onSave(text)
      .then((outcome) => setStatus(outcome === "saved" ? "idle" : outcome));
  };
  return (
    <section className="ui-card" data-testid="artifact-editor">
      <label className="ui-section-label" htmlFor="artifact-editor-text">
        Edit revision {props.revision}
      </label>
      <textarea
        id="artifact-editor-text"
        className="ui-input ui-mono"
        value={text}
        disabled={props.disabled || status === "saving"}
        onChange={(event) => setText(event.target.value)}
        rows={16}
      />
      {status === "conflict" ? (
        <p className="ui-caption" role="alert">
          A newer revision exists. Your local edits are preserved; review it and
          manually rebase before saving.
        </p>
      ) : null}
      {status === "error" ? (
        <p className="ui-caption" role="alert">
          Could not save this revision. Your local edits are still here.
        </p>
      ) : null}
      <button
        className="ui-button"
        type="button"
        disabled={props.disabled || status === "saving"}
        onClick={save}
      >
        {status === "saving" ? "Saving…" : "Save new revision"}
      </button>
    </section>
  );
}
