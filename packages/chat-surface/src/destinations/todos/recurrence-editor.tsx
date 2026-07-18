// Recurrence editor — Phase 3 sub-PRD §16 Q2 + implementation-plan §11.1.
//
// User-facing editor that produces a `TodoRecurrence` shape (rule + spec)
// from a small RRULE-subset:
//   - FREQ ∈ { DAILY, WEEKLY, MONTHLY }
//   - BYDAY (weekly only) ∈ { MO,TU,WE,TH,FR,SA,SU }
//   - INTERVAL: integer ≥ 1
//
// The output `spec` string follows RFC 5545 RRULE syntax for the
// `rule="rrule"` branch and a compact `every_N_days:N` form for the
// `every_N_days` shortcut (matches implementation-plan §11.1's two
// allowed `rule` values). `every_weekday` is the third allowed rule and
// is selectable via a quick shortcut.
//
// Server-managed fields (`next_materialize_at`, `series_id`) are NOT
// part of this editor's output — the backend assigns them on the
// `POST /v1/todos` that consumes this object. Editor stays in the user
// surface, never invents a series id.
//
// Substrate-agnostic: no `window`/`document`/`fetch`. Pure controlled
// component — parent owns the value via `onChange`.

import {
  useCallback,
  useMemo,
  type CSSProperties,
  type ReactElement,
} from "react";

// ===========================================================================
// Public types — local until P3-A merges api-types/src/todos.ts.
// Shape matches implementation-plan §11.1 exactly.
// ===========================================================================

export type TodoRecurrenceRule = "rrule" | "every_N_days" | "every_weekday";

/**
 * The user-controlled subset of `Todo.recurrence`. Server adds
 * `series_id` + `next_materialize_at` when this lands in `POST /v1/todos`.
 */
export interface TodoRecurrence {
  readonly rule: TodoRecurrenceRule;
  readonly spec: string;
}

export type RecurrenceFreq = "DAILY" | "WEEKLY" | "MONTHLY";

/**
 * RFC 5545 weekday codes. Order matches Mon-first display order; we keep
 * BYDAY output in this order so the rendered preview is stable.
 */
export const BYDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] as const;
export type Byday = (typeof BYDAY_CODES)[number];

const BYDAY_LABEL: Readonly<Record<Byday, string>> = {
  MO: "M",
  TU: "T",
  WE: "W",
  TH: "T",
  FR: "F",
  SA: "S",
  SU: "S",
};

const BYDAY_LONG: Readonly<Record<Byday, string>> = {
  MO: "Mon",
  TU: "Tue",
  WE: "Wed",
  TH: "Thu",
  FR: "Fri",
  SA: "Sat",
  SU: "Sun",
};

// ===========================================================================
// Pure spec helpers (exported so tests can pin them)
// ===========================================================================

/**
 * Build an RRULE spec string from FREQ, INTERVAL, and (weekly) BYDAY.
 * Order is fixed: FREQ then INTERVAL (>1) then BYDAY (non-empty).
 */
export function buildRruleSpec(args: {
  readonly freq: RecurrenceFreq;
  readonly interval: number;
  readonly byday: ReadonlyArray<Byday>;
}): string {
  const parts: Array<string> = [`FREQ=${args.freq}`];
  if (args.interval > 1) parts.push(`INTERVAL=${args.interval}`);
  if (args.freq === "WEEKLY" && args.byday.length > 0) {
    // Preserve canonical Mon→Sun order.
    const ordered = BYDAY_CODES.filter((d) => args.byday.includes(d));
    parts.push(`BYDAY=${ordered.join(",")}`);
  }
  return parts.join(";");
}

/**
 * Render a human-readable preview of a recurrence given today's date.
 * Pure — accepts `today` so tests pin the date.
 */
