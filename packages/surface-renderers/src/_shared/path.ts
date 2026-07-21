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
