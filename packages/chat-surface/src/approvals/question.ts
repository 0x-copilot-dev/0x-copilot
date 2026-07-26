// The `ask_a_question` payload, parsed off the run stream.
//
// The backend already projects this shape through its own allow-list
// (`_ask_a_question_requested_payload`), so nothing new is on the wire — the
// cockpit simply never read it, and rendered the question through the generic
// approval card instead. That card offers Approve and Reject, which are not
// answers to a question.
//
// Every string here is model-authored. Render as PLAIN TEXT.

/** One option chip. `recommended` marks it, it does not pre-select it. */
export interface QuestionOption {
  readonly label: string;
  readonly description: string | null;
  readonly recommended: boolean;
}

export interface QuestionSpec {
  /** Card header — short by contract (24 chars). Also the notification title. */
  readonly header: string;
  readonly question: string;
  readonly hint: string | null;
  readonly options: readonly QuestionOption[];
  readonly multiSelect: boolean;
  readonly allowFreeText: boolean;
}

const DEFAULT_HEADER = "Quick question";
// Mirrors the tool contract's `_Limits.OPTION_COUNT_MAX`. A model that ignores
// it gets truncated rather than handed an unbounded chip row.
const MAX_OPTIONS = 8;

/**
 * Parse an `ask_a_question` approval payload, or null when it carries no question.
 *
 * Null is the honest answer for a malformed payload: the caller falls back to the
 * generic approval card, which is worse but not broken. Inventing a question
 * would be worse than both.
 */
export function parseQuestion(payload: unknown): QuestionSpec | null {
  if (!isRecord(payload)) {
    return null;
  }
  const question = text(payload.question) ?? text(payload.message);
  if (question === null) {
    return null;
  }
  return {
    header: text(payload.header) ?? DEFAULT_HEADER,
    question,
    hint: text(payload.hint),
    options: parseOptions(payload.options),
    multiSelect: payload.multi_select === true,
    // Default TRUE, matching the tool contract: a question with no options must
    // still be answerable, so free text is only off when the server says so.
    allowFreeText: payload.allow_free_text !== false,
  };
}

/**
 * Whether the card can be answered at all.
 *
 * A question with no options and no free text is unanswerable — the run would
 * sit blocked behind a card with no controls. Callers use this to fall back to
 * the free-text input regardless of the flag.
 */
export function isAnswerable(spec: QuestionSpec): boolean {
  return spec.options.length > 0 || spec.allowFreeText;
}

/** Join a selection and a typed answer the way the tool composes them server-side. */
export function composeAnswer(
  selected: readonly string[],
  freeText: string | null,
): string | null {
  const parts = [...selected];
  const trimmed = freeText?.trim() ?? "";
  if (trimmed.length > 0) {
    parts.push(trimmed);
  }
  return parts.length > 0 ? parts.join(", ") : null;
}

function parseOptions(value: unknown): readonly QuestionOption[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const options: QuestionOption[] = [];
  for (const entry of value) {
    const option = parseOption(entry);
    if (option === null) {
      continue;
    }
    options.push(option);
    // Checked after every push, not just in one branch: a model that sends
    // thirty bare strings must be capped exactly like one sending thirty
    // objects, or the cap only holds for the shape we happened to test.
    if (options.length === MAX_OPTIONS) {
      break;
    }
  }
  return options;
}

function parseOption(entry: unknown): QuestionOption | null {
  // The tool contract normalises bare strings to `{label}`; accept both so a
  // model that sends `["a","b"]` still gets chips.
  if (typeof entry === "string") {
    const label = entry.trim();
    return label.length > 0
      ? { label, description: null, recommended: false }
      : null;
  }
  if (!isRecord(entry)) {
    return null;
  }
  const label = text(entry.label);
  if (label === null) {
    return null;
  }
  return {
    label,
    description: text(entry.description),
    recommended: entry.recommended === true,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}
