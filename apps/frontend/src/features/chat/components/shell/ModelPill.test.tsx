import type { ModelCatalogModel } from "@enterprise-search/api-types";
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
});
