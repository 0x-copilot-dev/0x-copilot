import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcProvenanceFooter } from "./TcProvenanceFooter";
import type { SurfaceProvenance } from "./provenance";

const base: SurfaceProvenance = {
  surfaceId: "s1",
  ledgerId: "r7f3·042",
  connector: "linear",
  op: "get_issue",
  kind: "record",
  latencyMs: 420,
  accessClass: "read",
  tier: "shaped",
  openIn: null,
};

describe("TcProvenanceFooter", () => {
  it("renders op, latency, access class, and ledger id", () => {
    render(<TcProvenanceFooter provenance={base} />);
    expect(screen.getByTestId("tc-provenance-op")).toHaveTextContent(
      "linear.get_issue",
    );
    expect(screen.getByTestId("tc-provenance-latency")).toHaveTextContent(
      "420ms",
    );
    expect(screen.getByTestId("tc-provenance-access")).toHaveTextContent(
      "read-only",
    );
    expect(screen.getByTestId("tc-provenance-ledger-id")).toHaveTextContent(
      "r7f3·042",
    );
  });

  it("shows the write · held badge for a held surface", () => {
    render(
      <TcProvenanceFooter
        provenance={{ ...base, accessClass: "write_held" }}
      />,
    );
    const badge = screen.getByTestId("tc-provenance-access");
    expect(badge).toHaveTextContent("write · held");
    expect(badge.className).toContain("ui-badge--warning");
  });

  it("renders the deep link with rel=noreferrer noopener and the spec label", () => {
    render(
      <TcProvenanceFooter
        provenance={{
          ...base,
          openIn: { label: "Open issue", url: "https://linear.app/x" },
        }}
      />,
    );
    const link = screen.getByTestId("tc-provenance-open-in");
    expect(link).toHaveAttribute("href", "https://linear.app/x");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveTextContent("Open issue ↗");
  });

  it("builds the Open in <connector> fallback when the spec label is absent", () => {
    render(
      <TcProvenanceFooter
        provenance={{
          ...base,
          openIn: { label: null, url: "https://linear.app/x" },
        }}
      />,
    );
    expect(screen.getByTestId("tc-provenance-open-in")).toHaveTextContent(
      "Open in Linear ↗",
    );
  });

  it("renders no anchor when openIn is null", () => {
    render(<TcProvenanceFooter provenance={base} />);
    expect(screen.queryByTestId("tc-provenance-open-in")).toBeNull();
  });

  it("omits the latency chip when latency is null", () => {
    render(<TcProvenanceFooter provenance={{ ...base, latencyMs: null }} />);
    expect(screen.queryByTestId("tc-provenance-latency")).toBeNull();
  });
});
