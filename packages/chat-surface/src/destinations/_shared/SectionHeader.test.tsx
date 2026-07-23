// SectionHeader — the design `.sect-h` mono section header (PRD-G FR-G.1).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SectionHeader } from "./SectionHeader";

describe("<SectionHeader>", () => {
  it("renders an <h2> whose type comes from the .ui-mono-caps recipe", () => {
    render(<SectionHeader>Pinned</SectionHeader>);
    const label = screen.getByTestId("section-header-label");
    expect(label.tagName).toBe("H2");
    expect(label).toHaveTextContent("Pinned");
    // Family / size / tracking / case are the recipe's job — the component must
    // NOT re-compose them inline (that is how it shipped at 11.2px semibold with
    // a raw 0.12em).
    expect(label).toHaveClass("ui-mono-caps");
    expect(label.style.fontFamily).toBe("");
    expect(label.style.fontSize).toBe("");
    expect(label.style.fontWeight).toBe("");
    expect(label.style.letterSpacing).toBe("");
    expect(label.style.textTransform).toBe("");
    // The one documented per-role override: the design's `.sect-h` is --mut2,
    // one rung quieter than the recipe's default --color-text-muted.
    expect(label.style.color).toBe("var(--color-text-subtle)");
  });

  it("puts the type recipe on the LABEL and never on the wrapper (C13)", () => {
    // The wrapper also carries the count pill and the action slot (the Chats
    // "＋ New chat" primary), so `.ui-mono-caps` there would mono-uppercase a CTA.
    render(<SectionHeader>Pinned</SectionHeader>);
    expect(screen.getByTestId("section-header-label")).toHaveClass(
      "ui-mono-caps",
    );
    expect(screen.getByTestId("section-header")).not.toHaveClass(
      "ui-mono-caps",
    );
  });

  it("carries the block-rhythm recipe on the wrapper", () => {
    render(<SectionHeader>Pinned</SectionHeader>);
    const wrap = screen.getByTestId("section-header");
    // `.ui-section-head` supplies the design's 22px/10px block margins AND the
    // flex row; the component no longer hand-rolls the layout inline.
    expect(wrap).toHaveClass("ui-section-head");
    expect(wrap.style.display).toBe("");
    expect(wrap.style.gap).toBe("");
    // `sect-h` survives this change on purpose — PRD-13 deletes the vestigial
    // class, not this one.
    expect(wrap).toHaveClass("sect-h");
  });

  it("keeps caller classNames alongside both recipe classes", () => {
    render(<SectionHeader className="chats-head">Pinned</SectionHeader>);
    const wrap = screen.getByTestId("section-header");
    expect(wrap).toHaveClass("sect-h");
    expect(wrap).toHaveClass("ui-section-head");
    expect(wrap).toHaveClass("chats-head");
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
