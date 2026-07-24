import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ArtifactRevisionCompare,
  compareArtifactText,
} from "./ArtifactRevisionCompare";

describe("ArtifactRevisionCompare", () => {
  it("renders added and removed text with accessible non-color labels", () => {
    const comparison = compareArtifactText(
      "title\nold line\nshared",
      "title\nnew line\nshared",
      1,
      2,
    );
    render(
      <ArtifactRevisionCompare
        comparison={comparison}
        status="ready"
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "1 removed line; 1 added line",
    );
    expect(screen.getByLabelText("Text change details")).toHaveTextContent(
      "Removed: old line",
    );
    expect(screen.getByLabelText("Text change details")).toHaveTextContent(
      "Added: new line",
    );
    expect(
      screen.getByRole("button", { name: "Close comparison" }),
    ).toBeInTheDocument();
  });
});
