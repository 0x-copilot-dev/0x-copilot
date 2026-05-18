import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactElement,
} from "react";
import type { ProjectId } from "@enterprise-search/api-types";

// Branded ids — canonical site is `@enterprise-search/api-types`
// (`packages/api-types/src/brands.ts`). Re-export keeps existing
// `from "../inline-add"` imports working without a churn pass.
export type { ProjectId };
export type TodoPriority = "low" | "med" | "high";

export interface TodoQuickAddInput {
  readonly text: string;
  readonly project_id: ProjectId | null;
  readonly priority?: TodoPriority;
  /** ISO date (YYYY-MM-DD); user-tz interpreted server-side. */
  readonly due?: string;
}

export interface InlineAddProps {
  /**
   * Context-aware default project. Per cross-audit §9.6 Q6:
   *   - project-detail view → that project's id
   *   - `/todos` direct → null
   *   - inline-add → inherits TodosPanel's active project filter (this prop)
   *
   * Prop wins; fallback to null (Unfiled).
   */
  readonly defaultProject?: ProjectId | null;
  /**
   * Optional placeholder override. Defaults to a section-friendly hint.
   */
  readonly placeholder?: string;
  /**
   * Synchronous callback. Side effects (POST /v1/todos) are the caller's
   * job — this component is pure presentation per the P3-B2 brief.
   * Implementations should treat returns of `false` as "stay focused"
   * (e.g., to surface a row-level rollback elsewhere) but the component
   * always clears its own text after Enter regardless.
   */
  readonly onSubmit: (input: TodoQuickAddInput) => void;
  /**
   * Optional test hook for clock injection (date-phrase parsing).
   * Production callers leave this undefined — uses `Date.now()`.
   */
  readonly nowMs?: number;
}

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";

const DAY_MS = 86_400_000;
const WEEKDAY_NAMES: ReadonlyArray<string> = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];

/**
 * Parse a small whitelist of date phrases out of the trailing tokens of a
 * todo text. Returns `{ text, due }` where `text` is the user's input with
 * the matched phrase removed (trimmed). Minimum coverage per the brief:
 * "tomorrow", "next monday" (any weekday), "in N days".
 *
 * Unrecognised input leaves `due` undefined and `text` untouched. Pure;
 * no DOM, no clock except injected `nowMs`.
 */
