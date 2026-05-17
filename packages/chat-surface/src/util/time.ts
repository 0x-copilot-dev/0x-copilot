// Single locale-aware relative-time formatter for every destination.
//
// Source: cross-audit.md §3.4 (binding 2026-05-17). This file is the
// SOLE definition of `formatRelativeTime`; every prior copy (Home,
// Library, Inbox, Projects) is replaced by an import from here.
//
// The `now` parameter is explicit (not implicit `Date.now()`) so tests
// can pin time without monkey-patching `Date`. The default fills in
// `Date.now()` at call time when callers don't care.

/**
 * Format an ISO-8601 timestamp as a human-readable relative phrase
 * (e.g. "just now", "5m ago", "3d ago"). Locale-aware via
 * `Intl.RelativeTimeFormat`.
 *
 * Returns `"—"` (em dash) when the input can't be parsed — keeps
 * the UI from collapsing on bad data and gives a clear visual signal.
 *
 * @param iso  ISO-8601 timestamp; e.g. `"2026-05-17T11:43:12Z"`.
 * @param now  Reference instant in epoch milliseconds; defaults to
 *             `Date.now()` evaluated at call time.
 * @param locale  BCP-47 locale tag; defaults to the runtime's locale.
 */
export function formatRelativeTime(
  iso: string,
  now: number = Date.now(),
  locale?: string,
): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "—";

  const diffMs = Math.max(0, now - parsed);
  if (diffMs < 60_000) return "just now";

  const rtf = new Intl.RelativeTimeFormat(locale ?? undefined, {
    numeric: "always",
    style: "narrow",
  });

  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return rtf.format(-minutes, "minute");

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return rtf.format(-hours, "hour");

  const days = Math.floor(hours / 24);
  if (days < 30) return rtf.format(-days, "day");

  const months = Math.floor(days / 30);
  if (months < 12) return rtf.format(-months, "month");

  const years = Math.floor(months / 12);
  return rtf.format(-years, "year");
}
