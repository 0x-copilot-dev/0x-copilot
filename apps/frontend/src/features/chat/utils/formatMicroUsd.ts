/**
 * Single source of truth for rendering micro-USD integers as user-facing
 * dollar strings. Used by /usage, /context, and any future cost surface
 * — never re-implemented inline. Locale-aware via `Intl.NumberFormat`
 * with the browser default.
 *
 * Math is integer-only on the conversion: we divide by 1_000_000 once
 * to feed `Intl.NumberFormat`, which does its own deterministic rounding
 * to the requested fraction digits. No double-rounding drift.
 */

const MICRO_PER_USD = 1_000_000;

const _formatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const _preciseFormatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

export function formatMicroUsd(
  value: number | null | undefined,
  options: { precise?: boolean } = {},
): string {
  if (value === null || value === undefined) {
    return "—";
  }
  const dollars = value / MICRO_PER_USD;
  return options.precise
    ? _preciseFormatter.format(dollars)
    : _formatter.format(dollars);
}
