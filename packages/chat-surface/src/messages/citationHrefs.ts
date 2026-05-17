// Single source of truth for the citation-href contract.
//
// Three things live here so they can never drift:
//   1. The prefix CONSTANTS — what an emitted citation href looks like.
//   2. The PREDICATES — given an href, is it ours?
//   3. The PARSERS — given an href, extract the id / ordinal payload.
//
// The remark plugin (`citationRemarkPlugin`) imports the prefixes to
// emit hrefs; the dispatcher (`MarkdownLink`) imports the predicates and
// parsers to route hrefs to the right chip; chip components in
// `apps/frontend` import the prefix when they need to round-trip an id.
// One file, one definition each — rename in one place and the whole
// system follows.

export const CITATION_HREF_PREFIX = "#cite:";
export const CITATION_ORDINAL_HREF_PREFIX = "#cite-ord:";

/** True when `href` is a legacy `#cite:<id>` link emitted by the plugin. */
export function isCitationHref(href: string | undefined): boolean {
  return typeof href === "string" && href.startsWith(CITATION_HREF_PREFIX);
}

/** Extract the id from `#cite:<id>`, or `null` if the href is malformed. */
export function citationIdFromHref(href: string): string | null {
  if (!href.startsWith(CITATION_HREF_PREFIX)) {
    return null;
  }
  const id = href.slice(CITATION_HREF_PREFIX.length);
  return id || null;
}

/** True when `href` is an ordinal `#cite-ord:<n>` link emitted by the plugin. */
export function isOrdinalCitationHref(href: string | undefined): boolean {
  return (
    typeof href === "string" && href.startsWith(CITATION_ORDINAL_HREF_PREFIX)
  );
}

/**
 * Extract the ordinal from `#cite-ord:<n>`. Returns `null` for malformed
 * hrefs (empty, non-integer, non-canonical leading zeros, non-positive).
 * Strict on round-trip — `String(value) !== raw` rejects `007` but
 * accepts `7`, matching what the emitter writes.
 */
export function ordinalFromHref(href: string): number | null {
  if (!href.startsWith(CITATION_ORDINAL_HREF_PREFIX)) {
    return null;
  }
  const raw = href.slice(CITATION_ORDINAL_HREF_PREFIX.length);
  if (raw.length === 0) {
    return null;
  }
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value <= 0 || String(value) !== raw) {
    return null;
  }
  return value;
}
