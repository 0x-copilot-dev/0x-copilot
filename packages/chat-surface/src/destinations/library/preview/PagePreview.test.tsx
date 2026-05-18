// Tests for <PagePreview /> (P7-B2).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PagePreview } from "./PagePreview";

describe("<PagePreview>", () => {
  it("renders the markdown body", () => {
    render(<PagePreview markdown="# Heading\n\nBody text." />);
    const preview = screen.getByTestId("library-page-preview");
    expect(preview.getAttribute("data-state")).toBe("ready");
    // Streamdown emits real HTML; just check the prose surfaces.
    expect(preview.textContent).toContain("Heading");
    expect(preview.textContent).toContain("Body text");
  });

  it("renders empty state when markdown is empty", () => {
    render(<PagePreview markdown="" />);
    const preview = screen.getByTestId("library-page-preview");
    expect(preview.getAttribute("data-state")).toBe("empty");
    expect(preview.textContent).toContain("empty");
  });

  it("passes streaming mode through to Streamdown", () => {
    render(<PagePreview markdown="loading..." mode="streaming" />);
    expect(
      screen.getByTestId("library-page-preview").getAttribute("data-mode"),
    ).toBe("streaming");
  });
});
