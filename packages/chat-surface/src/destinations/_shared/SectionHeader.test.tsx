// SectionHeader — the design `.sect-h` mono section header (PRD-G FR-G.1).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SectionHeader } from "./SectionHeader";

describe("<SectionHeader>", () => {
  it("renders an <h2> with the mono uppercase `.sect-h` style", () => {
    render(<SectionHeader>Pinned</SectionHeader>);
    const label = screen.getByTestId("section-header-label");
    expect(label.tagName).toBe("H2");
    expect(label).toHaveTextContent("Pinned");
    expect(label.style.fontFamily).toBe("var(--font-mono)");
    expect(label.style.textTransform).toBe("uppercase");
    expect(label.style.letterSpacing).toBe("0.12em");
    expect(label.style.color).toBe("var(--color-text-subtle)");
    expect(screen.getByTestId("section-header")).toHaveClass("sect-h");
  });

  it("associates the heading id so a section can aria-labelledby it", () => {
    render(<SectionHeader headingId="chats-pinned">Pinned</SectionHeader>);
    expect(screen.getByTestId("section-header-label")).toHaveAttribute(
      "id",
      "chats-pinned",
    );
  });

  it("renders an inline count chip when provided", () => {
    render(<SectionHeader count={<span>3</span>}>Recent</SectionHeader>);
    expect(screen.getByTestId("section-header-count")).toHaveTextContent("3");
  });

  it("renders a right-aligned action slot when provided", () => {
    render(
      <SectionHeader action={<button type="button">＋ New chat</button>}>
        Pinned
      </SectionHeader>,
    );
    const action = screen.getByTestId("section-header-action");
    expect(action.style.marginInlineStart).toBe("auto");
    expect(
      screen.getByRole("button", { name: "＋ New chat" }),
    ).toBeInTheDocument();
  });

  it("omits count + action slots when not provided", () => {
    render(<SectionHeader>Archived · history</SectionHeader>);
    expect(screen.queryByTestId("section-header-count")).toBeNull();
    expect(screen.queryByTestId("section-header-action")).toBeNull();
  });
});
