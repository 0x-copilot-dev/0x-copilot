import { describe, expect, it } from "vitest";
import { isLiveConnector, sourceFreshnessLabel } from "./sourceFreshness";

describe("isLiveConnector", () => {
  it.each(["salesforce", "snowflake", "datadog", "intercom", "pagerduty"])(
    "%s is live",
    (slug) => {
      expect(isLiveConnector(slug)).toBe(true);
    },
  );

  it.each(["notion", "drive", "slack", "github", "linear"])(
    "%s is not live",
    (slug) => {
      expect(isLiveConnector(slug)).toBe(false);
    },
  );

  it("returns false on null/undefined/empty", () => {
    expect(isLiveConnector(null)).toBe(false);
    expect(isLiveConnector(undefined)).toBe(false);
    expect(isLiveConnector("")).toBe(false);
  });

  it("normalises case", () => {
    expect(isLiveConnector("SALESFORCE")).toBe(true);
  });
});

describe("sourceFreshnessLabel", () => {
  it("renders Live data for live connectors regardless of timestamps", () => {
    expect(
      sourceFreshnessLabel({
        connectorSlug: "salesforce",
        freshnessAt: "2026-05-04T10:00:00Z",
      }),
    ).toBe("Live data");
  });

  it("renders Updated <when> for snapshot connectors with freshness_at", () => {
    const label = sourceFreshnessLabel({
      connectorSlug: "notion",
      freshnessAt: "2026-05-04T10:00:00Z",
    });
    expect(label).toMatch(/^Updated /);
  });

  it("falls back to Last cited <when> when freshness_at is missing", () => {
    const label = sourceFreshnessLabel({
      connectorSlug: "notion",
      freshnessAt: null,
      lastCitedAt: "2026-05-04T10:00:00Z",
    });
    expect(label).toMatch(/^Last cited /);
  });

  it("returns empty string when nothing is known", () => {
    expect(
      sourceFreshnessLabel({
        connectorSlug: null,
        freshnessAt: null,
      }),
    ).toBe("");
  });
});
