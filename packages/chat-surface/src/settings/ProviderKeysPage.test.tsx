// FR-5.11 / FR-5.13 — the Provider keys (BYOK) section. Empty providers render
// "Add key" rows; a stored provider shows the masked hint + Rotate/Remove with
// no plaintext-reveal affordance; the keychain note is always present. Add /
// Remove route through the injected port; there is no dirty savebar.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProviderKeySummary } from "@0x-copilot/api-types";

import {
  PROVIDER_KEYS_KEYCHAIN_NOTE,
  ProviderKeysPage,
} from "./ProviderKeysPage";
import type { ProviderKeysPort } from "./data/providerKeys";

const SAVED_ANTHROPIC: ProviderKeySummary = {
  provider: "anthropic",
  key_hint: "…7890",
  updated_at: "2026-07-01T10:00:00Z",
};

const FAKE_KEY = "sk-unit-test-placeholder-not-a-real-key";

function makePort(overrides: Partial<ProviderKeysPort> = {}): ProviderKeysPort {
  return {
    list: vi.fn<ProviderKeysPort["list"]>().mockResolvedValue([]),
    save: vi
      .fn<ProviderKeysPort["save"]>()
      .mockImplementation(async (provider: string) => ({
        provider: provider as ProviderKeySummary["provider"],
        key_hint: "…real",
        updated_at: "2026-07-18T00:00:00Z",
      })),
    remove: vi.fn<ProviderKeysPort["remove"]>().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<ProviderKeysPage>", () => {
  it("uses the design IA: a 17px section heading over separate Connected / Add-a-provider cards", async () => {
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]) })}
      />,
    );
    await screen.findByTestId("provider-row-anthropic");
    // The section title is now the top-of-hierarchy <h1> (SecTitle), NOT a card
    // title, and the sub-groups are separate cards with <h3> titles + meta.
    expect(
      screen.getByRole("heading", { level: 1, name: "Provider keys" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Connected" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 active")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "Add a provider" }),
    ).toBeInTheDocument();
  });

  it("renders every provider as an Add-key row when nothing is stored", async () => {
    render(<ProviderKeysPage port={makePort()} />);
    await screen.findByTestId("provider-add-anthropic");
    for (const slug of [
      "anthropic",
      "openai",
      "openrouter",
      "google",
      "groq",
      "xai",
    ]) {
      expect(screen.getByTestId(`provider-add-${slug}`)).toBeInTheDocument();
    }
    expect(screen.getByText(PROVIDER_KEYS_KEYCHAIN_NOTE)).toBeInTheDocument();
    // The generic CTA row replaces the plain note: a "Another provider" label +
    // an OpenAI-compatible hint + a primary "Add a key" (PR-F.2).
    const generic = screen.getByTestId("provider-compatible-note");
    expect(generic).toHaveTextContent("Another provider");
    expect(generic).toHaveTextContent(
      "Any OpenAI-compatible endpoint works too.",
    );
    const genericAdd = screen.getByTestId("provider-add-generic");
    expect(genericAdd).toHaveAttribute("aria-label", "Add a key");
    expect(genericAdd).not.toBeDisabled();
  });

  it("per-empty-provider Add key is a neutral (secondary) button, not accent-filled", async () => {
    render(<ProviderKeysPage port={makePort()} />);
    const add = await screen.findByTestId("provider-add-openai");
    expect(add).toHaveClass("ui-button--secondary");
    expect(add).not.toHaveClass("ui-button--primary");
  });

  it("the generic primary Add-a-key opens the modal for the first available provider", async () => {
    render(<ProviderKeysPage port={makePort()} />);
    fireEvent.click(await screen.findByTestId("provider-add-generic"));
    // Modal opens (its key input mounts) for the first catalog provider.
    expect(await screen.findByTestId("add-key-input")).toBeInTheDocument();
  });

  it("disables the generic Add-a-key when every provider already has a key", async () => {
    // Every catalog provider stored → nothing available to add.
    const stored: ProviderKeySummary[] = [
      "anthropic",
      "openai",
      "openrouter",
      "google",
      "groq",
      "xai",
    ].map((slug) => ({
      provider: slug as ProviderKeySummary["provider"],
      key_hint: "…real",
      updated_at: "2026-07-18T00:00:00Z",
    }));
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue(stored) })}
      />,
    );
    await screen.findByTestId("provider-row-openai");
    expect(screen.getByTestId("provider-add-generic")).toBeDisabled();
  });

  it("shows the masked hint + Rotate/Remove for a stored provider and never reveals plaintext", async () => {
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]) })}
      />,
    );
    await screen.findByTestId("provider-row-anthropic");
    expect(screen.getByText(/…7890/)).toBeInTheDocument();
    // Rotate is now a ghost button (PR-F.2).
    const rotate = screen.getByRole("button", {
      name: /rotate anthropic key/i,
    });
    expect(rotate).toHaveClass("ui-button--ghost");
    // Remove is a ghost icon button (a trash glyph), keeping its aria-label.
    const remove = screen.getByRole("button", {
      name: /remove anthropic key/i,
    });
    expect(remove).toHaveClass("ui-button--ghost");
    expect(remove).not.toHaveClass("ui-button--danger");
    expect(remove).not.toHaveTextContent("Remove");
    expect(remove.querySelector("svg")).not.toBeNull();
    // Stored provider drops out of the Add list; no password input on the page.
    expect(screen.queryByTestId("provider-add-anthropic")).toBeNull();
    expect(screen.queryByTestId("add-key-input")).toBeNull();
  });

  it("renders the model chip in the success tone on a connected row (PR-F.2)", async () => {
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]) })}
        modelChips={{ anthropic: "claude-opus-4" }}
      />,
    );
    const chip = await screen.findByTestId("provider-model-chip-anthropic");
    expect(chip).toHaveTextContent("claude-opus-4");
    expect(chip).toHaveClass("ui-badge--success");
  });

  it("prefers the summary's default_model over the modelChips fallback (PR-F.5)", async () => {
    const summaryWithModel: ProviderKeySummary = {
      ...SAVED_ANTHROPIC,
      default_model: "claude-sonnet-4",
    };
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([summaryWithModel]) })}
        modelChips={{ anthropic: "claude-opus-4" }}
      />,
    );
    const chip = await screen.findByTestId("provider-model-chip-anthropic");
    // Server-projected default wins over the host-supplied fallback chip.
    expect(chip).toHaveTextContent("claude-sonnet-4");
    expect(chip).not.toHaveTextContent("claude-opus-4");
    expect(chip).toHaveClass("ui-badge--success");
  });

  it("falls back to modelChips when the summary carries no default_model (PR-F.5)", async () => {
    // Older server / key stored without a model → summary.default_model absent.
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]) })}
        modelChips={{ anthropic: "claude-opus-4" }}
      />,
    );
    const chip = await screen.findByTestId("provider-model-chip-anthropic");
    expect(chip).toHaveTextContent("claude-opus-4");
  });

  it("adds a key through the flow, storing the plaintext once and toasting", async () => {
    const port = makePort();
    const onToast = vi.fn();
    render(<ProviderKeysPage port={port} onToast={onToast} />);

    fireEvent.click(await screen.findByTestId("provider-add-openai"));
    fireEvent.change(screen.getByTestId("add-key-input"), {
      target: { value: FAKE_KEY },
    });
    fireEvent.click(screen.getByTestId("add-key-continue"));
    fireEvent.click(await screen.findByTestId("add-key-submit"));

    await waitFor(() => expect(port.save).toHaveBeenCalledTimes(1));
    // The step-3 pick rides the same PUT (PR-F.5 per-provider default_model).
    expect(port.save).toHaveBeenCalledWith("openai", FAKE_KEY, "gpt-4o");
    // Row flips to connected with the chosen model chip; onToast fires.
    await screen.findByTestId("provider-row-openai");
    expect(screen.getByTestId("provider-model-chip-openai")).toHaveTextContent(
      "gpt-4o",
    );
    expect(onToast).toHaveBeenCalledWith("OpenAI key added.");
  });

  it("persists the step-3 pick as the workspace default when the port supports it", async () => {
    const saveDefaultModel = vi
      .fn<NonNullable<ProviderKeysPort["saveDefaultModel"]>>()
      .mockResolvedValue(undefined);
    const port = makePort({ saveDefaultModel });
    const onToast = vi.fn();
    render(<ProviderKeysPage port={port} onToast={onToast} />);

    fireEvent.click(await screen.findByTestId("provider-add-openai"));
    fireEvent.change(screen.getByTestId("add-key-input"), {
      target: { value: FAKE_KEY },
    });
    fireEvent.click(screen.getByTestId("add-key-continue"));
    fireEvent.click(await screen.findByTestId("add-key-submit"));

    await waitFor(() =>
      expect(saveDefaultModel).toHaveBeenCalledWith("openai", "gpt-4o"),
    );
    expect(onToast).toHaveBeenCalledWith(
      "OpenAI key added · gpt-4o is your default model.",
    );
  });

  it("keeps the key add honest when the defaults write fails", async () => {
    const saveDefaultModel = vi
      .fn<NonNullable<ProviderKeysPort["saveDefaultModel"]>>()
      .mockRejectedValue(new Error("defaults unavailable"));
    const port = makePort({ saveDefaultModel });
    const onToast = vi.fn();
    render(<ProviderKeysPage port={port} onToast={onToast} />);

    fireEvent.click(await screen.findByTestId("provider-add-openai"));
    fireEvent.change(screen.getByTestId("add-key-input"), {
      target: { value: FAKE_KEY },
    });
    fireEvent.click(screen.getByTestId("add-key-continue"));
    fireEvent.click(await screen.findByTestId("add-key-submit"));

    // The key row still lands (the save succeeded) and the copy says exactly
    // which half failed.
    await screen.findByTestId("provider-row-openai");
    expect(onToast).toHaveBeenCalledWith(
      "OpenAI key added. Saving the default model failed — set it in Model & behavior.",
    );
  });

  it("removes a stored key via the port and toasts", async () => {
    const port = makePort({
      list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]),
    });
    const onToast = vi.fn();
    render(<ProviderKeysPage port={port} onToast={onToast} />);

    fireEvent.click(await screen.findByTestId("provider-remove-anthropic"));

    await waitFor(() => expect(port.remove).toHaveBeenCalledWith("anthropic"));
    await waitFor(() =>
      expect(screen.queryByTestId("provider-row-anthropic")).toBeNull(),
    );
    expect(onToast).toHaveBeenCalledWith("Anthropic key removed.");
  });

  it("surfaces a load error with a Retry that re-lists", async () => {
    const list = vi
      .fn<ProviderKeysPort["list"]>()
      .mockRejectedValueOnce(new Error("provider keys unavailable"))
      .mockResolvedValue([]);
    render(<ProviderKeysPage port={makePort({ list })} />);

    const alert = await screen.findByTestId("provider-keys-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("provider keys unavailable");

    fireEvent.click(screen.getByTestId("provider-keys-retry"));
    await screen.findByTestId("provider-add-openai");
    expect(list).toHaveBeenCalledTimes(2);
  });
});
