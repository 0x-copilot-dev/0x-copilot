import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { computeLineDiff } from "./lineDiff";
import { TcFileDiff } from "./TcFileDiff";

describe("TcFileDiff", () => {
  it("renders nothing when the sides are identical", () => {
    const { container } = render(
      <TcFileDiff diff={computeLineDiff("same", "same")} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the addition and deletion counts", () => {
    render(
      <TcFileDiff
        diff={computeLineDiff("a\nb", "a\nB\nc")}
        filePath="/tmp/x.txt"
      />,
    );
    expect(screen.getByTestId("tc-file-diff-counts").textContent).toBe("+2−1");
  });

  it("marks each row with the change it represents", () => {
    const { container } = render(
      <TcFileDiff diff={computeLineDiff("old line", "new line")} />,
    );
    const kinds = Array.from(
      container.querySelectorAll("[data-diff-kind]"),
    ).map((node) => node.getAttribute("data-diff-kind"));
    expect(kinds).toEqual(["remove", "add"]);
  });

  it("renders the file path when given one", () => {
    render(
      <TcFileDiff
        diff={computeLineDiff("a", "b")}
        filePath="/Users/x/project/data.csv"
      />,
    );
    expect(screen.getByTestId("tc-file-diff-path").textContent).toBe(
      "/Users/x/project/data.csv",
    );
  });

  it("caps rendered rows and reports the remainder", () => {
    const before = Array.from({ length: 60 }, (_, i) => `l${i}`).join("\n");
    const after = Array.from({ length: 60 }, (_, i) => `L${i}`).join("\n");
    const { container } = render(
      <TcFileDiff diff={computeLineDiff(before, after)} maxRows={10} />,
    );
    expect(container.querySelectorAll("[data-diff-kind]")).toHaveLength(10);
    expect(screen.getByTestId("tc-file-diff-omitted").textContent).toContain(
      "more",
    );
  });

  it("says so when the diff is an approximation, not a minimal edit", () => {
    const big = Array.from({ length: 40 }, (_, i) => `l${i}`).join("\n");
    render(
      <TcFileDiff diff={computeLineDiff(big, `${big}\nx`, { maxLines: 10 })} />,
    );
    expect(screen.getByTestId("tc-file-diff-approximate")).toBeTruthy();
  });
});
