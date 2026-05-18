// TemplateGallery tests (P6.5-B1).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TemplateGallery,
  type ProjectTemplateCard,
  type ProjectTemplateId,
} from "./TemplateGallery";

const asId = (s: string): ProjectTemplateId =>
  s as unknown as ProjectTemplateId;

function makeTemplate(
  over: Partial<ProjectTemplateCard> = {},
): ProjectTemplateCard {
  return {
    id: asId("tpl_1"),
    name: "Quarterly review",
    description: "Recurring quarterly business review.",
    iconEmoji: "📊",
    colorHue: 210,
    ownerDisplayName: "Alice",
    ownerUserId: "usr_alice",
    viewerIsOwner: true,
    seededTodosCount: 3,
    seededRoutinesCount: 1,
    forkCount: 5,
    createdAt: "2026-05-10T10:00:00.000Z",
    ...over,
  };
}

describe("TemplateGallery", () => {
  it("renders the loading skeleton when templates is null", () => {
    render(<TemplateGallery templates={null} />);
    expect(screen.getByTestId("template-gallery")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getAllByTestId("template-skeleton-card")).toHaveLength(6);
  });

  it("renders the empty state when templates is an empty array", () => {
    const onSaveFromProject = vi.fn();
    render(
      <TemplateGallery templates={[]} onSaveFromProject={onSaveFromProject} />,
    );
    expect(screen.getByTestId("template-gallery")).toHaveAttribute(
      "data-state",
      "ready-empty",
    );
    expect(screen.getByText("No project templates yet")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onSaveFromProject).toHaveBeenCalledTimes(1);
  });

  it("renders one card per template", () => {
    render(
      <TemplateGallery
        templates={[
          makeTemplate({ id: asId("a"), name: "A" }),
          makeTemplate({ id: asId("b"), name: "B", viewerIsOwner: false }),
        ]}
      />,
    );
    const cards = screen.getAllByTestId("template-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]!.getAttribute("data-template-id")).toBe("a");
    expect(cards[1]!.getAttribute("data-template-id")).toBe("b");
  });

  it("shows seeded counts and fork count on each card", () => {
    render(
      <TemplateGallery
        templates={[
          makeTemplate({
            seededTodosCount: 3,
            seededRoutinesCount: 1,
            forkCount: 5,
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("template-card-seeded")).toHaveTextContent(
      "Seeds 3 todos · 1 routine",
    );
    expect(screen.getByTestId("template-card-forks")).toHaveTextContent(
      "5 forks",
    );
  });

  it("invokes onFork with the template id when Fork is clicked", () => {
    const onFork = vi.fn();
    render(
      <TemplateGallery
        templates={[makeTemplate({ id: asId("tpl_x") })]}
        onFork={onFork}
      />,
    );
    fireEvent.click(screen.getByTestId("template-card-fork"));
    expect(onFork).toHaveBeenCalledWith(asId("tpl_x"));
  });

  it("only shows Edit / Delete on cards owned by the viewer", () => {
    render(
      <TemplateGallery
        templates={[
          makeTemplate({ id: asId("mine"), viewerIsOwner: true }),
          makeTemplate({ id: asId("yours"), viewerIsOwner: false }),
        ]}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const editButtons = screen.getAllByTestId("template-card-edit");
    expect(editButtons).toHaveLength(1);
    const deleteButtons = screen.getAllByTestId("template-card-delete");
    expect(deleteButtons).toHaveLength(1);
  });

  it("renders the filter tabs and fires onFilterChange on click", () => {
    const onFilterChange = vi.fn();
    render(
      <TemplateGallery
        templates={[makeTemplate()]}
        filter="all"
        counts={{ all: 4, mine: 1 }}
        onFilterChange={onFilterChange}
      />,
    );
    expect(screen.getByTestId("filter-tab-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-tab-mine")).toBeInTheDocument();
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("4");
    expect(screen.getByTestId("filter-tab-count-mine")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("filter-tab-mine"));
    expect(onFilterChange).toHaveBeenCalledWith("mine");
  });

  it("renders the New-from-project primary action when supplied", () => {
    const onSaveFromProject = vi.fn();
    render(
      <TemplateGallery
        templates={[makeTemplate()]}
        onSaveFromProject={onSaveFromProject}
      />,
    );
    fireEvent.click(screen.getByTestId("template-gallery-new-from-project"));
    expect(onSaveFromProject).toHaveBeenCalledTimes(1);
  });

  it("renders the You status pill on cards owned by the viewer", () => {
    render(
      <TemplateGallery templates={[makeTemplate({ viewerIsOwner: true })]} />,
    );
    expect(screen.getByText("You")).toBeInTheDocument();
  });
});