export function previewRecurrence(value: TodoRecurrence, today: Date): string {
  const startsIso = today.toISOString().slice(0, 10);
  if (value.rule === "every_weekday") {
    return `Repeats every weekday starting ${startsIso}`;
  }
  if (value.rule === "every_N_days") {
    const n = parseEveryNDays(value.spec);
    if (n === null) return `Repeats (invalid spec) starting ${startsIso}`;
    if (n === 1) return `Repeats every day starting ${startsIso}`;
    return `Repeats every ${n} days starting ${startsIso}`;
  }
  // rrule
  const parsed = parseRruleSpec(value.spec);
  if (parsed === null) return `Repeats (invalid spec) starting ${startsIso}`;
  const { freq, interval, byday } = parsed;
  if (freq === "DAILY") {
    if (interval <= 1) return `Repeats every day starting ${startsIso}`;
    return `Repeats every ${interval} days starting ${startsIso}`;
  }
  if (freq === "WEEKLY") {
    if (byday.length === 0) {
      if (interval <= 1) return `Repeats every week starting ${startsIso}`;
      return `Repeats every ${interval} weeks starting ${startsIso}`;
    }
    const labels = BYDAY_CODES.filter((d) => byday.includes(d)).map(
      (d) => BYDAY_LONG[d],
    );
    const joined = formatWeekdayList(labels);
    if (interval <= 1) return `Repeats every ${joined} starting ${startsIso}`;
    return `Repeats every ${interval} weeks on ${joined} starting ${startsIso}`;
  }
  // MONTHLY
  if (interval <= 1) return `Repeats every month starting ${startsIso}`;
  return `Repeats every ${interval} months starting ${startsIso}`;
}

function formatWeekdayList(labels: ReadonlyArray<string>): string {
  if (labels.length === 0) return "";
  if (labels.length === 1) return labels[0]!;
  if (labels.length === 2) return `${labels[0]} and ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")}, and ${labels[labels.length - 1]}`;
}

/**
 * Parse an RRULE spec back to its components. Returns null when the
 * input is not in our supported subset (any unknown FREQ, missing FREQ,
 * malformed INTERVAL, unknown BYDAY token).
 */
export function parseRruleSpec(spec: string): {
  readonly freq: RecurrenceFreq;
  readonly interval: number;
  readonly byday: ReadonlyArray<Byday>;
} | null {
  let freq: RecurrenceFreq | null = null;
  let interval = 1;
  let byday: ReadonlyArray<Byday> = [];
  for (const segment of spec.split(";")) {
    if (segment.length === 0) continue;
    const eq = segment.indexOf("=");
    if (eq === -1) return null;
    const key = segment.slice(0, eq);
    const val = segment.slice(eq + 1);
    if (key === "FREQ") {
      if (val !== "DAILY" && val !== "WEEKLY" && val !== "MONTHLY") return null;
      freq = val;
    } else if (key === "INTERVAL") {
      const n = Number.parseInt(val, 10);
      if (!Number.isFinite(n) || n < 1) return null;
      interval = n;
    } else if (key === "BYDAY") {
      const tokens = val.split(",");
      const out: Array<Byday> = [];
      for (const t of tokens) {
        if (!(BYDAY_CODES as ReadonlyArray<string>).includes(t)) return null;
        out.push(t as Byday);
      }
      byday = out;
    } else {
      // Unknown segment — out of our subset.
      return null;
    }
  }
  if (freq === null) return null;
  return { freq, interval, byday };
}

function parseEveryNDays(spec: string): number | null {
  if (!spec.startsWith("every_N_days:")) return null;
  const n = Number.parseInt(spec.slice("every_N_days:".length), 10);
  if (!Number.isFinite(n) || n < 1) return null;
  return n;
}

// ===========================================================================
// Component
// ===========================================================================

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";

export interface RecurrenceEditorProps {
  readonly value: TodoRecurrence | null;
  readonly onChange: (next: TodoRecurrence | null) => void;
  /**
   * Date used to render the "starting …" preview suffix. Defaults to
   * `new Date()`. Tests pass a pinned value.
   */
  readonly today?: Date;
}

/**
 * Read the editor's "draft" view from a `TodoRecurrence` value, falling
 * back to sane defaults when the value is null or in a non-rrule branch.
 * Pure — never reads the clock.
 */
function deriveDraft(value: TodoRecurrence | null): {
  readonly freq: RecurrenceFreq;
  readonly interval: number;
  readonly byday: ReadonlyArray<Byday>;
} {
  if (value === null) {
    return { freq: "DAILY", interval: 1, byday: [] };
  }
  if (value.rule === "rrule") {
    const parsed = parseRruleSpec(value.spec);
    if (parsed !== null) return parsed;
    return { freq: "DAILY", interval: 1, byday: [] };
  }
  if (value.rule === "every_N_days") {
    return {
      freq: "DAILY",
      interval: parseEveryNDays(value.spec) ?? 1,
      byday: [],
    };
  }
  // every_weekday — present BYDAY M-F as the underlying picker view.
  return {
    freq: "WEEKLY",
    interval: 1,
    byday: ["MO", "TU", "WE", "TH", "FR"],
  };
}

