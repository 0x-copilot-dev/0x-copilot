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
});
