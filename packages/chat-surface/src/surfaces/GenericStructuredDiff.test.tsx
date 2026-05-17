import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  GenericStructuredDiff,
  registerGenericStructuredDiff,
  type GenericCurrentState,
  type GenericStructuredDiffPayload,
} from "./GenericStructuredDiff";
import { TIER3_SCHEME } from "./SaaSRendererAdapter";
import {
  clearRegistry,
  registerAdapter,
  resolveAdapter,
} from "./SurfaceRegistry";

afterEach(() => {
  clearRegistry();
});

describe("GenericStructuredDiff — adapter contract", () => {
  it("registers as the tier-3 wildcard", () => {
    expect(GenericStructuredDiff.scheme).toBe(TIER3_SCHEME);
    expect(GenericStructuredDiff.scheme).toBe("*");
  });

  it("matches every uri", () => {
    expect(GenericStructuredDiff.matches("anything://x")).toBe(true);
    expect(GenericStructuredDiff.matches("")).toBe(true);
    expect(GenericStructuredDiff.matches("hubspot-deal://abc")).toBe(true);
  });

  it("declares first-party origin and schemaVersion 1", () => {
    expect(GenericStructuredDiff.metadata.origin).toBe("first-party");
    expect(GenericStructuredDiff.metadata.schemaVersion).toBe(1);
  });

  it("registerGenericStructuredDiff installs into the registry", () => {
    registerGenericStructuredDiff();
    const resolved = resolveAdapter("totally-unknown-saas://abc");
    expect(resolved).toBe(GenericStructuredDiff);
  });

  it("an exact-scheme tier-1 still wins over tier-3", () => {
    registerGenericStructuredDiff();
    const fake = {
      scheme: "email",
      matches: (uri: string) => uri.startsWith("email://"),
      renderCurrent: () => <span data-testid="fake" />,
      renderDiff: () => <span data-testid="fake-diff" />,
      metadata: { origin: "first-party" as const, schemaVersion: 1 },
    };
    registerAdapter(fake);
    expect(resolveAdapter("email://draft-1")).toBe(fake);
    expect(resolveAdapter("salesforce://acc-9")).toBe(GenericStructuredDiff);
  });
});

describe("GenericStructuredDiff.renderCurrent — missing fields", () => {
  it("renders with no fields at all", () => {
    render(GenericStructuredDiff.renderCurrent({} as GenericCurrentState));
    expect(
      screen.getByTestId("generic-diff-current-body-empty"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("generic-diff-resource-id")).toHaveTextContent(
      "(no resource id)",
    );
    expect(screen.getByTestId("generic-diff-saas")).toHaveTextContent(
      "(unknown saas)",
    );
  });

  it("renders with only a resourceId", () => {
    render(GenericStructuredDiff.renderCurrent({ resourceId: "Deal-92" }));
    expect(screen.getByTestId("generic-diff-resource-id")).toHaveTextContent(
      "Deal-92",
    );
    expect(screen.getByTestId("generic-diff-saas")).toHaveTextContent(
      "(unknown saas)",
    );
  });

  it("renders with only a saas label", () => {
    render(GenericStructuredDiff.renderCurrent({ saas: "Hubspot" }));
    expect(screen.getByTestId("generic-diff-saas")).toHaveTextContent(
      "Hubspot",
    );
    expect(screen.getByTestId("generic-diff-resource-id")).toHaveTextContent(
      "(no resource id)",
    );
  });

  it("renders top-level fields as a definition list", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Deal-92",
        saas: "Hubspot",
        fields: { stage: "Negotiation", amount: 42000 },
      }),
    );
    const dl = screen.getByTestId("generic-diff-object");
    expect(within(dl).getByText("stage")).toBeInTheDocument();
    expect(within(dl).getByText("Negotiation")).toBeInTheDocument();
    expect(within(dl).getByText("amount")).toBeInTheDocument();
    expect(within(dl).getByText("42000")).toBeInTheDocument();
  });

  it("renders a primitive `fields` payload defensively (no crash)", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "X",
        fields: "just a string",
      }),
    );
    expect(screen.getByText("just a string")).toBeInTheDocument();
  });
});

describe("GenericStructuredDiff.renderCurrent — deeply nested payloads", () => {
  it("caps recursion at depth 5", () => {
    const deep = {
      l1: { l2: { l3: { l4: { l5: { l6: "too deep" } } } } },
    };
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Deep",
        fields: deep,
      }),
    );
    expect(screen.queryByText("too deep")).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("generic-diff-depth-cap").length,
    ).toBeGreaterThan(0);
  });

  it("renders nested objects above the depth cap", () => {
    const nested = { a: { b: { c: "shallow" } } };
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "ok",
        fields: nested,
      }),
    );
    expect(screen.getByText("shallow")).toBeInTheDocument();
  });

  it("does not throw on circular references", () => {
    const node: Record<string, unknown> = { name: "loop" };
    node.self = node;
    expect(() => {
      render(
        GenericStructuredDiff.renderCurrent({
          resourceId: "Loop",
          fields: node,
        }),
      );
    }).not.toThrow();
    expect(screen.getByTestId("generic-diff-circular")).toBeInTheDocument();
  });
});

