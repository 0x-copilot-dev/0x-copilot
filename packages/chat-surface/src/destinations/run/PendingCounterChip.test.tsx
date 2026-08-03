// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PendingCounterChip } from "./PendingCounterChip";

describe("PendingCounterChip", () => {
  it("is hidden at zero", () => {
    const { container } = render(
      <PendingCounterChip count={0} onClick={() => undefined} />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("pending-counter-chip")).toBeNull();
  });

  it("renders 'N waiting' when there is pending work", () => {
    render(<PendingCounterChip count={3} onClick={() => undefined} />);
    const chip = screen.getByTestId("pending-counter-chip");
    expect(chip.textContent).toBe("3 waiting");
    expect(chip.getAttribute("data-count")).toBe("3");
  });

  it("opens the Approvals tab on click", () => {
    const onClick = vi.fn();
    render(<PendingCounterChip count={2} onClick={onClick} />);
    screen.getByTestId("pending-counter-chip").click();
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("says 'elsewhere' when every counted item is in another chat", () => {
    // Beside PostureChip this chip reads as one sentence — "Writes wait for
    // you · 2 waiting" — so an unqualified count sends the reader hunting THIS
    // thread for work parked somewhere else.
    render(
      <PendingCounterChip count={2} allElsewhere onClick={() => undefined} />,
    );
    const chip = screen.getByTestId("pending-counter-chip");
    expect(chip.textContent).toBe("2 elsewhere");
    expect(chip.getAttribute("data-scope")).toBe("elsewhere");
  });

  it("stays 'waiting' when any item belongs to the run on screen", () => {
    render(
      <PendingCounterChip
        count={2}
        allElsewhere={false}
        onClick={() => undefined}
      />,
    );
    expect(screen.getByTestId("pending-counter-chip").textContent).toBe(
      "2 waiting",
    );
  });
});
