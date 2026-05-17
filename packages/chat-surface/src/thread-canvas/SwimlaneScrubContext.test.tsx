import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";

import {
  SwimlaneScrubProvider,
  useSwimlaneScrub,
  type SwimlaneScrubState,
} from "./SwimlaneScrubContext";

function ScrubProbe(): ReactElement {
  const state = useSwimlaneScrub();
  return (
    <span data-testid="probe">
      {typeof state.scrubbedTo === "number" ? `t=${state.scrubbedTo}` : "now"}
    </span>
  );
}

describe("SwimlaneScrubContext", () => {
  it('defaults to "now" when no provider is mounted', () => {
    render(<ScrubProbe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("now");
  });

  it("returns the provided scrubbedTo value", () => {
    const value: SwimlaneScrubState = { scrubbedTo: 1716000000000 };
    render(
      <SwimlaneScrubProvider value={value}>
        <ScrubProbe />
      </SwimlaneScrubProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("t=1716000000000");
  });

  it("re-renders consumers when the provider value changes", () => {
    const { rerender } = render(
      <SwimlaneScrubProvider value={{ scrubbedTo: "now" }}>
        <ScrubProbe />
      </SwimlaneScrubProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("now");

    rerender(
      <SwimlaneScrubProvider value={{ scrubbedTo: 1234 }}>
        <ScrubProbe />
      </SwimlaneScrubProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("t=1234");
  });
});
