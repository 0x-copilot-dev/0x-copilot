import type { ToolCallMessagePartProps } from "../../runtime/types";
import { Button, classNames } from "@enterprise-search/design-system";
import { useMemo, useState, type FormEvent, type ReactElement } from "react";
import { Streamdown } from "streamdown";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { MarkdownLink } from "../markdown/MarkdownLink";

const QUESTION_MARKDOWN_COMPONENTS = { a: MarkdownLink };

interface NormalizedOption {
  label: string;
  description: string | null;
  recommended: boolean;
}

export function AskAQuestionTool({
  args,
  approvalId,
  resolved,
  result,
  presentation,
  resume,
}: {
  args: Record<string, unknown>;
  approvalId: string;
  resolved: boolean;
  result: unknown;
  presentation: ReturnType<typeof presentationFromArgs>;
  resume: ToolCallMessagePartProps<Record<string, unknown>>["resume"];
}): ReactElement {
  const question =
    stringValue(args.question) ?? stringValue(args.message) ?? "";
  const options = useMemo(() => normalizeOptions(args.options), [args.options]);
  const multiSelect = readBoolean(args.multi_select, false);
  const allowFreeText = readBoolean(args.allow_free_text, true);
  const showFreeText = allowFreeText || options.length === 0;

  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState<string[]>([]);

  const submit = (payload: {
    selected?: string[];
    free_text?: string;
  }): void => {
    const sanitizedSelected = (payload.selected ?? [])
      .map((label) => label.trim())
      .filter(Boolean);
    const sanitizedFreeText = payload.free_text?.trim() ?? "";
    if (sanitizedSelected.length === 0 && sanitizedFreeText.length === 0) {
      return;
    }
    const composed = composeAnswer({
      selected: sanitizedSelected,
      freeText: sanitizedFreeText,
    });
    if (composed === null) {
      return;
    }
    resume({
      decision: "approved",
      approval_id: approvalId,
      approval_kind: "ask_a_question",
      answer: composed,
      selected: sanitizedSelected,
      free_text: sanitizedFreeText || null,
    });
  };

  const onChipClick = (option: NormalizedOption): void => {
    if (resolved) {
      return;
    }
    if (multiSelect) {
      setSelected((current) =>
        current.includes(option.label)
          ? current.filter((label) => label !== option.label)
          : [...current, option.label],
      );
      return;
    }
    submit({ selected: [option.label] });
  };

  const onFreeTextSubmit = (event: FormEvent): void => {
    event.preventDefault();
    submit({ selected, free_text: draft });
  };

  const onMultiSelectSubmit = (): void => {
    submit({ selected, free_text: draft });
  };

  const resolvedAnswer = answerFromResult(result, args);
  const resolvedSelected = selectedFromResult(result);

  return (
    <section
      className="aui-question-card"
      data-resolved={resolved ? "true" : undefined}
      aria-label="Atlas needs an answer"
    >
      <div className="aui-question-card__head">
        <span className="aui-question-card__icon" aria-hidden="true">
          ?
        </span>
        <div className="aui-question-card__question">
          {question ? (
            <Streamdown
              className="aui-question-card__body"
              components={QUESTION_MARKDOWN_COMPONENTS}
              mode="static"
            >
              {question}
            </Streamdown>
          ) : null}
        </div>
      </div>
      {resolved ? (
        <ResolvedAnswer answer={resolvedAnswer} selected={resolvedSelected} />
      ) : (
        <div className="aui-question-card__form">
          {options.length > 0 ? (
            <ChipRow
              options={options}
              selected={multiSelect ? selected : []}
              multiSelect={multiSelect}
              disabled={resolved}
              onClick={onChipClick}
            />
          ) : null}
          {showFreeText ? (
            <form
              className="aui-question-card__free-text"
              onSubmit={onFreeTextSubmit}
            >
              <input
                type="text"
                className="aui-question-card__input"
                placeholder={
                  options.length > 0
                    ? "Or type a different answer"
                    : "Type your answer"
                }
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                autoFocus={options.length === 0}
              />
              <Button
                type="submit"
                size="sm"
                disabled={
                  draft.trim().length === 0 &&
                  (!multiSelect || selected.length === 0)
                }
              >
                Send
              </Button>
            </form>
          ) : multiSelect ? (
            <div className="aui-question-card__free-text">
              <Button
                type="button"
                size="sm"
                disabled={selected.length === 0}
                onClick={onMultiSelectSubmit}
              >
                Send
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function ChipRow({
  options,
  selected,
  multiSelect,
  disabled,
  onClick,
}: {
  options: NormalizedOption[];
  selected: string[];
  multiSelect: boolean;
  disabled: boolean;
  onClick: (option: NormalizedOption) => void;
}): ReactElement {
  return (
    <ul
      className="aui-question-card__chips"
      role={multiSelect ? "group" : "radiogroup"}
    >
      {options.map((option) => {
        const isSelected = selected.includes(option.label);
        return (
          <li key={option.label} className="aui-question-card__chip-item">
            <button
              type="button"
              className={classNames(
                "aui-question-card__chip",
                isSelected ? "aui-question-card__chip--selected" : undefined,
                option.recommended
                  ? "aui-question-card__chip--recommended"
                  : undefined,
              )}
              aria-pressed={multiSelect ? isSelected : undefined}
              role={multiSelect ? "checkbox" : "radio"}
              aria-checked={isSelected}
              disabled={disabled}
              onClick={() => onClick(option)}
              title={option.description ?? undefined}
            >
              <span className="aui-question-card__chip-label">
                {option.label}
              </span>
              {option.recommended ? (
                <span className="aui-question-card__chip-flag">
                  Recommended
                </span>
              ) : null}
            </button>
            {option.description ? (
              <span className="aui-question-card__chip-description">
                {option.description}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function ResolvedAnswer({
  answer,
  selected,
}: {
  answer: string | null;
  selected: string[];
}): ReactElement | null {
  const chosen = selected.length > 0 ? selected.join(", ") : answer;
  if (!chosen) {
    return null;
  }
  return (
    <div className="aui-question-card__resolved">
      <span className="aui-question-card__resolved-check" aria-hidden="true">
        ✓
      </span>
      <span>
        You answered <strong>{chosen}</strong>
      </span>
    </div>
  );
}

function normalizeOptions(value: unknown): NormalizedOption[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const normalized: NormalizedOption[] = [];
  for (const entry of value) {
    if (typeof entry === "string") {
      const label = entry.trim();
      if (label) {
        normalized.push({ label, description: null, recommended: false });
      }
      continue;
    }
    const record = asRecord(entry);
    const label = stringValue(record.label);
    if (label === null) {
      continue;
    }
    normalized.push({
      label,
      description: stringValue(record.description),
      recommended: readBoolean(record.recommended, false),
    });
  }
  return normalized;
}

function readBoolean(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return fallback;
}

function composeAnswer({
  selected,
  freeText,
}: {
  selected: string[];
  freeText: string;
}): string | null {
  const parts = [...selected];
  if (freeText) {
    parts.push(freeText);
  }
  if (parts.length === 0) {
    return null;
  }
  return parts.join(", ");
}

function answerFromResult(
  result: unknown,
  args: Record<string, unknown>,
): string | null {
  if (result && typeof result === "object" && "answer" in result) {
    const answer = stringValue((result as Record<string, unknown>).answer);
    if (answer !== null) {
      return answer;
    }
  }
  return stringValue(args.answer);
}

function selectedFromResult(result: unknown): string[] {
  if (!result || typeof result !== "object" || !("selected" in result)) {
    return [];
  }
  const raw = (result as Record<string, unknown>).selected;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    const label = stringValue(entry);
    return label ? [label] : [];
  });
}
