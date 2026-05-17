// Source freshness label heuristic.
//
// Live connectors (Salesforce, Snowflake, Datadog, Intercom, PagerDuty)
// return "current as of read-time" — there is no document modification
// time the way Notion / Drive have one. The source row footer flips to
// "Live data" for these so users understand the temporal contract of
// each citation without reading a manual.
//
// v1 uses a slug heuristic; a future `freshness_kind: "live" | "snapshot"`
// field on `CitationSourceRef` makes it explicit (PR 3.1 follow-up).

const LIVE_CONNECTOR_SLUGS = new Set<string>([
  "salesforce",
  "snowflake",
  "datadog",
  "intercom",
  "pagerduty",
]);

/** True when the connector serves "live" data (no stable last_modified_at). */
export function isLiveConnector(slug: string | null | undefined): boolean {
  if (!slug) {
    return false;
  }
  return LIVE_CONNECTOR_SLUGS.has(slug.toLowerCase());
}

/** Human-readable freshness label for a source row. */
export function sourceFreshnessLabel(input: {
  connectorSlug: string | null | undefined;
  freshnessAt: string | null | undefined;
  lastCitedAt?: string | null;
}): string {
  if (isLiveConnector(input.connectorSlug)) {
    return "Live data";
  }
  if (input.freshnessAt) {
    return `Updated ${formatRelative(input.freshnessAt)}`;
  }
  if (input.lastCitedAt) {
    return `Last cited ${formatRelative(input.lastCitedAt)}`;
  }
  return "";
}

/** Best-effort relative time. Falls back to the raw ISO on bad input. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString();
}
