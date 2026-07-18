// FR-5.12 — the 3-StepDots Add-provider-key flow. Happy path stores the
// plaintext exactly once via the injected `onSubmit`; a failed validation
// bounces to step 1 with a role="alert" and stores nothing.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AddProviderKeyModal } from "./AddProviderKeyModal";
import { providerCatalogEntry } from "./data/providerKeys";

const OPENAI = providerCatalogEntry("openai")!;
const FAKE_KEY = "sk-unit-test-placeholder-not-a-real-key";

function typeKey(value = FAKE_KEY): void {
  fireEvent.change(screen.getByTestId("add-key-input"), { target: { value } });
}

describe("<AddProviderKeyModal>", () => {
  it("opens on step 1 with a disabled Continue until a key is typed", () => {
    render(
      <AddProviderKeyModal
        open
        provider={OPENAI}
        onClose={() => undefined}
        onSubmit={() => Promise.resolve()}
      />,
    );
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 1 of 3",
    );
    const cont = screen.getByTestId("add-key-continue") as HTMLButtonElement;
    expect(cont.disabled).toBe(true);
    typeKey();
    expect(cont.disabled).toBe(false);
    // Masked input — never a text field that reveals the key.
    expect((screen.getByTestId("add-key-input") as HTMLInputElement).type).toBe(
      "password",
    );
  });

  it("validates, advances to choose-model, and stores the plaintext once", async () => {
    const onValidate = vi
      .fn<(key: string) => Promise<{ ok: boolean; models?: string[] }>>()
      .mockResolvedValue({ ok: true, models: ["gpt-4o", "o3"] });
    const onSubmit = vi
      .fn<(s: { apiKey: string; model: string }) => Promise<void>>()
      .mockResolvedValue(undefined);
    const onClose = vi.fn();

    render(
      <AddProviderKeyModal
        open
        provider={OPENAI}
        onClose={onClose}
        onValidate={onValidate}
        onSubmit={onSubmit}
      />,
    );

    typeKey();
    fireEvent.click(screen.getByTestId("add-key-continue"));

    // Step 2 — validating spinner announces the provider.
    expect(await screen.findByTestId("add-key-validating")).toHaveTextContent(
      /Validating with OpenAI/i,
    );
    expect(onValidate).toHaveBeenCalledWith(FAKE_KEY);

    // Step 3 — choose default model, then Add.
    const modelSelect = (await screen.findByTestId(
      "add-key-model",
    )) as HTMLSelectElement;
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 3 of 3",
    );
    fireEvent.change(modelSelect, { target: { value: "o3" } });
    fireEvent.click(screen.getByTestId("add-key-submit"));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({ apiKey: FAKE_KEY, model: "o3" });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("bounces a failed validation back to step 1 with an alert and stores nothing", async () => {
    const onValidate = vi
      .fn<(key: string) => Promise<{ ok: boolean; error?: string }>>()
      .mockResolvedValue({ ok: false, error: "Invalid key for OpenAI." });
    const onSubmit = vi
      .fn<(s: { apiKey: string; model: string }) => Promise<void>>()
      .mockResolvedValue(undefined);

    render(
      <AddProviderKeyModal
        open
        provider={OPENAI}
        onClose={() => undefined}
        onValidate={onValidate}
        onSubmit={onSubmit}
      />,
    );

    typeKey();
    fireEvent.click(screen.getByTestId("add-key-continue"));

    const alert = await screen.findByTestId("add-key-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("Invalid key for OpenAI.");
    // Back on step 1, key never stored.
    expect(screen.getByTestId("step-dots")).toHaveAttribute(
      "aria-label",
      "Step 1 of 3",
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("uses the built-in format check when no onValidate is injected", async () => {
    render(
      <AddProviderKeyModal
        open
        provider={OPENAI}
        onClose={() => undefined}
        onSubmit={() => Promise.resolve()}
      />,
    );
    // Wrong prefix → default checkProviderKeyFormat rejects on step 1.
    typeKey("nope-0000000000000000000000");
    fireEvent.click(screen.getByTestId("add-key-continue"));
    expect(await screen.findByTestId("add-key-error")).toHaveTextContent(
      /start with/i,
    );
    // A well-formed key advances to the model step.
    typeKey();
    fireEvent.click(screen.getByTestId("add-key-continue"));
    expect(await screen.findByTestId("add-key-model")).toBeInTheDocument();
  });

  it("keeps the flow open with an alert when the store rejects", async () => {
    const onSubmit = vi
      .fn<(s: { apiKey: string; model: string }) => Promise<void>>()
      .mockRejectedValue(new Error("Could not save the key."));
    const onClose = vi.fn();

    render(
      <AddProviderKeyModal
        open
        provider={OPENAI}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );

    typeKey();
    fireEvent.click(screen.getByTestId("add-key-continue"));
    fireEvent.click(await screen.findByTestId("add-key-submit"));

    expect(await screen.findByTestId("add-key-error")).toHaveTextContent(
      "Could not save the key.",
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
