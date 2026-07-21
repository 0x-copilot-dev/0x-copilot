import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  ModelPicker,
  listDepthDescriptors,
  listModelDescriptors,
} from "./ModelPicker";

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

  it("renders the built-in fallback models when no catalog is injected", () => {
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

  it("renders EXACTLY the injected catalog models, not the fallback, when `models` is provided", () => {
    /* The injected `models` prop is the source of truth (sourced from the
     * workspace catalog / `/v1/agent/models`). The hardcoded fallback trio
     * must NOT leak through when a host injects a real catalog. */
    render(
      <ModelPicker
        open={true}
        models={[
          { id: "qwen3-4b", label: "Qwen 3 4B", family: "Local" },
          { id: "gpt-5", label: "GPT-5", family: "OpenAI" },
        ]}
        selectedModel="qwen3-4b"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    // The injected models are shown…
    expect(screen.getByText("Qwen 3 4B")).toBeInTheDocument();
    expect(screen.getByText("GPT-5")).toBeInTheDocument();
    expect(screen.getByTestId("model-picker-row-qwen3-4b")).toBeInTheDocument();
    // …and none of the hardcoded fallback trio bleed through.
    expect(screen.queryByText("Opus 4.7")).not.toBeInTheDocument();
    expect(screen.queryByText("Sonnet 4.6")).not.toBeInTheDocument();
    expect(screen.queryByText("Haiku 4.5")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("model-picker-row-claude-opus-4-7"),
    ).not.toBeInTheDocument();
  });

  it("falls back to the built-in list when `models` is an empty array", () => {
    render(
      <ModelPicker
        open={true}
        models={[]}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Opus 4.7")).toBeInTheDocument();
  });

  it("fires onSelect with an injected model id and then onClose", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ModelPicker
        open={true}
        models={[{ id: "qwen3-4b", label: "Qwen 3 4B", family: "Local" }]}
        selectedModel="qwen3-4b"
        onSelect={onSelect}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("model-picker-row-qwen3-4b"));
    expect(onSelect).toHaveBeenCalledWith("qwen3-4b");
    expect(onClose).toHaveBeenCalledTimes(1);
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

  /* Combined Model · Depth popover (chat1.md L805-820). The same popover
   * surfaces both axes — model rows and a Fast/Balanced/Deep grid. */
  it("renders the depth chips Fast/Balanced/Deep when open", () => {
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("depth-picker")).toBeInTheDocument();
    expect(screen.getByTestId("depth-picker-row-fast")).toBeInTheDocument();
    expect(screen.getByTestId("depth-picker-row-balanced")).toBeInTheDocument();
    expect(screen.getByTestId("depth-picker-row-deep")).toBeInTheDocument();
  });

  it("marks the selected depth with aria-checked=true and fires onDepthChange", () => {
    const onDepthChange = vi.fn();
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        selectedDepth="balanced"
        onSelect={() => {}}
        onDepthChange={onDepthChange}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("depth-picker-row-balanced")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(screen.getByTestId("depth-picker-row-fast"));
    expect(onDepthChange).toHaveBeenCalledWith("fast");
  });

  it("does not close the popover when a depth chip is selected", () => {
    /* Depth is a frequently tuned axis after picking a model; closing
     * here would force a re-open and make the popover feel modal. */
    const onClose = vi.fn();
    render(
      <ModelPicker
        open={true}
        selectedModel="claude-opus-4-7"
        onSelect={() => {}}
        onDepthChange={() => {}}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("depth-picker-row-deep"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("exposes the depth descriptor list for hosts that need it", () => {
    const list = listDepthDescriptors();
    expect(list.map((d) => d.id)).toEqual(["fast", "balanced", "deep"]);
  });
});
