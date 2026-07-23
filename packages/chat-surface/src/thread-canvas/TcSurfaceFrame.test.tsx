import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcSurfaceFrame } from "./TcSurfaceFrame";
import type { SurfaceProvenance } from "./provenance";

const prov = (over: Partial<SurfaceProvenance> = {}): SurfaceProvenance => ({
  surfaceId: "s1",
  ledgerId: "r7f3·042",
  connector: "linear",
  op: "get_issue",
  kind: "record",
  latencyMs: 120,
  accessClass: "read",
  tier: "shaped",
  openIn: null,
  ...over,
});

const child = <div data-testid="b1-pane">surface content</div>;

describe("TcSurfaceFrame", () => {
  it("renders children bare (no frame chrome) when provenance is null", () => {
    render(<TcSurfaceFrame provenance={null}>{child}</TcSurfaceFrame>);
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-surface-frame")).toBeNull();
    expect(screen.queryByTestId("tc-provenance-footer")).toBeNull();
  });

  it("shows the skeleton (not children) while pending, with the footer pinned", () => {
    render(
      <TcSurfaceFrame provenance={prov({ tier: "pending" })}>
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-surface-skeleton")).toHaveTextContent(
      "Linear · assembling record view…",
    );
    expect(screen.queryByTestId("b1-pane")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("renders children for generic/shaped with the footer", () => {
    render(
      <TcSurfaceFrame provenance={prov({ tier: "generic" })}>
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("renders the raw fallback (not children) for the raw tier", () => {
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "raw" })}
        rawPayload={{ data: { id: 1 } }}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-raw-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("b1-pane")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("resolves a deep link from the hydrated payload for the footer", () => {
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "generic" })}
        rawPayload={{
          spec: { link: { label: "Open", url_path: "data.url" } },
          data: { url: "https://linear.app/x" },
        }}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-provenance-open-in")).toHaveAttribute(
      "href",
      "https://linear.app/x",
    );
  });
});
