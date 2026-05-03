import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatDetailValue } from "./formatDetailValue";

describe("formatDetailValue", () => {
  it("renders short strings inline", () => {
    const { container } = render(<>{formatDetailValue("hello")}</>);
    expect(container.querySelector("span")?.textContent).toBe("hello");
    expect(container.querySelector("pre")).toBeNull();
  });
  it("renders multi-line strings as a pre block", () => {
    const { container } = render(
      <>{formatDetailValue("line one\nline two")}</>,
    );
    expect(container.querySelector("pre")?.textContent).toBe(
      "line one\nline two",
    );
  });
  it("renders objects as a pretty-printed pre block", () => {
    const { container } = render(<>{formatDetailValue({ ok: true })}</>);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('"ok": true');
  });
  it("renders null and primitives as a span", () => {
    const { container: nullContainer } = render(<>{formatDetailValue(null)}</>);
    expect(nullContainer.querySelector("span")?.textContent).toBe("null");
    const { container: numContainer } = render(<>{formatDetailValue(42)}</>);
    expect(numContainer.querySelector("span")?.textContent).toBe("42");
  });
});
