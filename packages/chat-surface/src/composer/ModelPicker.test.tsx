import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ModelPicker, listModelDescriptors } from "./ModelPicker";

describe("ModelPicker", () => {
  it("renders nothing when closed", () => {
    render(
      <ModelPicker
        open={false}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByTestId("model-picker")).not.toBeInTheDocument();
  });

  it("renders the three hardcoded models when open", () => {
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Opus 4.7")).toBeInTheDocument();
    expect(screen.getByText("Sonnet 4.6")).toBeInTheDocument();
    expect(screen.getByText("Haiku 4.5")).toBeInTheDocument();
  });

  it("marks the selected model with aria-selected=true and others false", () => {
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-sonnet-4-6"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    const sonnetRow = screen.getByTestId("model-picker-row-claude-sonnet-4-6");
    const opusRow = screen.getByTestId("model-picker-row-claude-opus-4-7");
    expect(sonnetRow).toHaveAttribute("aria-selected", "true");
    expect(opusRow).toHaveAttribute("aria-selected", "false");
  });

  it("fires onSelect with the chosen id and then onClose when a row is clicked", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={onSelect}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("model-picker-row-claude-haiku-4-5"));
    expect(onSelect).toHaveBeenCalledWith("claude-haiku-4-5");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("fires onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("model-picker-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders into portalTarget when provided", () => {
    const portalTarget = globalThis.document.createElement("div");
    portalTarget.setAttribute("data-testid", "portal-host");
    globalThis.document.body.appendChild(portalTarget);
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={() => {}}
        portalTarget={portalTarget}
      />,
    );
    expect(
      portalTarget.querySelector('[data-testid="model-picker"]'),
    ).not.toBeNull();
    portalTarget.remove();
  });

  it("exposes the descriptor list for hosts that need it", () => {
    const list = listModelDescriptors();
    expect(list.map((m) => m.id)).toEqual([
      "claude-opus-4-7",
      "claude-sonnet-4-6",
      "claude-haiku-4-5",
    ]);
  });
});
