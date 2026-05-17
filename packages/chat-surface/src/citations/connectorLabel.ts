// Humanise connector slugs for the Sources tab + chip tooltips.
//
// Connector slugs come from the runtime untouched (`web_search`,
// `notion`, `google_drive`, `pagerduty-incidents`). Showing them raw in
// chrome reads as engineering jargon. This helper turns the slug into
// "Web search", "Notion", "Google drive", "Pagerduty incidents" with a
// memoised lookup so render paths stay cheap.

const CACHE = new Map<string, string>();

export function humanizeConnector(slug: string): string {
  const cached = CACHE.get(slug);
  if (cached !== undefined) {
    return cached;
  }
  const trimmed = slug.trim();
  if (trimmed.length === 0) {
    CACHE.set(slug, slug);
    return slug;
  }
  const spaced = trimmed.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  const cased = spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
  CACHE.set(slug, cased);
  return cased;
}