export function parseQuickAddDate(
  raw: string,
  nowMs: number = Date.now(),
): { text: string; due?: string } {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { text: trimmed };

  // Try longest phrase first so "next monday" beats "monday".
  const lower = trimmed.toLowerCase();

  // "in N days" / "in N day"
  const inNDays = lower.match(/\bin\s+(\d{1,3})\s+days?\b\s*$/);
  if (inNDays !== null) {
    const n = Number.parseInt(inNDays[1] as string, 10);
    if (Number.isFinite(n) && n >= 0 && n <= 365) {
      const due = isoDateFromOffset(nowMs, n);
      const text = trimmed.slice(0, inNDays.index ?? trimmed.length).trim();
      return { text, due };
    }
  }

  // "next <weekday>"
  const nextWeekday = lower.match(
    /\bnext\s+(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b\s*$/,
  );
  if (nextWeekday !== null) {
    const targetIdx = WEEKDAY_NAMES.indexOf(nextWeekday[1] as string);
    if (targetIdx >= 0) {
      const now = new Date(nowMs);
      const currentIdx = now.getDay();
      // "next monday" when today is monday → 7 days out (next week's monday).
      let delta = targetIdx - currentIdx;
      if (delta <= 0) delta += 7;
      const due = isoDateFromOffset(nowMs, delta);
      const text = trimmed.slice(0, nextWeekday.index ?? trimmed.length).trim();
      return { text, due };
    }
  }

  // "tomorrow"
  const tomorrow = lower.match(/\btomorrow\b\s*$/);
  if (tomorrow !== null) {
    const due = isoDateFromOffset(nowMs, 1);
    const text = trimmed.slice(0, tomorrow.index ?? trimmed.length).trim();
    return { text, due };
  }

  // "today"
  const today = lower.match(/\btoday\b\s*$/);
  if (today !== null) {
    const due = isoDateFromOffset(nowMs, 0);
    const text = trimmed.slice(0, today.index ?? trimmed.length).trim();
    return { text, due };
  }

  return { text: trimmed };
}

function isoDateFromOffset(nowMs: number, dayOffset: number): string {
  const base = new Date(nowMs);
  base.setHours(0, 0, 0, 0);
  base.setTime(base.getTime() + dayOffset * DAY_MS);
  const y = base.getFullYear();
  const m = String(base.getMonth() + 1).padStart(2, "0");
  const d = String(base.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function InlineAdd({
  defaultProject,
  placeholder,
  onSubmit,
  nowMs,
}: InlineAddProps): ReactElement {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [text, setText] = useState("");

  // Prop wins; fallback to null (Unfiled). `undefined` means "caller did
  // not supply" → Unfiled. `null` means "explicitly Unfiled" → Unfiled.
  // Both collapse to null on submit.
  const projectId: ProjectId | null = useMemo(
    () => defaultProject ?? null,
    [defaultProject],
  );

  // Live preview of the parsed due date, so the user can see that
  // "buy milk tomorrow" will set a due date before they hit Enter.
  const preview = useMemo(() => parseQuickAddDate(text, nowMs), [text, nowMs]);

  const clear = useCallback(() => {
    setText("");
  }, []);

  const submit = useCallback(() => {
    const { text: parsedText, due } = parseQuickAddDate(text, nowMs);
    if (parsedText.length === 0) return;
    onSubmit({
      text: parsedText,
      project_id: projectId,
      ...(due !== undefined ? { due } : {}),
    });
    setText("");
    // Keep focus so the user can rip off multiple quick adds in a row.
    inputRef.current?.focus();
  }, [text, nowMs, onSubmit, projectId]);

  const handleKey = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submit();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        clear();
        inputRef.current?.blur();
      }
    },
    [submit, clear],
  );

  const handleFormSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      submit();
    },
    [submit],
  );

  const wrapper: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "10px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 10,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const labelStyle: CSSProperties = {
    position: "absolute",
    width: 1,
    height: 1,
    padding: 0,
    margin: -1,
    overflow: "hidden",
    clip: "rect(0 0 0 0)",
    whiteSpace: "nowrap",
    border: 0,
  };
  const inputStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    background: "transparent",
    border: "none",
    outline: "none",
    color: TEXT_PRIMARY,
    fontSize: 14,
    padding: 0,
  };
  const previewStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_FAINT,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: 140,
  };
  const submitStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 13,
    fontWeight: 600,
    cursor: text.trim().length === 0 ? "default" : "pointer",
    opacity: text.trim().length === 0 ? 0.5 : 1,
  };

  const inputId = "todo-inline-add-input";

  const projectHint = projectId === null ? "Unfiled" : `Project ${projectId}`;

  return (
    <form
      onSubmit={handleFormSubmit}
      data-testid="todo-inline-add"
      data-project-id={projectId ?? "unfiled"}
      style={wrapper}
      aria-label="Quick add todo"
    >
      <label htmlFor={inputId} style={labelStyle}>
        Add a todo
      </label>
      <input
        id={inputId}
        ref={inputRef}
        type="text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKey}
        placeholder={placeholder ?? 'Add a todo… (try "buy milk tomorrow")'}
        style={inputStyle}
        data-testid="todo-inline-add-input"
        autoComplete="off"
        spellCheck
      />
      {preview.due !== undefined ? (
        <span
          style={previewStyle}
          data-testid="todo-inline-add-due-preview"
          data-due={preview.due}
          aria-label={`Due ${preview.due}`}
        >
          {preview.due}
        </span>
      ) : null}
      <span
        style={previewStyle}
        data-testid="todo-inline-add-project-hint"
        aria-hidden="true"
      >
        {projectHint}
      </span>
      <button
        type="submit"
        style={submitStyle}
        data-testid="todo-inline-add-submit"
        disabled={text.trim().length === 0}
        aria-label="Add todo"
      >
        Add
      </button>
    </form>
  );
}