describe("GenericStructuredDiff.renderCurrent — very large payloads", () => {
  it("truncates strings longer than 2048 bytes", () => {
    const huge = "x".repeat(5000);
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Huge",
        fields: { body: huge },
      }),
    );
    const truncation = screen.getByTestId("generic-diff-truncation");
    expect(truncation).toHaveTextContent("(+2952 chars hidden)");
  });

  it("does not truncate strings at exactly 2048 bytes", () => {
    const exact = "y".repeat(2048);
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Exact",
        fields: { body: exact },
      }),
    );
    expect(
      screen.queryByTestId("generic-diff-truncation"),
    ).not.toBeInTheDocument();
  });

  it("truncates arrays longer than 50 items", () => {
    const arr = Array.from({ length: 73 }, (_, i) => i);
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "BigArr",
        fields: { items: arr },
      }),
    );
    expect(screen.getByTestId("generic-diff-array-overflow")).toHaveTextContent(
      "(+23 items hidden)",
    );
  });

  it("truncates objects with more than 50 keys", () => {
    const fat: Record<string, unknown> = {};
    for (let i = 0; i < 73; i += 1) {
      fat[`k${i}`] = i;
    }
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "BigObj",
        fields: fat,
      }),
    );
    expect(
      screen.getByTestId("generic-diff-object-overflow"),
    ).toHaveTextContent("(+23 keys hidden)");
  });
});

describe("GenericStructuredDiff.renderCurrent — Open in {SaaS} link", () => {
  it("renders a safe https link with the SaaS label", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Deal-92",
        saas: "Hubspot",
        openUrl: "https://hubspot.example/deal/92",
        fields: {},
      }),
    );
    const link = screen.getByTestId("generic-diff-open-link");
    expect(link).toHaveAttribute("href", "https://hubspot.example/deal/92");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("aria-label", "Open Deal-92 in Hubspot");
    expect(link).toHaveTextContent("Open in Hubspot");
  });

  it("renders a safe http link", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "X",
        saas: "Y",
        openUrl: "http://example.com",
        fields: {},
      }),
    );
    expect(screen.getByTestId("generic-diff-open-link")).toHaveAttribute(
      "href",
      "http://example.com",
    );
  });

  it("drops a javascript: url", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "X",
        saas: "Y",
        openUrl: "javascript:alert(1)",
        fields: {},
      }),
    );
    expect(
      screen.queryByTestId("generic-diff-open-link"),
    ).not.toBeInTheDocument();
  });

  it("drops an empty / non-string openUrl", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "X",
        saas: "Y",
        openUrl: "",
        fields: {},
      }),
    );
    expect(
      screen.queryByTestId("generic-diff-open-link"),
    ).not.toBeInTheDocument();

    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "X",
        saas: "Y",
        openUrl: 42,
        fields: {},
      }),
    );
    expect(
      screen.queryByTestId("generic-diff-open-link"),
    ).not.toBeInTheDocument();
  });

  it("renders without an openUrl when SaaS is unknown", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "abc",
        fields: { kind: "thing" },
      }),
    );
    expect(
      screen.queryByTestId("generic-diff-open-link"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("generic-diff-saas")).toHaveTextContent(
      "(unknown saas)",
    );
  });
});

