// ComposerToolsButton — the composer tools pill trigger (PRD-P4).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComposerToolsButton } from "./ComposerToolsButton";

describe("<ComposerToolsButton>", () => {
  it("renders the label and reflects the open state via aria-expanded", () => {
    render(<ComposerToolsButton open onClick={vi.fn()} activeCount={0} />);
    const btn = screen.getByTestId("first-run-tools-button");
    expect(btn.textContent).toContain("Tools");
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("data-open")).toBe("true");
  });

  it("hides the badge at zero active tools", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={0} />,
    );
    expect(screen.queryByTestId("first-run-tools-button-badge")).toBeNull();
    expect(
      screen
        .getByTestId("first-run-tools-button")
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("shows the active count in the badge", () => {
    render(
      <ComposerToolsButton open={false} onClick={vi.fn()} activeCount={3} />,
    );
    expect(screen.getByTestId("first-run-tools-button-badge").textContent).toBe(
      "3",
    );
  });

  it("fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(
      <ComposerToolsButton open={false} onClick={onClick} activeCount={1} />,
    );
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire onClick when disabled", () => {
    const onClick = vi.fn();
    render(
      <ComposerToolsButton
        open={false}
        onClick={onClick}
        activeCount={1}
        disabled
      />,
    );
    fireEvent.click(screen.getByTestId("first-run-tools-button"));
    expect(onClick).not.toHaveBeenCalled();
  });
});
