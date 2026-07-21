// KeyForm — inline BYOK add-key (PRD-P1 §6.2). The plaintext key crosses
// exactly one call (`port.save`), never re-displayed; a rejected save alerts
// and does NOT connect; provider switch wipes the input (no plaintext leak).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProviderKeySummary } from "@0x-copilot/api-types";

import { KeyForm, type KeyFormConnected } from "./KeyForm";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

const ANTHROPIC_KEY = "sk-ant-unit-test-placeholder-not-real";
const OPENROUTER_KEY = "sk-or-v1-unit-test-placeholder-not-real";

function summary(provider: string, hint = "…abcd"): ProviderKeySummary {
  return {
    provider: provider as ProviderKeySummary["provider"],
    key_hint: hint,
    updated_at: new Date(0).toISOString(),
  };
}

/** A fake port whose `save` spy the caller keeps a reference to. */
function makePort(save: ProviderKeysPort["save"]): ProviderKeysPort {
  return {
    list: vi.fn(() => Promise.resolve([])),
    save,
    remove: vi.fn(() => Promise.resolve()),
  };
}

function okSave() {
  return vi.fn((provider: string) => Promise.resolve(summary(provider)));
}

function input(): HTMLInputElement {
  return screen.getByTestId("first-run-key-input") as HTMLInputElement;
}

describe("<KeyForm>", () => {
  it("defaults to Anthropic with an sk-ant placeholder and a masked input", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    // Tri-toggle default = first provider (Anthropic).
    const group = screen.getByTestId("segmented-control");
    expect(group.querySelector('[aria-checked="true"]')?.textContent).toContain(
      "Anthropic",
    );
    expect(input().placeholder).toBe("sk-…  paste your API key");
    // Never a text field that reveals the key.
    expect(input().type).toBe("password");
    // Privacy note is verbatim.
    expect(screen.getByTestId("first-run-key-note").textContent).toBe(
      "stored in your OS keychain — never uploaded",
    );
  });

  it("disables Connect until a key is typed", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    const connect = screen.getByTestId(
      "first-run-key-connect",
    ) as HTMLButtonElement;
    expect(connect.disabled).toBe(true);
    fireEvent.change(input(), { target: { value: ANTHROPIC_KEY } });
    expect(connect.disabled).toBe(false);
  });

  it("saves the plaintext exactly once and connects with the key_hint", async () => {
    const save = okSave();
    let connected: KeyFormConnected | null = null;
    render(
      <KeyForm port={makePort(save)} onConnected={(r) => (connected = r)} />,
    );

    fireEvent.change(input(), { target: { value: ANTHROPIC_KEY } });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));

    await waitFor(() => expect(connected).not.toBeNull());
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith("anthropic", ANTHROPIC_KEY);
    expect(connected).toMatchObject({
      provider: "anthropic",
      label: "Anthropic",
      dotColor: "#d97757",
      keyHint: "…abcd",
      modelId: null,
    });
  });

  it("switches provider + placeholder and clears any typed key (no leak)", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    fireEvent.change(input(), { target: { value: ANTHROPIC_KEY } });
    expect(input().value).toBe(ANTHROPIC_KEY);

    // Switch to OpenRouter via the segmented control.
    const openrouter = screen
      .getByTestId("segmented-control")
      .querySelector('[data-value="openrouter"]') as HTMLButtonElement;
    fireEvent.click(openrouter);

    expect(input().placeholder).toBe("sk-…  paste your API key");
    // Input wiped on switch — the Anthropic plaintext never carries over.
    expect(input().value).toBe("");
    fireEvent.change(input(), { target: { value: OPENROUTER_KEY } });
    expect(input().value).toBe(OPENROUTER_KEY);
  });

  it("rejects a malformed key client-side before any save", () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    // Wrong prefix for Anthropic.
    fireEvent.change(input(), {
      target: { value: "not-an-anthropic-key-xxxx" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));
    expect(screen.getByRole("alert").textContent).toContain(
      'Anthropic keys start with "sk-ant-"',
    );
    expect(save).not.toHaveBeenCalled();
  });

  it("surfaces a rejected save as role=alert and does NOT connect", async () => {
    const save = vi.fn(() =>
      Promise.reject(new Error("Provider rejected key")),
    );
    const onConnected = vi.fn();
    render(<KeyForm port={makePort(save)} onConnected={onConnected} />);

    fireEvent.change(input(), { target: { value: ANTHROPIC_KEY } });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Provider rejected key",
      ),
    );
    expect(onConnected).not.toHaveBeenCalled();
    // Connect is re-enabled so the user can retry.
    expect(
      (screen.getByTestId("first-run-key-connect") as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("renders per-option swatch dots as inline color data (not tokens)", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    const dots = Array.from(
      screen.getByTestId("segmented-control").querySelectorAll(".fr-kf__dot"),
    ) as HTMLElement[];
    expect(dots.map((d) => d.getAttribute("data-swatch"))).toEqual([
      "#d97757",
      "#6aa88f",
      "#9a7fd6",
    ]);
    // The swatch is inline data, never wired to --color-accent.
    expect(dots[0]?.style.backgroundColor).not.toContain("--color-accent");
  });
});
