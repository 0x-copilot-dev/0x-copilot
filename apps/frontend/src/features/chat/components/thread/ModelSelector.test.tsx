import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ModelCatalogModel } from "@enterprise-search/api-types";
import { ModelSelector } from "./ModelSelector";

const models: Array<ModelCatalogModel & { disabled?: boolean }> = [
  {
    id: "claude-opus-4-7",
    name: "Claude Opus 4.7",
    provider: "anthropic",
    model_name: "claude-opus-4-7",
    configured: true,
  },
  {
    id: "claude-sonnet-4-6",
    name: "Claude Sonnet 4.6",
    provider: "anthropic",
    model_name: "claude-sonnet-4-6",
    configured: true,
  },
];

describe("ModelSelector", () => {
  it("renders all available options", () => {
    render(
      <ModelSelector
        models={models}
        value="claude-opus-4-7"
        onChange={() => undefined}
      />,
    );
    expect(screen.getByText("Claude Opus 4.7")).toBeInTheDocument();
    expect(screen.getByText("Claude Sonnet 4.6")).toBeInTheDocument();
  });
  it("calls onChange when the selection changes", () => {
    const onChange = vi.fn();
    render(
      <ModelSelector
        models={models}
        value="claude-opus-4-7"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "claude-sonnet-4-6" },
    });
    expect(onChange).toHaveBeenCalledWith("claude-sonnet-4-6");
  });
});
