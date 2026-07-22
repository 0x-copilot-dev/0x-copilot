// Composer model picker — v3 design (PR-4F). A quiet, grouped popover:
// "Your keys" (cloud) + "Local · on-device", radio selection, footer
// deep-links, custom-slug add. No search (that lives in Settings → Models).

import type {
  ModelCatalogModel,
  ProviderKeySummary,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelPill } from "./ModelPill";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

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
  {
    id: "llama-3.3-70b",
    provider: "ollama",
    model_name: "llama-3.3-70b",
    name: "Llama 3.3 70B",
    configured: true,
  },
];

describe("ModelPill (v3)", () => {
  it("renders the selected model name on the trigger", () => {
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

  it("groups models into Your keys and Local · on-device", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(screen.getByText("Your keys")).toBeInTheDocument();
    expect(screen.getByText("Local · on-device")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /GPT-5\.4/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Llama 3\.3 70B/ }),
    ).toBeInTheDocument();
    // Sub-line renders the v3 idiom.
    expect(screen.getByText(/OpenAI · your key/)).toBeInTheDocument();
    expect(screen.getByText(/never leaves this machine/)).toBeInTheDocument();
  });

  it("selects an enabled model and closes", () => {
    const onChange = vi.fn();
    render(
      <ModelPill models={models} value="openai/gpt-5.4" onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.click(
      screen.getByRole("menuitemradio", { name: /Llama 3\.3 70B/ }),
    );
    expect(onChange).toHaveBeenCalledWith("llama-3.3-70b");
  });

  it("does not select a disabled (needs-key) row", () => {
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
        id: "openai/selected-off",
        provider: "openai",
        model_name: "selected-off",
        name: "Selected Off",
        configured: true,
        enabled: false,
      },
    ];
    render(
      <ModelPill
        models={curated}
        value="openai/selected-off"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Model: Selected Off/ }),
    );
    expect(
      screen.queryByRole("menuitemradio", { name: /Hidden Model/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Selected Off/ }),
    ).toBeInTheDocument();
  });

  it("renders footer deep-links and fires their callbacks", () => {
    const onAddProviderKey = vi.fn();
    const onGetLocalModels = vi.fn();
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        onAddProviderKey={onAddProviderKey}
        onGetLocalModels={onGetLocalModels}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add a provider key/ }));
    expect(onAddProviderKey).toHaveBeenCalled();
  });

  it("omits the footer when no deep-link callbacks are provided", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(
      screen.queryByRole("button", { name: /Add a provider key/ }),
    ).not.toBeInTheDocument();
  });

  it("respects the disabled prop on the trigger", () => {
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

  it("opens the inline KeyForm sub-view and connects a key via providerKeysPort", async () => {
    const summary = {
      provider: "anthropic",
      key_hint: "…wxyz",
    } as unknown as ProviderKeySummary;
    const save = vi.fn().mockResolvedValue(summary);
    const port: ProviderKeysPort = {
      list: vi.fn().mockResolvedValue([]),
      save,
      remove: vi.fn().mockResolvedValue(undefined),
    };
    const onProviderKeyAdded = vi.fn();
    const onAddProviderKey = vi.fn();

    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        providerKeysPort={port}
        onProviderKeyAdded={onProviderKeyAdded}
        onAddProviderKey={onAddProviderKey}
      />,
    );

    // Open the popover, then the inline add-key affordance.
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add a provider key/ }));

    // providerKeysPort wins over the deep-link: the inline KeyForm renders and
    // onAddProviderKey never fires.
    expect(screen.getByTestId("first-run-keyform")).toBeInTheDocument();
    expect(onAddProviderKey).not.toHaveBeenCalled();

    // Type a well-formed Anthropic key (the default first provider) and connect.
    fireEvent.change(screen.getByTestId("first-run-key-input"), {
      target: { value: "sk-ant-0123456789012345678901234" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "anthropic",
        "sk-ant-0123456789012345678901234",
      ),
    );
    await waitFor(() =>
      expect(onProviderKeyAdded).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "anthropic", keyHint: "…wxyz" }),
      ),
    );

    // Sub-view closed on success (popover collapses too).
    expect(screen.queryByTestId("first-run-keyform")).not.toBeInTheDocument();
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
});
