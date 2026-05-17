import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContextPanel } from "./ContextPanel";

describe("ContextPanel", () => {
  it("renders the supplied title", () => {
    render(<ContextPanel title="Library" />);
    expect(screen.getByTestId("context-panel-header")).toHaveTextContent(
      "Library",
    );
  });

  it("renders a subtitle when provided", () => {
    render(
      <ContextPanel title="Library" subtitle="Files · Pages · Datasets" />,
    );
    expect(screen.getByTestId("context-panel-subtitle")).toHaveTextContent(
      "Files · Pages · Datasets",
    );
  });

  it("does not render hardcoded 'Filter row 1/2/3' placeholders", () => {
    // Regression: prior implementation always rendered three rows with
    // the string "Filter row N". The new ContextPanel is a generic
    // shell — content comes from the host or the empty state.
    render(<ContextPanel title="Inbox" />);
    expect(screen.queryByText(/Filter row \d/)).not.toBeInTheDocument();
  });

  it("falls back to a neutral empty state when no children are supplied", () => {
    render(<ContextPanel title="Inbox" />);
    expect(screen.getByTestId("context-panel-empty")).toBeInTheDocument();
  });

  it("renders host-supplied children inside the body", () => {
    render(
      <ContextPanel title="Inbox">
        <ul data-testid="host-rows">
          <li>Mention</li>
          <li>Reply</li>
        </ul>
      </ContextPanel>,
    );
    expect(screen.getByTestId("host-rows")).toBeInTheDocument();
    expect(screen.queryByTestId("context-panel-empty")).not.toBeInTheDocument();
  });

  it("wires the search input value + onChange", () => {
    const onChange = vi.fn<(next: string) => void>();
    render(
      <ContextPanel
        title="Library"
        search={{ value: "abc", onChange, placeholder: "Search library" }}
      />,
    );
    const input = screen.getByTestId(
      "context-panel-search",
    ) as HTMLInputElement;
    expect(input.value).toBe("abc");
    expect(input.placeholder).toBe("Search library");
    fireEvent.change(input, { target: { value: "abcd" } });
    expect(onChange).toHaveBeenCalledWith("abcd");
  });

  it("renders a primary action button and fires onClick", () => {
    const onClick = vi.fn<() => void>();
    render(
      <ContextPanel
        title="Library"
        primaryAction={{ label: "New page", onClick }}
      />,
    );
    const action = screen.getByTestId("context-panel-primary-action");
    expect(action).toHaveTextContent("New page");
    fireEvent.click(action);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders a footer when provided", () => {
    render(<ContextPanel title="Library" footer={<span>{"42 files"}</span>} />);
    expect(screen.getByText("42 files")).toBeInTheDocument();
  });

  it("tags the rendered panel with the destination slug for theming", () => {
    render(<ContextPanel title="Library" destination="library" />);
    // `getByRole("complementary")` is the panel's <aside>; its
    // data-destination attribute is the host's theming hook.
    expect(
      screen.getByRole("complementary", { name: /library panel/i }),
    ).toHaveAttribute("data-destination", "library");
  });
});
