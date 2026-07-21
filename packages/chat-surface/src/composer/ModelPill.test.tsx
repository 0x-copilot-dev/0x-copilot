// Topbar model-picker render contract (FR-1.6). Moved down with the
// component from apps/frontend (PR-1.2); the same assertions run from
// chat-surface.

import type { ModelCatalogModel } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelPill } from "./ModelPill";

const models: Array<ModelCatalogModel & { disabled?: boolean }> = [
  {
    id: "openai/gpt-5.4",
    provider: "openai",
    model_name: "gpt-5.4",
    name: "GPT-5.4",
    description: "Default fast model",
    configured: true,
    supports_reasoning: true,
  },
  {
    id: "anthropic/claude-haiku",
    provider: "anthropic",
    model_name: "claude-haiku-4-5",
    name: "Claude Haiku",
    description: "Anthropic fast model",
    configured: false,
    disabled: true,
  },
];

describe("ModelPill", () => {
  it("renders the selected model name", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Model: GPT-5\.4/ }),
    ).toBeInTheDocument();
  });

  it("opens the menu on click and lists every option", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(
      screen.getByRole("menuitemradio", { name: /GPT-5\.4/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Claude Haiku/ }),
    ).toBeInTheDocument();
  });

  it("does not change selection when a disabled row is clicked", () => {
    const onChange = vi.fn();
    render(
      <ModelPill models={models} value="openai/gpt-5.4" onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.click(
      screen.getByRole("menuitemradio", { name: /Claude Haiku/ }),
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("invokes onChange and closes when an enabled row is clicked", () => {
    const onChange = vi.fn();
    render(
      <ModelPill
        models={[
          models[0],
          { ...models[1], disabled: false, configured: true },
        ]}
        value="openai/gpt-5.4"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.click(
      screen.getByRole("menuitemradio", { name: /Claude Haiku/ }),
    );
    expect(onChange).toHaveBeenCalledWith("anthropic/claude-haiku");
  });

  it("respects disabled prop on the trigger", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        disabled
      />,
    );
    expect(
      screen.getByRole("button", { name: /Model: GPT-5\.4/ }),
    ).toBeDisabled();
  });

  it("omits the custom-model field when onAddCustom is not provided", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(
      screen.queryByPlaceholderText(/vendor\/model/),
    ).not.toBeInTheDocument();
  });

  it("submits a custom OpenRouter slug via onAddCustom", () => {
    const onAddCustom = vi.fn();
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        onAddCustom={onAddCustom}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.change(screen.getByPlaceholderText(/vendor\/model/), {
      target: { value: "deepseek/deepseek-r1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));
    expect(onAddCustom).toHaveBeenCalledWith("deepseek/deepseek-r1");
  });

  // --- PR-3E: search + keyboard nav + grouping + enabled-only ---

  it("filters the list by the search box", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.change(
      screen.getByRole("searchbox", { name: /search models/i }),
      {
        target: { value: "haiku" },
      },
    );
    expect(
      screen.queryByRole("menuitemradio", { name: /GPT-5\.4/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Claude Haiku/ }),
    ).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.change(
      screen.getByRole("searchbox", { name: /search models/i }),
      {
        target: { value: "zzz-nothing" },
      },
    );
    expect(screen.getByText(/no models match/i)).toBeInTheDocument();
  });

  it("selects the highlighted model with arrow keys + Enter (skipping disabled)", () => {
    const onChange = vi.fn();
    const enabledModels: Array<ModelCatalogModel & { disabled?: boolean }> = [
      { ...models[0] }, // openai/gpt-5.4 (selectable)
      {
        id: "openai/gpt-5.4-mini",
        provider: "openai",
        model_name: "gpt-5.4-mini",
        name: "GPT-5.4 Mini",
        configured: true,
      },
    ];
    render(
      <ModelPill
        models={enabledModels}
        value="openai/gpt-5.4"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    const search = screen.getByRole("searchbox", { name: /search models/i });
    // From the selected (index 0) → down to Mini → Enter.
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("openai/gpt-5.4-mini");
  });

  it("hides enabled:false models but keeps the current selection visible", () => {
    const curated: Array<ModelCatalogModel & { disabled?: boolean }> = [
      { ...models[0], enabled: true },
      {
        id: "openai/hidden",
        provider: "openai",
        model_name: "hidden",
        name: "Hidden Model",
        configured: true,
        enabled: false,
      },
      {
        id: "openai/selected-but-off",
        provider: "openai",
        model_name: "selected-but-off",
        name: "Selected Off",
        configured: true,
        enabled: false,
      },
    ];
    render(
      <ModelPill
        models={curated}
        value="openai/selected-but-off"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Model: Selected Off/ }),
    );
    // enabled:false and not selected → hidden.
    expect(
      screen.queryByRole("menuitemradio", { name: /Hidden Model/ }),
    ).not.toBeInTheDocument();
    // enabled:false but IS the selection → still shown.
    expect(
      screen.getByRole("menuitemradio", { name: /Selected Off/ }),
    ).toBeInTheDocument();
  });
});
