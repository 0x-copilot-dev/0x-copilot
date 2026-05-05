import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThinkingDepthControl } from "./ThinkingDepthControl";

describe("ThinkingDepthControl", () => {
  it("renders nothing when visible=false", () => {
    const { container } = render(
      <ThinkingDepthControl
        value="balanced"
        onChange={() => undefined}
        visible={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders three radios", () => {
    render(
      <ThinkingDepthControl
        value="balanced"
        onChange={() => undefined}
        visible
      />,
    );
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("marks the active depth checked", () => {
    render(
      <ThinkingDepthControl value="deep" onChange={() => undefined} visible />,
    );
    const checked = screen
      .getAllByRole("radio")
      .filter((node) => node.getAttribute("aria-checked") === "true");
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveTextContent("Deep");
  });

  it("invokes onChange when a chip is clicked", () => {
    const onChange = vi.fn();
    render(
      <ThinkingDepthControl value="balanced" onChange={onChange} visible />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Deep" }));
    expect(onChange).toHaveBeenCalledWith("deep");
  });

  it("supports arrow-key navigation", () => {
    const onChange = vi.fn();
    render(
      <ThinkingDepthControl value="balanced" onChange={onChange} visible />,
    );
    fireEvent.keyDown(screen.getByRole("radio", { name: "Balanced" }), {
      key: "ArrowRight",
    });
    expect(onChange).toHaveBeenCalledWith("deep");
    onChange.mockReset();
    fireEvent.keyDown(screen.getByRole("radio", { name: "Balanced" }), {
      key: "ArrowLeft",
    });
    expect(onChange).toHaveBeenCalledWith("fast");
  });

  it("respects disabled", () => {
    const onChange = vi.fn();
    render(
      <ThinkingDepthControl
        value="balanced"
        onChange={onChange}
        visible
        disabled
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Deep" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
