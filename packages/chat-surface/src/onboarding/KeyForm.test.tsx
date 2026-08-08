// KeyForm — inline BYOK add-key (PRD-P1 §6.2). The plaintext key crosses
// exactly one call (`port.save`), never re-displayed; a rejected save alerts
// and does NOT connect.
//
// There is no provider toggle: the form INFERS the provider from the pasted
// key. The cases that matter are therefore (a) each prefix resolving to the
// right provider, (b) the one case that resolves to nothing opening a picker
// rather than guessing, and (c) the verdict never being taken mid-keystroke.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProviderKeySummary } from "@0x-copilot/api-types";

import { KeyForm, type KeyFormConnected } from "./KeyForm";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

const ANTHROPIC_KEY = "sk-ant-unit-test-placeholder-not-real";
const OPENROUTER_KEY = "sk-or-v1-unit-test-placeholder-not-real";
const OPENAI_KEY = "sk-proj-unit-test-placeholder-not-real";
const VIRTUALS_KEY = "acp-unit-test-placeholder-not-real";
const UNKNOWN_KEY = "zz-unit-test-placeholder-not-real-0000";

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

function connectBtn(): HTMLButtonElement {
  return screen.getByTestId("first-run-key-connect") as HTMLButtonElement;
}

/** Type a key and take the verdict the way a paste would — via blur. */
function enterKey(value: string): void {
  fireEvent.change(input(), { target: { value } });
  fireEvent.blur(input());
}

function resolvedProvider(): string | null {
  return (
    screen
      .queryByTestId("first-run-key-resolved")
      ?.getAttribute("data-provider") ?? null
  );
}

