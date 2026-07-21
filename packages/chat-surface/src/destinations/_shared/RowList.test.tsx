// RowList — the design `.rowlist` card wrapping rows (PRD-G FR-G.1).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Row } from "./Row";
import { RowList } from "./RowList";

describe("<RowList>", () => {
  it("renders one bordered/rounded card wrapping the rows", () => {
    render(
      <RowList
        ariaLabel="Runs"
        items={["a", "b"]}
        keyFor={(x) => x}
        renderRow={(x) => <Row title={x} />}
      />,
    );
    const card = screen.getByTestId("row-list");
    expect(card.tagName).toBe("UL");
    expect(card).toHaveClass("rowlist");
    expect(card).toHaveAttribute("aria-label", "Runs");
    expect(card.style.border).toBe("1px solid var(--color-border)");
    expect(card.style.borderRadius).toBe("var(--radius-md)");
    expect(card.style.backgroundColor).toBe("var(--color-surface)");
  });

  it("separates rows with internal hairlines — last row has none", () => {
    render(
      <RowList
        items={["a", "b", "c"]}
        keyFor={(x) => x}
        renderRow={(x) => <Row title={x} />}
      />,
    );
    const rows = screen.getAllByTestId("row-list-item");
    expect(rows).toHaveLength(3);
    expect(rows[0]!.style.borderBottom).toBe("1px solid var(--color-border)");
    expect(rows[1]!.style.borderBottom).toBe("1px solid var(--color-border)");
    expect(rows[2]!.style.borderBottom).toBe("");
  });

  it("renders each item through renderRow", () => {
    render(
      <RowList
        items={[{ id: "x", name: "Row X" }]}
        keyFor={(x) => x.id}
        renderRow={(x) => <Row title={x.name} />}
      />,
    );
    expect(screen.getByTestId("row-title")).toHaveTextContent("Row X");
  });

  it("renders an empty card when there are no items", () => {
    render(<RowList items={[]} renderRow={() => null} />);
    expect(screen.getByTestId("row-list")).toBeInTheDocument();
    expect(screen.queryByTestId("row-list-item")).toBeNull();
  });
});
