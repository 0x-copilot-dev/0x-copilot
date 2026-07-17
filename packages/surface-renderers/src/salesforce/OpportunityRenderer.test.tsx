import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { clearRegistry, resolveAdapter } from "@0x-copilot/chat-surface";

import {
  opportunityAdapter,
  OpportunityRenderer,
  type SalesforceOpportunity,
} from "./OpportunityRenderer";
import { registerSalesforceAdapter } from "./index";

const ACME_OPPORTUNITY: SalesforceOpportunity = {
  id: "006XYZ",
  name: "Acme — Platform Renewal FY26",
  account: "Acme Corp",
  stage: "Negotiation",
  closeDate: "2026-09-30",
  arr: "$420,000",
  owner: "Sarah Chen",
  customFields: [
    {
      key: "renewal_path",
      label: "Renewal Path",
      value: "Multi-year, locked price",
    },
    { key: "champion", label: "Champion", value: "Marcus Vega" },
  ],
};

describe("opportunityAdapter contract", () => {
  it("declares scheme 'sf-opp'", () => {
    expect(opportunityAdapter.scheme).toBe("sf-opp");
  });

  it("matches sf-opp:// URIs", () => {
    expect(opportunityAdapter.matches("sf-opp://acme/006XYZ")).toBe(true);
    expect(opportunityAdapter.matches("sf-opp://")).toBe(true);
  });

  it("does not match other schemes", () => {
    expect(opportunityAdapter.matches("email://draft-1")).toBe(false);
    expect(opportunityAdapter.matches("sheet-row://sf/1")).toBe(false);
    expect(opportunityAdapter.matches("slide://deck/1")).toBe(false);
    expect(opportunityAdapter.matches("")).toBe(false);
  });

  it("is first-party origin at schemaVersion 1", () => {
    expect(opportunityAdapter.metadata.origin).toBe("first-party");
    expect(opportunityAdapter.metadata.schemaVersion).toBe(1);
  });

  it("exposes renderCurrent and renderDiff as functions", () => {
    expect(typeof opportunityAdapter.renderCurrent).toBe("function");
    expect(typeof opportunityAdapter.renderDiff).toBe("function");
  });
});

describe("OpportunityRenderer (renderCurrent)", () => {
  it("renders the five standard fields with labels and values", () => {
    render(OpportunityRenderer(ACME_OPPORTUNITY));
    expect(screen.getByTestId("sf-field-account-value")).toHaveTextContent(
      "Acme Corp",
    );
    expect(screen.getByTestId("sf-field-stage-value")).toHaveTextContent(
      "Negotiation",
    );
    expect(screen.getByTestId("sf-field-closeDate-value")).toHaveTextContent(
      "2026-09-30",
    );
    expect(screen.getByTestId("sf-field-arr-value")).toHaveTextContent(
      "$420,000",
    );
    expect(screen.getByTestId("sf-field-owner-value")).toHaveTextContent(
      "Sarah Chen",
    );
  });

  it("renders the opportunity name and id in the header", () => {
    render(OpportunityRenderer(ACME_OPPORTUNITY));
    expect(screen.getByTestId("sf-opportunity-id")).toHaveTextContent("006XYZ");
    expect(
      screen.getByText("Acme — Platform Renewal FY26"),
    ).toBeInTheDocument();
  });

  it("renders standard fields followed by custom fields in input order", () => {
    render(OpportunityRenderer(ACME_OPPORTUNITY));
    const renderer = screen.getByTestId("sf-opportunity-renderer");
    const rows = renderer.querySelectorAll('[data-testid^="sf-field-"]');
    const fieldTestIds = Array.from(rows)
      .map((el) => el.getAttribute("data-testid"))
      .filter((id) => id !== null && !id.endsWith("-value"));
    expect(fieldTestIds).toEqual([
      "sf-field-account",
      "sf-field-stage",
      "sf-field-closeDate",
      "sf-field-arr",
      "sf-field-owner",
      "sf-field-renewal_path",
      "sf-field-champion",
    ]);
  });

  it("renders custom field values through the same field row", () => {
    render(OpportunityRenderer(ACME_OPPORTUNITY));
    expect(screen.getByTestId("sf-field-renewal_path-value")).toHaveTextContent(
      "Multi-year, locked price",
    );
    expect(screen.getByTestId("sf-field-champion-value")).toHaveTextContent(
      "Marcus Vega",
    );
  });

  it("handles an empty customFields array without crashing", () => {
    render(OpportunityRenderer({ ...ACME_OPPORTUNITY, customFields: [] }));
    expect(screen.getByTestId("sf-opportunity-renderer")).toBeInTheDocument();
    expect(screen.getByTestId("sf-field-account-value")).toHaveTextContent(
      "Acme Corp",
    );
  });

  it("renders unknown / arbitrary custom-field keys through the generic field row", () => {
    const exoticOpportunity: SalesforceOpportunity = {
      ...ACME_OPPORTUNITY,
      customFields: [
        {
          key: "ext_xyz__sla_tier",
          label: "SLA Tier (custom)",
          value: "Platinum",
        },
        {
          key: "wholly_unknown_field",
          label: "Wholly Unknown",
          value: "value-99",
        },
      ],
    };
    render(OpportunityRenderer(exoticOpportunity));
    expect(
      screen.getByTestId("sf-field-ext_xyz__sla_tier-value"),
    ).toHaveTextContent("Platinum");
    expect(
      screen.getByTestId("sf-field-wholly_unknown_field-value"),
    ).toHaveTextContent("value-99");
  });

  it("renders mode='current' on the root element", () => {
    render(OpportunityRenderer(ACME_OPPORTUNITY));
    expect(screen.getByTestId("sf-opportunity-renderer")).toHaveAttribute(
      "data-mode",
      "current",
    );
  });
});

describe("registerSalesforceAdapter", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("registers the salesforce adapter under sf-opp", () => {
    registerSalesforceAdapter();
    const resolved = resolveAdapter("sf-opp://acme/006XYZ");
    expect(resolved).not.toBeNull();
    expect(resolved?.scheme).toBe("sf-opp");
  });

  it("is idempotent within the same schemaVersion", () => {
    registerSalesforceAdapter();
    registerSalesforceAdapter();
    const resolved = resolveAdapter("sf-opp://acme/006XYZ");
    expect(resolved).not.toBeNull();
    expect(resolved?.scheme).toBe("sf-opp");
  });

  it("does not register under any other scheme", () => {
    registerSalesforceAdapter();
    expect(resolveAdapter("email://draft-1")).toBeNull();
    expect(resolveAdapter("sheet-row://sf/1")).toBeNull();
  });
});
