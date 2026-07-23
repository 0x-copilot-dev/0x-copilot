// Composer model picker — v3 design (PR-4F). A quiet, grouped popover:
// "Your keys" (cloud) + "Local · on-device", radio selection, footer
// deep-links, custom-slug add. No search (that lives in Settings → Models).

import type {
  ModelCatalogModel,
  ProviderKeySummary,
} from "@0x-copilot/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { formatModelSize, ModelPill } from "./ModelPill";
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

  // --- v3 popover structure (rows 23-36) -----------------------------------

  // The `Menu` primitive portals out of `container`, so the popover is queried
  // through `baseElement` — chat-surface bans a bare `document`, tests included.
  it("renders the design's header + scrollable list frame", () => {
    const { baseElement } = render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));

    // Header: title + right-aligned "this chat" meta (design `.pop__h`).
    const header = baseElement.querySelector(".ui-pop__h");
    expect(header).not.toBeNull();
    expect(header?.textContent).toContain("Model");
    expect(header?.querySelector(".ui-pop__h-meta")?.textContent).toBe(
      "this chat",
    );

    // The frame is the shared `.ui-pop` recipe, not the bare dropdown menu…
    const frame = baseElement.querySelector(".atlas-model-pill__menu");
    expect(frame?.classList.contains("ui-pop")).toBe(true);
    // …and the ONE scroll region wraps every row (design `.pop__list`).
    const list = frame?.querySelector(".ui-pop__list");
    expect(list).not.toBeNull();
    for (const row of screen.getAllByRole("menuitemradio")) {
      expect(list?.contains(row)).toBe(true);
    }
  });

  it("uses the mono popover group heading, not the sans section label", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    const head = screen.getByText("Your keys");
    expect(head.className).toContain("ui-pop__grp");
    // `.ui-section-label` is sans 11.2px/600 and belongs to page sections —
    // other surfaces depend on it, so the popover must not reuse it here.
    expect(head.className).not.toContain("ui-section-label");
  });

  it("marks selection with the radio only — never a row fill", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    const selected = screen.getByRole("menuitemradio", { name: /GPT-5\.4/ });
    const other = screen.getByRole("menuitemradio", { name: /Llama 3\.3 70B/ });

    expect(selected.className).toContain("ui-pop-row");
    expect(selected.getAttribute("data-on")).toBe("true");
    expect(other.getAttribute("data-on")).toBeNull();
    // The radio (not the row) carries the filled state.
    expect(
      selected.querySelector(".ui-pop-row__rad")?.querySelector("svg"),
    ).not.toBeNull();
    expect(
      other.querySelector(".ui-pop-row__rad")?.querySelector("svg"),
    ).toBeNull();
  });

  it("renders a provider mark in the row badge — never a single letter", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    const badge = screen
      .getByRole("menuitemradio", { name: /GPT-5\.4/ })
      .querySelector(".ui-pop-row__lg");
    // OpenAI ships a bundled mark → an <svg>, not the old "O" text badge.
    expect(badge?.querySelector("svg")).not.toBeNull();
    expect(badge?.textContent).toBe("");

    // Local rows render the chip glyph (not the literal "◇" character).
    const localBadge = screen
      .getByRole("menuitemradio", { name: /Llama 3\.3 70B/ })
      .querySelector(".ui-pop-row__lg");
    expect(localBadge?.querySelector("svg")).not.toBeNull();
    expect(localBadge?.textContent).not.toContain("◇");
  });

  it("colours the trigger dot with the provider's brand hue, not the accent", () => {
    const { container } = render(
      <ModelPill
        models={models}
        value="anthropic/claude-haiku"
        onChange={() => undefined}
      />,
    );
    const dot = container.querySelector(".ui-cpill__dot") as HTMLElement | null;
    expect(dot).not.toBeNull();
    expect(dot?.style.background).toBe("rgb(217, 119, 87)"); // #d97757
    expect(dot?.style.background).not.toContain("--color-accent");
  });

  it("swaps the trigger dot for the chip glyph on a local selection", () => {
    render(
      <ModelPill
        models={models}
        value="llama-3.3-70b"
        onChange={() => undefined}
      />,
    );
    const trigger = screen.getByRole("button", {
      name: /Model: Llama 3\.3 70B/,
    });
    expect(trigger.querySelector(".ui-cpill__dot")).toBeNull();
    expect(trigger.querySelector("svg")).not.toBeNull();
  });

  it("fires the Get local models deep-link and closes the popover", () => {
    const onGetLocalModels = vi.fn();
    const { baseElement } = render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        onAddProviderKey={() => undefined}
        onGetLocalModels={onGetLocalModels}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    // The footer + its flex spacer render only when the host wires the link.
    expect(baseElement.querySelector(".ui-pop__f-sp")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Get local models/ }));
    expect(onGetLocalModels).toHaveBeenCalled();
    expect(
      screen.queryByRole("menuitemradio", { name: /GPT-5\.4/ }),
    ).not.toBeInTheDocument();
  });

  it("reads the joined on-disk size on a local row's sub-line", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
        localModelSizes={{ "llama-3.3-70b": 42_000_000_000 }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(
      screen.getByText("42 GB · never leaves this machine"),
    ).toBeInTheDocument();
  });

  it("falls back to the generic local sub-line when no size is joined", () => {
    render(
      <ModelPill
        models={models}
        value="openai/gpt-5.4"
        onChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Model: GPT-5\.4/ }));
    expect(
      screen.getByText("local · never leaves this machine"),
    ).toBeInTheDocument();
  });
});

describe("formatModelSize", () => {
  it("formats the design's decimal GB idiom", () => {
    expect(formatModelSize(42_000_000_000)).toBe("42 GB");
    expect(formatModelSize(4_700_000_000)).toBe("4.7 GB");
    expect(formatModelSize(650_000_000)).toBe("650 MB");
  });

  it("answers null for a missing or nonsensical size", () => {
    // A `null`-ish size must not become "0 GB" — the sub-line falls back
    // to the honest generic lead instead.
    expect(formatModelSize(undefined)).toBeNull();
    expect(formatModelSize(0)).toBeNull();
    expect(formatModelSize(-1)).toBeNull();
    expect(formatModelSize(Number.NaN)).toBeNull();
  });
});
