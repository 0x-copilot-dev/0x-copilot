import type { SurfaceFieldFormat } from "./specTypes";

// Pure, defensive value access + presentation helpers for the ArchetypeRenderer
// pack (PRD-03). No I/O, no globals — safe under D28. Every accessor returns a
// benign value on any miss instead of throwing; the boundary state is `unknown`.

/** Longest string we ever paint into the DOM. Hostile 10k-char blobs are
 * truncated here (PRD-03 AC3) so a single field can never blow the render. */
export const MAX_DISPLAY_CHARS = 2000;

function truncate(value: string): string {
  return value.length > MAX_DISPLAY_CHARS
    ? `${value.slice(0, MAX_DISPLAY_CHARS)}…`
    : value;
}

/**
 * Resolve a dotted accessor (`"a.b.0.c"`) against JSON-parsed tool output.
 * Identifier segments read object keys; all-digit segments index arrays (or
 * numeric-keyed objects). Returns `undefined` on any miss — a wrong path, a
 * primitive mid-traversal, a null hole — never throws. Iterative, so 20-level
 * nesting costs no stack.
 */
export function resolvePath(data: unknown, path: string): unknown {
  if (typeof path !== "string" || path.length === 0) {
    return undefined;
  }
  let current: unknown = data;
  for (const segment of path.split(".")) {
    if (current === null || current === undefined) {
      return undefined;
    }
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(segment)) {
        return undefined;
      }
      current = current[Number(segment)];
      continue;
    }
    if (typeof current === "object") {
      current = (current as Record<string, unknown>)[segment];
      continue;
    }
    // A primitive with segments still to consume — dead end.
    return undefined;
  }
  return current;
}

/**
 * Turn a resolved value into a display string honouring the (purely visual)
 * `format` hint. Locale-safe via `Intl`; unparseable numbers/dates fall back to
 * the raw string. Objects are JSON-stringified rather than rendered as
 * `[object Object]`. Always length-capped.
 */
export function formatValue(
  value: unknown,
  format?: SurfaceFieldFormat,
): string {
  if (value === null || value === undefined) {
    return "";
  }
  switch (format) {
    case "number":
    case "currency": {
      const numeric = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(numeric)) {
        return truncate(stringify(value));
      }
      if (format === "currency") {
        return new Intl.NumberFormat(undefined, {
          style: "currency",
          currency: "USD",
        }).format(numeric);
      }
      return new Intl.NumberFormat(undefined).format(numeric);
    }
    case "datetime": {
      if (typeof value !== "string" && typeof value !== "number") {
        return truncate(stringify(value));
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return truncate(stringify(value));
      }
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
    }
    case "text":
    case "badge":
    case "user":
    default:
      return truncate(stringify(value));
  }
}

function stringify(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value) ?? "";
    } catch {
      return "";
    }
  }
  return String(value);
}

/** True only for `http(s)://…` strings. Everything else (incl.
 * `javascript:`, `data:`, non-strings) renders as inert text — PRD-03 AC3. */
export function isSafeHttpUrl(value: unknown): value is string {
  return typeof value === "string" && /^https?:\/\//i.test(value);
}

/** `true` when the format hint should paint with tabular figures. */
export function isNumericFormat(format?: SurfaceFieldFormat): boolean {
  return format === "number" || format === "currency";
}

/**
 * The finite number behind a resolved cell value, or `null`.
 *
 * Deliberately stricter than `Number(value)`: booleans, empty strings, and
 * whitespace all coerce to a number in JS, and letting `""` become `0` would
 * paint a real magnitude bar for a missing cell. A magnitude channel that lies
 * about absent data is worse than no channel.
 */
export function numericValue(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * A string that carries a digit but does not parse — "1,234", "$1.2k",
 * "21,850 USDC". That is a magnitude we FAILED to read, not an empty cell, and
 * the difference decides whether a column may be scaled at all.
 */
function isUnreadableNumber(value: unknown): boolean {
  return (
    typeof value === "string" &&
    /\d/.test(value) &&
    numericValue(value) === null
  );
}

/**
 * Each row's share of the largest magnitude in a numeric column, as a 0–1
 * fraction, for the value bars behind numeric cells.
 *
 * Returns all-`null` — meaning "paint no bars" — in the cases where a bar would
 * mislead rather than inform:
 *
 *  - fewer than two comparable values: a lone full-width bar states a
 *    comparison that does not exist;
 *  - every value identical: a column of full bars is pure noise;
 *  - a non-finite or zero maximum: nothing to be a share of.
 *
 * Magnitude is absolute, so a column mixing +1000 and -1000 scales both to the
 * same length. That is honest about SIZE, which is what a bar encodes; the sign
 * stays legible in the number itself, which is never obscured.
 */
export function magnitudeShares(
  values: readonly unknown[],
): readonly (number | null)[] {
  const numbers = values.map(numericValue);
  // A column containing a number we could not READ is not the same as a column
  // containing empty cells, and treating them alike inverts the ranking: with
  // ["1,234", "987", "2,500", "555"] only the two bare values parse, so 987 —
  // the SMALLEST number in the column — is painted as the maximum and the two
  // largest get no bar at all. The bars would then rank rows by parseability
  // rather than by size, which is precisely the lie this channel must not tell.
  //
  // Suppressing the whole column is the honest response. Stripping separators
  // instead is not: "1.234" is 1234 in de-DE and 1.234 in en-US, so a stripper
  // would silently misread European-formatted output — a quieter version of the
  // same defect. Reading such a column correctly requires the payload's locale,
  // which lives upstream of here.
  if (values.some(isUnreadableNumber)) {
    return numbers.map(() => null);
  }
  const present = numbers.filter((value): value is number => value !== null);
  if (present.length < 2) {
    return numbers.map(() => null);
  }
  const magnitudes = present.map(Math.abs);
  const max = Math.max(...magnitudes);
  if (!Number.isFinite(max) || max === 0) {
    return numbers.map(() => null);
  }
  if (Math.min(...magnitudes) === max) {
    return numbers.map(() => null);
  }
  return numbers.map((value) =>
    value === null ? null : Math.abs(value) / max,
  );
}
