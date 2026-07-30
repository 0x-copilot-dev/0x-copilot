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
      // OWN properties only. A bare bracket read walks the prototype chain, so
      // a spec path of "constructor" printed `function Object() { [native code] }`
      // and "toString" printed its source — JS internals rendered as if they
      // were facts the tool returned. Specs are untrusted (`specFromState`
      // checks two fields, and the event projector merges payload state
      // verbatim), so the path is reachable from tool output.
      if (!Object.prototype.hasOwnProperty.call(current, segment)) {
        return undefined;
      }
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
      // The SAME test the magnitude bars use, deliberately. Splitting them made
      // one cell contradict itself: `Number("007")` is 7, so the value rendered
      // as "7" while `magnitudeShares` — which reads "007" as an identifier —
      // gave it no bar. Displaying a reformatted "7" also destroys the only
      // thing that made it recognisable as an order number.
      //
      // One definition of "is this a magnitude", used by what we print and by
      // what we measure. A value that is not one renders as it arrived.
      const numeric = numericValue(value);
      if (numeric === null) {
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
 * A number spelled the way a number is spelled: an optional minus, an integer
 * part with no leading zero, an optional fraction — digits required on at least
 * ONE side of the point, so ".5" and "12." are in — and an optional exponent.
 *
 * JSON's number grammar with those two lenient decimals added back. See
 * `numericValue` for why the line sits here and not exactly on JSON's.
 *
 * Linear, with no nested quantifier, so a hostile 10k-char cell costs one scan.
 * The two mantissa alternatives are disjoint on their first character — digit
 * versus `.` — so neither can be reached by backtracking out of the other.
 */
const NUMBER_SPELLING =
  /^-?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$/;

/**
 * The finite number behind a resolved cell value, or `null`.
 *
 * Deliberately stricter than `Number(value)` in two directions.
 *
 * Values that only coerce by accident: booleans, empty strings and whitespace
 * all become numbers in JS, and letting `""` become `0` would paint a real
 * magnitude bar for a missing cell. A magnitude channel that lies about absent
 * data is worse than no channel.
 *
 * Strings that are IDENTIFIERS rather than magnitudes: `Number()` reads "007"
 * as 7, "0x1F" as 31, "0b101" as 5, and "+14155550123" as fourteen billion. An
 * order number, a hex colour and a phone number have no size — scaling a bar to
 * them compares quantities that do not exist, and setting them in the tabular
 * figure register says they are meant to be read digit-column against
 * digit-column, which is what that register means and not what they are.
 *
 * The line is SPELLING, not value, and it runs between a mark a canonical
 * number does not carry and a character a canonical number could have dropped:
 *
 *  - an ADDED mark was added to say something, and what it says is never the
 *    size. A leading zero says how wide the field is padded, "0x"/"0b"/"0o" say
 *    which radix, a separator says which grouping locale. Refused, which keeps
 *    out "007", "0x1F", "0b101" and "1,234" while keeping "1000", "3.14",
 *    "-12.5", "-0" (genuinely zero) and "1e5" (genuinely scientific notation).
 *  - an OMITTED redundant zero says nothing at all. ".5" and "12." are the
 *    canonical spelling minus a character that carried no information; they
 *    have one reading and only one, and no identifier scheme elides — schemes
 *    pad. Accepted, and printed back as "0.5" and "12", which is the same
 *    magnitude and destroys nothing that identified the value.
 *
 * The minus is not an added mark: it IS how a negative number is spelled, by
 * `JSON.stringify` and by `Intl` alike.
 *
 * The leading `+` is the one place this is a bet rather than a reading, so it
 * is stated plainly. "+14155550123" is a phone number. "+12" is a delta, and it
 * is exactly what `Intl.NumberFormat(undefined, { signDisplay: "always" })`
 * emits, so machines really do produce it. Nothing local to the value separates
 * them — both are `+` then digits, and digit count is a guess about size, not a
 * difference in spelling. The near misses are worse than the bet: a
 * column-level rule ("all-`+` is phones, mixed signs are deltas") would break
 * the one thing `formatValue` and the bars share, a single definition applied
 * per value with no view of its neighbours; a carve-out for "+12.5" (no phone
 * number carries a point) would split one delta column across two registers,
 * printing some rows with their sign and some without.
 *
 * We refuse the whole spelling, and the cost is not one bar. The delta column
 * ["+12", "-4", "+30"] gets none at all, `isUnreadableNumber` taking the
 * in-grammar "-4" down with it. We take that over the alternative because
 * accepting the mark would ALSO print "+12" as "12", deleting the one mark that
 * made it a delta — the same harm as printing "007" as "7" — while a column of
 * phone numbers would be scaled, ranking rows by dialling code.
 */
export function numericValue(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string") {
    return null;
  }
  // Trimmed first because CSV cells carry padding (`", 42"` reaches us as
  // `" 42"`) and that is presentation whitespace, not a different value. The
  // grammar rejects "" and "   " on its own, so the empty cell needs no
  // separate guard.
  const trimmed = value.trim();
  if (!NUMBER_SPELLING.test(trimmed)) {
    return null;
  }
  const parsed = Number(trimmed);
  // Spelling is not enough: "1e400" is a well-formed number literal that
  // overflows to Infinity, and a bar of infinite length is not a bar.
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * A string that carries a digit but is not a magnitude we can read — "1,234",
 * "$1.2k", "21,850 USDC", and equally the identifier shapes "007" or "0x1F".
 * Either way it is not an empty cell, and the difference decides whether a
 * column may be scaled at all.
 *
 * Identifiers land here rather than counting as holes on purpose. Nothing in a
 * cell tells us whether a zero-padded string is an id or a padded quantity —
 * "0999999" could be either — so scoring it as a hole risks leaving what is
 * actually the column's largest value with no bar while smaller values get full
 * ones, the same inverted ranking the separator case produces. And when the
 * column is identifiers all the way down there is no magnitude in it to lose,
 * so suppressing it costs nothing.
 *
 * The blast radius runs the other way too, and `NUMBER_SPELLING` is written
 * knowing it: one member is enough to suppress the entire column, so a false
 * negative in the grammar never costs the one bar under that value — it costs
 * every bar beside it, including values spelled perfectly well.
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
