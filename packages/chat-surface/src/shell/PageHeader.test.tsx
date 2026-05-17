import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PageHeader } from "./PageHeader";

describe("<PageHeader>", () => {
  it("renders the title and a region landmark named after it", () => {
    render(<PageHeader title="Home" />);
    expect(screen.getByTestId("page-header-title")).toHaveTextContent("Home");
    expect(screen.getByRole("region", { name: "Home" })).toBeInTheDocument();
  });

  it("renders the subtitle when provided and skips it when empty", () => {
    const { rerender } = render(
      <PageHeader title="Home" subtitle="Morning briefing" />,
    );
    expect(screen.getByTestId("page-header-subtitle")).toHaveTextContent(
      "Morning briefing",
    );
    rerender(<PageHeader title="Home" subtitle="" />);
    expect(screen.queryByTestId("page-header-subtitle")).toBeNull();
  });

  it("renders pre-built badges in the badges slot", () => {
    render(
      <PageHeader title="Home" badges={<span data-testid="badge-x">x</span>} />,
    );
    expect(screen.getByTestId("page-header-badges")).toBeInTheDocument();
    expect(screen.getByTestId("badge-x")).toBeInTheDocument();
  });

  it("fires onClick when the primary action is pressed", () => {
    const onClick = vi.fn();
    render(
      <PageHeader
        title="Routines"
        primaryAction={{ label: "New routine", onClick }}
      />,
    );
    const btn = screen.getByTestId("page-header-primary-action");
    expect(btn).toHaveTextContent("New routine");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables the primary action and skips clicks when disabled", () => {
    const onClick = vi.fn();
    render(
      <PageHeader
        title="Routines"
        primaryAction={{ label: "New routine", onClick, disabled: true }}
      />,
    );
    const btn = screen.getByTestId("page-header-primary-action");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});
