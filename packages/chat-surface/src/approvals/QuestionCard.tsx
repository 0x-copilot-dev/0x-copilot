// The question card — `ask_a_question` as its own surface.
//
// This is a member of the consent-card family rather than a chip row in the
// transcript, and the reason is that a question is a real interrupt: the run is
// blocked, and the user may be on another chat or another screen when it lands.
// That means it has to be notifiable and has to be answerable from the approvals
// inbox — neither of which a row of chips in one transcript can do.
//
// It is NOT an approval, though, and the differences are deliberate:
//   · a `?` chip, not the shield — nothing here is being permitted
//   · the primary control is an answer, not "Approve"
//   · single-select answers on click; there is nothing to confirm
//   · the footer states the pause, because the pause is the whole cost
//
// Presentational only: the answer leaves via `onAnswer` and the host owns the POST.

import { useState, type ReactElement } from "react";
import { composeAnswer, isAnswerable, type QuestionSpec } from "./question";

export interface QuestionAnswer {
  /** Chosen option labels, in click order. */
  readonly selected: readonly string[];
  /** Typed answer, or null. */
  readonly freeText: string | null;
  /** The two joined the way the tool composes them server-side. */
  readonly answer: string;
}

export interface QuestionCardProps {
  readonly spec: QuestionSpec;
  /** "Launch Week ops" — which run is waiting. Server-derived. */
  readonly provenance?: string | null;
  /** Resolved questions collapse to the answer that was given. */
  readonly resolved?: boolean;
  readonly answer?: string | null;
  readonly onAnswer?: (answer: QuestionAnswer) => void;
  readonly testId?: string;
}

export function QuestionCard({
  spec,
  provenance = null,
  resolved = false,
  answer = null,
  onAnswer,
  testId,
}: QuestionCardProps): ReactElement {
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [draft, setDraft] = useState("");

  // A question with neither options nor free text would render a card with no
  // controls and leave the run blocked behind it. Free text is the fallback that
  // can always be offered.
  const showFreeText = spec.allowFreeText || !isAnswerable(spec);

  const submit = (nextSelected: readonly string[], freeText: string): void => {
    const composed = composeAnswer(nextSelected, freeText);
    if (composed === null) {
      return;
    }
    onAnswer?.({
      selected: nextSelected,
      freeText: freeText.trim() === "" ? null : freeText.trim(),
      answer: composed,
    });
  };

  const onChip = (label: string): void => {
    if (resolved) {
      return;
    }
    if (!spec.multiSelect) {
      // Single select IS the answer — asking the user to then press a confirm
      // button would be a second decision for one choice.
      submit([label], "");
      return;
    }
    setSelected((current) =>
      current.includes(label)
        ? current.filter((entry) => entry !== label)
        : [...current, label],
    );
  };

  return (
    <section
      className="qc"
      data-resolved={resolved ? "true" : undefined}
      data-testid={testId}
      aria-label={spec.header}
    >
      <header className="qc__head">
        <span className="qc__icon" aria-hidden="true">
          ?
        </span>
        <span className="qc__header">{spec.header}</span>
        {provenance !== null ? (
          <span className="qc__meta">{provenance}</span>
        ) : null}
        {spec.multiSelect && !resolved ? (
          <span className="qc__meta" data-testid="qc-count">
            {selected.length} of {spec.options.length} selected
          </span>
        ) : null}
      </header>

      <div className="qc__body">
        <p className="qc__question">{spec.question}</p>
        {spec.hint !== null ? <p className="qc__hint">{spec.hint}</p> : null}

        {resolved ? (
          <p className="qc__answer" data-testid="qc-answer">
            <span className="qc__answer-mark" aria-hidden="true">
              ✓
            </span>
            {answer ?? "Answered"}
          </p>
        ) : (
          <>
            {spec.options.length > 0 ? (
              <div className="qc__chips" role="group" aria-label="Options">
                {spec.options.map((option) => {
                  const on = selected.includes(option.label);
                  return (
                    <span className="qc__opt" key={option.label}>
                      {/* Marks the option, does NOT pre-select it. If the label
                          and the chip were both accent, "suggested" would read
                          as "already chosen". */}
                      {option.recommended ? (
                        <span className="qc__rec">Recommended</span>
                      ) : null}
                      <button
                        type="button"
                        className="qc__chip"
                        data-on={on ? "true" : undefined}
                        aria-pressed={spec.multiSelect ? on : undefined}
                        title={option.description ?? undefined}
                        data-testid={`qc-option-${option.label}`}
                        onClick={() => onChip(option.label)}
                      >
                        {spec.multiSelect ? (
                          <span className="qc__box" aria-hidden="true">
                            {on ? "✓" : ""}
                          </span>
                        ) : null}
                        {option.label}
                      </button>
                    </span>
                  );
                })}
              </div>
            ) : null}

            {showFreeText ? (
              <form
                className="qc__input"
                onSubmit={(event) => {
                  event.preventDefault();
                  submit(selected, draft);
                  setDraft("");
                }}
              >
                <input
                  type="text"
                  value={draft}
                  placeholder={
                    spec.options.length > 0
                      ? "Or answer in your own words…"
                      : "Type your answer…"
                  }
                  aria-label="Answer"
                  data-testid="qc-free-text"
                  onChange={(event) => setDraft(event.target.value)}
                />
                <button
                  type="submit"
                  className="qc__send"
                  aria-label="Send answer"
                  data-testid="qc-send"
                  disabled={composeAnswer(selected, draft) === null || resolved}
                >
                  ↑
                </button>
              </form>
            ) : null}

            {spec.multiSelect ? (
              <div className="qc__actions">
                <button
                  type="button"
                  className="apc-btn"
                  data-testid="qc-skip"
                  onClick={() =>
                    onAnswer?.({ selected: [], freeText: null, answer: "" })
                  }
                >
                  Skip
                </button>
                <button
                  type="button"
                  className="apc-btn apc-btn--primary"
                  data-testid="qc-confirm"
                  disabled={selected.length === 0}
                  onClick={() => submit(selected, draft)}
                >
                  {selected.length > 0
                    ? `Use these ${selected.length}`
                    : "Use selected"}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>

      {resolved ? null : (
        <p className="qc__foot">
          The run is paused here — it resumes as soon as you answer.
        </p>
      )}
    </section>
  );
}