describe("<KeyForm>", () => {
  it("opens on a bare field with nothing to choose", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);

    // The provider toggle is gone — the form asks one thing.
    expect(screen.queryByTestId("segmented-control")).toBeNull();
    expect(input().type).toBe("password"); // never reveals the key
    expect(input().placeholder).toBe("paste your API key");
    expect(connectBtn().disabled).toBe(true);
    expect(screen.getByTestId("first-run-key-note").textContent).toBe(
      "stored in your OS keychain — never uploaded",
    );
  });

  it.each([
    ["anthropic", ANTHROPIC_KEY, "Anthropic"],
    ["openrouter", OPENROUTER_KEY, "OpenRouter"],
    ["openai", OPENAI_KEY, "OpenAI"],
    ["virtuals", VIRTUALS_KEY, "Virtuals"],
  ])("infers %s from the key prefix", (id, key, label) => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey(key);

    expect(resolvedProvider()).toBe(id);
    // The destination is named on the button the user is about to press.
    expect(connectBtn().textContent).toContain(label);
    expect(connectBtn().disabled).toBe(false);
  });

  it("does NOT resolve mid-keystroke, so `sk-` never reads as OpenAI", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);

    // `sk-` is a prefix of `sk-ant-`. Typing towards an Anthropic key must not
    // flash "OpenAI" on the way — the verdict is not taken until paste/blur.
    for (const partial of ["s", "sk", "sk-", "sk-a", "sk-ant-"]) {
      fireEvent.change(input(), { target: { value: partial } });
      expect(screen.queryByTestId("first-run-key-resolved")).toBeNull();
    }

    fireEvent.change(input(), { target: { value: ANTHROPIC_KEY } });
    fireEvent.blur(input());
    expect(resolvedProvider()).toBe("anthropic");
  });

  it("never masks more than the last four characters", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey(ANTHROPIC_KEY);

    const shown = screen.getByTestId("first-run-key-edit").textContent ?? "";
    // The settled row must not become a way to read back a hidden key.
    expect(shown).not.toContain("sk-ant-");
    expect(shown.endsWith(ANTHROPIC_KEY.slice(-4))).toBe(true);
  });

  it("saves the plaintext exactly once and connects with the key_hint", async () => {
    const save = okSave();
    let connected: KeyFormConnected | null = null;
    render(
      <KeyForm port={makePort(save)} onConnected={(r) => (connected = r)} />,
    );

    enterKey(ANTHROPIC_KEY);
    fireEvent.click(connectBtn());

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

  it("asks rather than guesses when no prefix matches", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey(UNKNOWN_KEY);

    // No provider was invented…
    expect(resolvedProvider()).toBe("");
    expect(connectBtn().disabled).toBe(true);
    expect(screen.getByTestId("first-run-key-unknown").textContent).toBe(
      "We can't tell whose key this is.",
    );
    // …and the fallback picker is open, unasked.
    expect(screen.getByTestId("first-run-key-picker")).toBeInTheDocument();
  });

  it("lets the user pick a provider for an unrecognised key", async () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    enterKey(UNKNOWN_KEY);

    fireEvent.click(screen.getByTestId("first-run-key-pick-virtuals"));

    expect(resolvedProvider()).toBe("virtuals");
    expect(connectBtn().textContent).toContain("Virtuals");
    fireEvent.click(connectBtn());
    // The key the user pasted is the key that is sent — picking a provider
    // must not wipe it, which is the whole point of correcting a guess.
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("virtuals", UNKNOWN_KEY),
    );
  });

  it("lets the user override a WRONG inference without retyping", async () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    // A Virtuals key that happens to start `sk-` infers as OpenAI.
    enterKey(OPENAI_KEY);
    expect(resolvedProvider()).toBe("openai");

    fireEvent.click(screen.getByTestId("first-run-key-change"));
    fireEvent.click(screen.getByTestId("first-run-key-pick-virtuals"));

    expect(resolvedProvider()).toBe("virtuals");
    fireEvent.click(connectBtn());
    // The pasted key survives the correction — retyping it would defeat the
    // entire point of letting the user fix a guess.
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("virtuals", OPENAI_KEY),
    );
  });

  it("still vetoes an override that contradicts a KNOWN format", () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    enterKey(OPENAI_KEY);
    fireEvent.click(screen.getByTestId("first-run-key-change"));
    fireEvent.click(screen.getByTestId("first-run-key-pick-openrouter"));
    fireEvent.click(connectBtn());

    // Overriding is not a licence to send a key somewhere it demonstrably does
    // not belong: OpenRouter's prefix is documented, so the format check holds.
    // Only providers whose format is UNCONFIRMED (Virtuals) are exempt.
    expect(screen.getByRole("alert").textContent).toContain("sk-or-");
    expect(save).not.toHaveBeenCalled();
  });

  it("drops a prior verdict when the key is edited", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey(OPENAI_KEY);
    expect(resolvedProvider()).toBe("openai");

    // Back to editing, then a different vendor's key.
    fireEvent.click(screen.getByTestId("first-run-key-edit"));
    enterKey(OPENROUTER_KEY);

    // The new key must not inherit the provider chosen for the old one.
    expect(resolvedProvider()).toBe("openrouter");
  });

  it("accepts a Virtuals key that does NOT carry the acp- prefix", async () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    enterKey(UNKNOWN_KEY);
    fireEvent.click(screen.getByTestId("first-run-key-pick-virtuals"));
    fireEvent.click(connectBtn());

    // `acp-` is advisory: good enough to RECOGNISE a Virtuals key, never used
    // to reject one. The prefix is not in Virtuals' public docs, so enforcing
    // it would 400 a valid key with no way for the user to override.
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("rejects a malformed key client-side before any save", () => {
    const save = okSave();
    render(<KeyForm port={makePort(save)} onConnected={() => undefined} />);
    // Long enough to settle, but carries no known prefix — so the picker opens
    // and the user names Anthropic, whose documented format it fails.
    enterKey(UNKNOWN_KEY);
    fireEvent.click(screen.getByTestId("first-run-key-pick-anthropic"));
    fireEvent.click(connectBtn());

    expect(screen.getByRole("alert").textContent).toContain(
      'Anthropic keys start with "sk-ant-"',
    );
    expect(save).not.toHaveBeenCalled();
  });

  it("treats a too-short key as still being typed", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey("sk-ant-");

    // No verdict, no picker, no error — the user is mid-paste, not wrong.
    expect(screen.queryByTestId("first-run-key-resolved")).toBeNull();
    expect(screen.queryByTestId("first-run-key-picker")).toBeNull();
    expect(connectBtn().disabled).toBe(true);
  });

  it("surfaces a rejected save as role=alert and does NOT connect", async () => {
    const save = vi.fn(() =>
      Promise.reject(new Error("Provider rejected key")),
    );
    const onConnected = vi.fn();
    render(<KeyForm port={makePort(save)} onConnected={onConnected} />);

    enterKey(ANTHROPIC_KEY);
    fireEvent.click(connectBtn());

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Provider rejected key",
      ),
    );
    expect(onConnected).not.toHaveBeenCalled();
    // Connect is re-enabled so the user can retry.
    expect(connectBtn().disabled).toBe(false);
  });

  it("renders swatch dots as inline color data (not tokens)", () => {
    render(<KeyForm port={makePort(okSave())} onConnected={() => undefined} />);
    enterKey(UNKNOWN_KEY); // opens the picker, which carries every dot

    const dots = Array.from(
      screen
        .getByTestId("first-run-key-picker")
        .querySelectorAll(".fr-kf__dot"),
    ) as HTMLElement[];
    expect(dots.map((d) => d.getAttribute("data-swatch"))).toEqual([
      "#5ad1e8",
      "#d97757",
      "#6aa88f",
      "#9a7fd6",
    ]);
    // The swatch is inline data, never wired to --color-accent.
    expect(dots[0]?.style.backgroundColor).not.toContain("--color-accent");
  });
});
