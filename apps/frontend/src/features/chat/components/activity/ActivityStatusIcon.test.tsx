import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityStatusIcon } from "./ActivityStatusIcon";

describe("ActivityStatusIcon", () => {
  it("renders a spinner for running statuses", () => {
    const { container } = render(<ActivityStatusIcon status="running" />);
    expect(
      container.querySelector(".aui-activity-item__spinner"),
    ).toBeInTheDocument();
  });
  it("renders an error mark for failed statuses", () => {
    const { container } = render(<ActivityStatusIcon status="failed" />);
    const mark = container.querySelector(".aui-activity-item__mark");
    expect(mark?.textContent).toBe("!");
  });
  it("renders a check mark for completed statuses", () => {
    const { container } = render(<ActivityStatusIcon status="done" />);
    const mark = container.querySelector(".aui-activity-item__mark");
    expect(mark?.textContent).toBe("✓");
  });
});
