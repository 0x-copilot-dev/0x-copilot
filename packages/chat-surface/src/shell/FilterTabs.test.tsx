import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FilterTabs, type FilterTabOption } from "./FilterTabs";

type Slug = "all" | "mentions" | "approvals";

const OPTIONS: ReadonlyArray<FilterTabOption<Slug>> = [
  { slug: "all", label: "All", count: 3 },
  { slug: "mentions", label: "Mentions", count: 1 },
  { slug: "approvals", label: "Approvals" },
];

describe("<FilterTabs>", () => {
  it("renders a tablist with one tab per option", () => {
    render(
      <FilterTabs<Slug>
        value="all"
        onChange={() => undefined}
        options={OPTIONS}
        ariaLabel="Inbox filters"
        idPrefix="inbox"
      />,
    );
    const list = screen.getByRole("tablist", { name: "Inbox filters" });
    expect(list).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
  });

  it("sets aria-selected on the active tab and tabIndex correctly", () => {
    render(
      <FilterTabs<Slug>
        value="mentions"
        onChange={() => undefined}
        options={OPTIONS}
        ariaLabel="Inbox filters"
        idPrefix="inbox"
      />,
    );
    const allTab = screen.getByTestId("filter-tab-all");
    const mentionsTab = screen.getByTestId("filter-tab-mentions");
    expect(allTab).toHaveAttribute("aria-selected", "false");
    expect(allTab).toHaveAttribute("tabindex", "-1");
    expect(mentionsTab).toHaveAttribute("aria-selected", "true");
    expect(mentionsTab).toHaveAttribute("tabindex", "0");
  });

  it("wires aria-controls to the panel id pattern", () => {
    render(
      <FilterTabs<Slug>
        value="all"
        onChange={() => undefined}
        options={OPTIONS}
        ariaLabel="Inbox filters"
        idPrefix="inbox"
      />,
    );
    expect(screen.getByTestId("filter-tab-all")).toHaveAttribute(
      "aria-controls",
      "inbox-panel-all",
    );
    expect(screen.getByTestId("filter-tab-mentions")).toHaveAttribute(
      "id",
      "inbox-tab-mentions",
    );
  });

  it("renders the optional count chip when present and omits it otherwise", () => {
    render(
      <FilterTabs<Slug>
        value="all"
        onChange={() => undefined}
        options={OPTIONS}
        ariaLabel="Inbox filters"
        idPrefix="inbox"
      />,
    );
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("3");
    expect(screen.queryByTestId("filter-tab-count-approvals")).toBeNull();
  });

  it("calls onChange with the slug on click", () => {
    const onChange = vi.fn();
    render(
      <FilterTabs<Slug>
        value="all"
        onChange={onChange}
        options={OPTIONS}
        ariaLabel="Inbox filters"
        idPrefix="inbox"
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-mentions"));
    expect(onChange).toHaveBeenCalledWith("mentions");
  });
});