export function RecurrenceEditor(props: RecurrenceEditorProps): ReactElement {
  const { value, onChange, today = new Date() } = props;

  const draft = useMemo(() => deriveDraft(value), [value]);
  const enabled = value !== null;

  const emit = useCallback(
    (next: {
      freq: RecurrenceFreq;
      interval: number;
      byday: ReadonlyArray<Byday>;
    }) => {
      onChange({
        rule: "rrule",
        spec: buildRruleSpec(next),
      });
    },
    [onChange],
  );

  const setFreq = useCallback(
    (freq: RecurrenceFreq) => {
      // Switching away from weekly drops BYDAY; switching back starts empty.
      emit({
        freq,
        interval: draft.interval,
        byday: freq === "WEEKLY" ? draft.byday : [],
      });
    },
    [emit, draft.interval, draft.byday],
  );

  const setInterval = useCallback(
    (interval: number) => {
      const clamped = Number.isFinite(interval)
        ? Math.max(1, Math.floor(interval))
        : 1;
      emit({ freq: draft.freq, interval: clamped, byday: draft.byday });
    },
    [emit, draft.freq, draft.byday],
  );

  const toggleByday = useCallback(
    (code: Byday) => {
      const has = draft.byday.includes(code);
      const next = has
        ? draft.byday.filter((d) => d !== code)
        : [...draft.byday, code];
      emit({ freq: draft.freq, interval: draft.interval, byday: next });
    },
    [emit, draft.freq, draft.interval, draft.byday],
  );

  // ---- Quick shortcuts -----------------------------------------------------

  const applyEveryDay = useCallback(() => {
    onChange({ rule: "every_N_days", spec: "every_N_days:1" });
  }, [onChange]);

  const applyEveryWeekday = useCallback(() => {
    onChange({ rule: "every_weekday", spec: "every_weekday" });
  }, [onChange]);

  const applyEveryMonday = useCallback(() => {
    onChange({
      rule: "rrule",
      spec: buildRruleSpec({
        freq: "WEEKLY",
        interval: 1,
        byday: ["MO"],
      }),
    });
  }, [onChange]);

  const applyEveryNDays = useCallback(
    (n: number) => {
      const clamped = Number.isFinite(n) ? Math.max(1, Math.floor(n)) : 1;
      onChange({
        rule: "every_N_days",
        spec: `every_N_days:${clamped}`,
      });
    },
    [onChange],
  );

  const clearRecurrence = useCallback(() => {
    onChange(null);
  }, [onChange]);

  // ---- Styles --------------------------------------------------------------

  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    padding: 14,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    color: TEXT_SECONDARY,
  };
  const selectStyle: CSSProperties = {
    height: 28,
    padding: "0 8px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
  };
  const intervalInputStyle: CSSProperties = {
    width: 56,
    height: 28,
    padding: "0 8px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
  };
  const previewStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_FAINT,
    fontStyle: "italic",
  };
  const shortcutButton = (active: boolean): CSSProperties => ({
    height: 26,
    padding: "0 10px",
    borderRadius: 999,
    border: `1px solid ${active ? ACCENT : PANEL_BORDER_STRONG}`,
    backgroundColor: active
      ? "var(--color-accent-soft, transparent)"
      : "transparent",
    color: active ? ACCENT : TEXT_SECONDARY,
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    cursor: "pointer",
  });

  // ---- Disabled (no recurrence) shell -------------------------------------

  if (!enabled) {
    return (
      <div
        style={wrapperStyle}
        data-testid="recurrence-editor"
        data-state="disabled"
      >
        <div style={rowStyle}>
          <span style={labelStyle}>Repeat</span>
          <button
            type="button"
            onClick={applyEveryDay}
            style={shortcutButton(false)}
            data-testid="recurrence-shortcut-every-day"
          >
            every day
          </button>
          <button
            type="button"
            onClick={applyEveryWeekday}
            style={shortcutButton(false)}
            data-testid="recurrence-shortcut-every-weekday"
          >
            every weekday
          </button>
          <button
            type="button"
            onClick={applyEveryMonday}
            style={shortcutButton(false)}
            data-testid="recurrence-shortcut-every-monday"
          >
            every Monday
          </button>
        </div>
        <div style={{ ...previewStyle, color: TEXT_SECONDARY }}>
          Does not repeat
        </div>
      </div>
    );
  }

  // ---- Enabled view --------------------------------------------------------

  const bydayRow: ReactElement | null =
    draft.freq === "WEEKLY" ? (
      <div style={rowStyle} role="group" aria-label="Repeat on days">
        <span style={labelStyle}>On</span>
        {BYDAY_CODES.map((code) => {
          const active = draft.byday.includes(code);
          const dayButton: CSSProperties = {
            width: 28,
            height: 28,
            padding: 0,
            borderRadius: 999,
            border: `1px solid ${active ? ACCENT : PANEL_BORDER_STRONG}`,
            backgroundColor: active ? ACCENT : "transparent",
            color: active ? "var(--color-bg)" : TEXT_PRIMARY,
            fontSize: "var(--font-size-xs)",
            fontWeight: 600,
            cursor: "pointer",
          };
          return (
            <button
              key={code}
              type="button"
              role="checkbox"
              aria-checked={active}
              aria-label={`${BYDAY_LONG[code]} (${code})`}
              onClick={() => toggleByday(code)}
              style={dayButton}
              data-testid={`recurrence-byday-${code}`}
              data-active={active ? "true" : "false"}
            >
              {BYDAY_LABEL[code]}
            </button>
          );
        })}
      </div>
    ) : null;

  return (
    <div
      style={wrapperStyle}
      data-testid="recurrence-editor"
      data-state="enabled"
      data-rule={value.rule}
    >
      <div style={rowStyle}>
        <span style={labelStyle}>Repeat</span>
        <button
          type="button"
          onClick={applyEveryDay}
          style={shortcutButton(
            value.rule === "every_N_days" && parseEveryNDays(value.spec) === 1,
          )}
          data-testid="recurrence-shortcut-every-day"
        >
          every day
        </button>
        <button
          type="button"
          onClick={applyEveryWeekday}
          style={shortcutButton(value.rule === "every_weekday")}
          data-testid="recurrence-shortcut-every-weekday"
        >
          every weekday
        </button>
        <button
          type="button"
          onClick={applyEveryMonday}
          style={shortcutButton(
            value.rule === "rrule" &&
              draft.freq === "WEEKLY" &&
              draft.interval === 1 &&
              draft.byday.length === 1 &&
              draft.byday[0] === "MO",
          )}
          data-testid="recurrence-shortcut-every-monday"
        >
          every Monday
        </button>
        <button
          type="button"
          onClick={() => applyEveryNDays(3)}
          style={shortcutButton(false)}
          data-testid="recurrence-shortcut-every-n-days"
        >
          every 3 days
        </button>
        <button
          type="button"
          onClick={clearRecurrence}
          style={shortcutButton(false)}
          data-testid="recurrence-clear"
          aria-label="Do not repeat"
        >
          off
        </button>
      </div>

      <div style={rowStyle}>
        <span style={labelStyle}>Frequency</span>
        <select
          value={draft.freq}
          onChange={(e) => setFreq(e.target.value as RecurrenceFreq)}
          style={selectStyle}
          aria-label="Repeat frequency"
          data-testid="recurrence-freq"
        >
          <option value="DAILY">Daily</option>
          <option value="WEEKLY">Weekly</option>
          <option value="MONTHLY">Monthly</option>
        </select>
        <span style={labelStyle}>every</span>
        <input
          type="number"
          min={1}
          step={1}
          value={draft.interval}
          onChange={(e) => setInterval(Number.parseInt(e.target.value, 10))}
          style={intervalInputStyle}
          aria-label="Repeat interval"
          data-testid="recurrence-interval"
        />
        <span style={labelStyle}>
          {draft.freq === "DAILY"
            ? draft.interval === 1
              ? "day"
              : "days"
            : draft.freq === "WEEKLY"
              ? draft.interval === 1
                ? "week"
                : "weeks"
              : draft.interval === 1
                ? "month"
                : "months"}
        </span>
      </div>

      {bydayRow}

      <div
        style={previewStyle}
        data-testid="recurrence-preview"
        aria-live="polite"
      >
        {previewRecurrence(value, today)}
      </div>
    </div>
  );
}
