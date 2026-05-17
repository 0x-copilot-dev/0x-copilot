import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  opportunityAdapter,
  OpportunityDiffRenderer,
  type SalesforceOpportunity,
  type SalesforceOpportunityDiff,
} from "./OpportunityRenderer";

const BASE_OPPORTUNITY: SalesforceOpportunity = {
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
  ],
};

describe("OpportunityDiffRenderer", () => {
  it("renders provenance pill on each changed field", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-1",
      opportunity: BASE_OPPORTUNITY,
      changes: [
        {
          key: "stage",
          label: "Stage",
          previousValue: "Negotiation",
          nextValue: "Verbal Commit",
          provenance: "DRAFTED FROM Q4 SHEET",
        },
        {
          key: "arr",
          label: "ARR",
          previousValue: "$420,000",
          nextValue: "$480,000",
          provenance: "DRAFTED FROM Q4 SHEET",
        },
      ],
    };
    render(OpportunityDiffRenderer(diff));
    expect(screen.getByTestId("sf-field-stage-provenance")).toHaveTextContent(
      "DRAFTED FROM Q4 SHEET",
    );
    expect(screen.getByTestId("sf-field-arr-provenance")).toHaveTextContent(
      "DRAFTED FROM Q4 SHEET",
    );
  });

  it("shows previous and next values for each changed field", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-2",
      opportunity: BASE_OPPORTUNITY,
      changes: [
        {
          key: "stage",
          label: "Stage",
          previousValue: "Negotiation",
          nextValue: "Closed Won",
          provenance: "DRAFTED FROM CALL TRANSCRIPT",
        },
      ],
    };
    render(OpportunityDiffRenderer(diff));
    expect(screen.getByTestId("sf-field-stage-previous")).toHaveTextContent(
      "Negotiation",
    );
    expect(screen.getByTestId("sf-field-stage-next")).toHaveTextContent(
      "Closed Won",
    );
  });

  it("flags changed rows with data-changed and leaves others alone", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-3",
      opportunity: BASE_OPPORTUNITY,
      changes: [
        {
          key: "owner",
          label: "Owner",
          previousValue: "Sarah Chen",
          nextValue: "Marcus Vega",
          provenance: "OWNERSHIP TRANSFER",
        },
      ],
    };
    render(OpportunityDiffRenderer(diff));
    expect(screen.getByTestId("sf-field-owner")).toHaveAttribute(
      "data-changed",
      "true",
    );
    expect(screen.getByTestId("sf-field-account")).not.toHaveAttribute(
      "data-changed",
    );
    expect(screen.getByTestId("sf-field-stage")).not.toHaveAttribute(
      "data-changed",
    );
    expect(
      screen.queryByTestId("sf-field-account-provenance"),
    ).not.toBeInTheDocument();
  });

  it("renders a custom-field change through the generic row", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-4",
      opportunity: BASE_OPPORTUNITY,
      changes: [
        {
          key: "renewal_path",
          label: "Renewal Path",
          previousValue: "Multi-year, locked price",
          nextValue: "Single-year, ramped",
          provenance: "DRAFTED FROM MSA §3.2",
        },
      ],
    };
    render(OpportunityDiffRenderer(diff));
    expect(
      screen.getByTestId("sf-field-renewal_path-provenance"),
    ).toHaveTextContent("DRAFTED FROM MSA §3.2");
    expect(
      screen.getByTestId("sf-field-renewal_path-previous"),
    ).toHaveTextContent("Multi-year, locked price");
    expect(screen.getByTestId("sf-field-renewal_path-next")).toHaveTextContent(
      "Single-year, ramped",
    );
  });

  it("renders an unknown / arbitrary custom-field change through the generic row", () => {
    const exoticOpportunity: SalesforceOpportunity = {
      ...BASE_OPPORTUNITY,
      customFields: [
        {
          key: "ext_xyz__sla_tier",
          label: "SLA Tier (custom)",
          value: "Gold",
        },
      ],
    };
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-5",
      opportunity: exoticOpportunity,
      changes: [
        {
          key: "ext_xyz__sla_tier",
          label: "SLA Tier (custom)",
          previousValue: "Gold",
          nextValue: "Platinum",
          provenance: "DRAFTED FROM RENEWAL DECK",
        },
      ],
    };
    render(OpportunityDiffRenderer(diff));
    expect(
      screen.getByTestId("sf-field-ext_xyz__sla_tier-provenance"),
    ).toHaveTextContent("DRAFTED FROM RENEWAL DECK");
    expect(
      screen.getByTestId("sf-field-ext_xyz__sla_tier-next"),
    ).toHaveTextContent("Platinum");
  });

  it("renders mode='diff' on the root element", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-6",
      opportunity: BASE_OPPORTUNITY,
      changes: [],
    };
    render(OpportunityDiffRenderer(diff));
    expect(screen.getByTestId("sf-opportunity-renderer")).toHaveAttribute(
      "data-mode",
      "diff",
    );
  });

  it("invokes the adapter's renderDiff (contract path)", () => {
    const diff: SalesforceOpportunityDiff = {
      diffId: "diff-7",
      opportunity: BASE_OPPORTUNITY,
      changes: [
        {
          key: "stage",
          label: "Stage",
          previousValue: "Negotiation",
          nextValue: "Closed Won",
          provenance: "PROVENANCE",
        },
      ],
    };
    render(opportunityAdapter.renderDiff(diff));
    expect(screen.getByTestId("sf-opportunity-renderer")).toBeInTheDocument();
    expect(screen.getByTestId("sf-field-stage")).toHaveAttribute(
      "data-changed",
      "true",
    );
  });
});
