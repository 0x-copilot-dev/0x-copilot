/**
 * PR 7.2 — by-connector breakdown table tests.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ByConnectorTable,
  formatConnectorLabel,
} from "./UsageConversationView";

describe("formatConnectorLabel", () => {
  it("returns 'Unattributed' for the empty slug", () => {
    expect(formatConnectorLabel("")).toBe("Unattributed");
  });

  it("title-cases known slugs", () => {
    expect(formatConnectorLabel("slack")).toBe("Slack");
    expect(formatConnectorLabel("notion")).toBe("Notion");
  });
});

describe("ByConnectorTable", () => {
  it("returns null when there are no rows", () => {
    const { container } = render(
      <ByConnectorTable rows={[]} showCosts={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one row per connector + the unattributed bucket", () => {
    render(
      <ByConnectorTable
        rows={[
          {
            connector_slug: "",
            input: 10,
            output: 5,
            cached_input: 0,
            total: 15,
            runs_count: 1,
            cost_micro_usd: null,
          },
          {
            connector_slug: "slack",
            input: 20,
            output: 10,
            cached_input: 0,
            total: 30,
            runs_count: 1,
            cost_micro_usd: null,
          },
        ]}
        showCosts={false}
      />,
    );
    expect(screen.getByText("Unattributed")).toBeTruthy();
    expect(screen.getByText("Slack")).toBeTruthy();
  });

  it("renders the cost column only when showCosts is true", () => {
    render(
      <ByConnectorTable
        rows={[
          {
            connector_slug: "slack",
            input: 20,
            output: 10,
            cached_input: 0,
            total: 30,
            runs_count: 1,
            cost_micro_usd: 1_000_000,
          },
        ]}
        showCosts={true}
      />,
    );
    expect(screen.getByText("Cost")).toBeTruthy();
  });
});
