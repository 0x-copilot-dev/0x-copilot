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
    expect(screen.getByTestId("provider-compatible-note")).toBeInTheDocument();
  });

  it("shows the masked hint + Rotate/Remove for a stored provider and never reveals plaintext", async () => {
    render(
      <ProviderKeysPage
        port={makePort({ list: vi.fn().mockResolvedValue([SAVED_ANTHROPIC]) })}
      />,
    );
    await screen.findByTestId("provider-row-anthropic");
    expect(screen.getByText(/…7890/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /rotate anthropic key/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /remove anthropic key/i }),
    ).toBeInTheDocument();
    // Stored provider drops out of the Add list; no password input on the page.
    expect(screen.queryByTestId("provider-add-anthropic")).toBeNull();
    expect(screen.queryByTestId("add-key-input")).toBeNull();
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
    expect(port.save).toHaveBeenCalledWith("openai", FAKE_KEY);
    // Row flips to connected with the chosen model chip; onToast fires.
    await screen.findByTestId("provider-row-openai");
    expect(screen.getByTestId("provider-model-chip-openai")).toHaveTextContent(
      "gpt-4o",
    );
    expect(onToast).toHaveBeenCalledWith("OpenAI key added.");
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