describe("GenericStructuredDiff.renderDiff", () => {
  const baseDiff = (
    over: Partial<GenericStructuredDiffPayload> = {},
  ): GenericStructuredDiffPayload => ({
    resourceId: "Deal-92",
    saas: "Hubspot",
    openUrl: "https://hubspot.example/deal/92",
    reasoning: "Stage advanced after demo call.",
    fieldChanges: [
      { field: "stage", old: "Discovery", new: "Negotiation" },
      { field: "amount", old: 30000, new: 42000 },
    ],
    ...over,
  });

  it("renders the PENDING DIFF pill in diff mode", () => {
    render(GenericStructuredDiff.renderDiff(baseDiff()));
    expect(screen.getByTestId("generic-diff-pending-pill")).toHaveTextContent(
      "PENDING DIFF",
    );
  });

  it("renders field-change rows with old → new", () => {
    render(GenericStructuredDiff.renderDiff(baseDiff()));
    const rows = screen.getAllByTestId("generic-diff-change-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("stage")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Discovery")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Negotiation")).toBeInTheDocument();
    expect(within(rows[1]).getByText("amount")).toBeInTheDocument();
    expect(within(rows[1]).getByText("30000")).toBeInTheDocument();
    expect(within(rows[1]).getByText("42000")).toBeInTheDocument();
  });

  it("renders reasoning text when present", () => {
    render(GenericStructuredDiff.renderDiff(baseDiff()));
    expect(screen.getByTestId("generic-diff-reasoning")).toHaveTextContent(
      "Stage advanced after demo call.",
    );
  });

  it("omits the reasoning block when reasoning is missing", () => {
    render(
      GenericStructuredDiff.renderDiff(baseDiff({ reasoning: undefined })),
    );
    expect(
      screen.queryByTestId("generic-diff-reasoning"),
    ).not.toBeInTheDocument();
  });

  it("renders 'Open in {SaaS}' link in diff mode", () => {
    render(GenericStructuredDiff.renderDiff(baseDiff()));
    expect(screen.getByTestId("generic-diff-open-link")).toHaveAttribute(
      "aria-label",
      "Open Deal-92 in Hubspot",
    );
  });

  it("uses sane fallbacks for unknown SaaS in diff mode", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Item-1",
        fieldChanges: [{ field: "name", old: "a", new: "b" }],
      }),
    );
    expect(screen.getByTestId("generic-diff-saas")).toHaveTextContent(
      "(unknown saas)",
    );
    expect(
      screen.queryByTestId("generic-diff-open-link"),
    ).not.toBeInTheDocument();
  });

  it("skips malformed field-change entries (no `field` string)", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "X",
        saas: "Y",
        fieldChanges: [
          { field: "ok", old: 1, new: 2 },
          { broken: true } as unknown as { field: string },
          "not-an-object" as unknown as { field: string },
        ],
      }),
    );
    expect(screen.getAllByTestId("generic-diff-change-row")).toHaveLength(1);
  });
});

describe("GenericStructuredDiff.renderDiff — no proposed payload, render current only", () => {
  it("falls back to `proposed` payload when fieldChanges is empty", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Deal-92",
        saas: "Hubspot",
        fieldChanges: [],
        proposed: { stage: "Negotiation", amount: 42000 },
      }),
    );
    const root = screen.getByTestId("generic-structured-diff");
    expect(root).toHaveAttribute("data-mode", "diff-current-only");
    expect(screen.getByText("stage")).toBeInTheDocument();
    expect(screen.getByText("Negotiation")).toBeInTheDocument();
    expect(screen.getByText("amount")).toBeInTheDocument();
    expect(screen.getByText("42000")).toBeInTheDocument();
  });

  it("falls back to `current` payload when fieldChanges + proposed are absent", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Deal-92",
        saas: "Hubspot",
        current: { stage: "Discovery" },
      }),
    );
    const root = screen.getByTestId("generic-structured-diff");
    expect(root).toHaveAttribute("data-mode", "diff-current-only");
    expect(screen.getByText("stage")).toBeInTheDocument();
    expect(screen.getByText("Discovery")).toBeInTheDocument();
  });

  it("renders an empty card gracefully when no payload is given at all", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Z",
        saas: "Y",
      }),
    );
    const root = screen.getByTestId("generic-structured-diff");
    expect(root).toHaveAttribute("data-mode", "diff-current-only");
    expect(
      screen.getByTestId("generic-diff-current-body-empty"),
    ).toBeInTheDocument();
  });

  it("still emits the PENDING DIFF pill in current-only fallback", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Z",
        saas: "Y",
        proposed: { x: 1 },
      }),
    );
    expect(screen.getByTestId("generic-diff-pending-pill")).toBeInTheDocument();
  });
});

describe("GenericStructuredDiff — accessibility", () => {
  it("current view exposes role=group with aria-label", () => {
    render(
      GenericStructuredDiff.renderCurrent({
        resourceId: "Deal-92",
        saas: "Hubspot",
        fields: { stage: "Discovery" },
      }),
    );
    const group = screen.getByRole("group");
    expect(group).toHaveAttribute("aria-label", "Hubspot Deal-92");
  });

  it("diff view exposes role=group with 'Pending diff' aria-label", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "Deal-92",
        saas: "Hubspot",
        fieldChanges: [{ field: "stage", old: "a", new: "b" }],
      }),
    );
    const group = screen.getByRole("group");
    expect(group).toHaveAttribute(
      "aria-label",
      "Pending diff: Hubspot Deal-92",
    );
  });

  it("field-change cells expose semantic aria-labels per side", () => {
    render(
      GenericStructuredDiff.renderDiff({
        resourceId: "X",
        saas: "Y",
        fieldChanges: [{ field: "stage", old: "a", new: "b" }],
      }),
    );
    expect(screen.getByLabelText("stage previous value")).toBeInTheDocument();
    expect(screen.getByLabelText("stage new value")).toBeInTheDocument();
  });
});
