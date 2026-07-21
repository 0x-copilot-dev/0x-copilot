// FirstRunSurface — shell + state machine (PRD-P1 §6.3). Top bar (brand + 0x
// accent span + wallet slot + skip), state transitions (choice → dl / ready →
// sent), footer, and the P2/P3 slot injection points.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProviderKeySummary } from "@0x-copilot/api-types";

import { FirstRunSurface } from "./FirstRunSurface";
import { FIRST_RUN_COPY } from "./firstRun";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

function fakePort(save?: ProviderKeysPort["save"]): ProviderKeysPort {
  return {
    list: vi.fn(() => Promise.resolve([])),
    save:
      save ??
      vi.fn((provider: string) =>
        Promise.resolve({
          provider: provider as ProviderKeySummary["provider"],
          key_hint: "…zzzz",
          updated_at: new Date(0).toISOString(),
        }),
      ),
    remove: vi.fn(() => Promise.resolve()),
  };
}

function renderSurface(overrides = {}) {
  const onSkip = vi.fn();
  const onComplete = vi.fn();
  render(
    <FirstRunSurface
      providerKeys={fakePort()}
      onSkip={onSkip}
      onComplete={onComplete}
      {...overrides}
    />,
  );
  return { onSkip, onComplete };
}

describe("<FirstRunSurface>", () => {
  it("renders the brand with the 0x lead in an accent span + skip link", () => {
    renderSurface();
    const brand = screen.getByTestId("first-run-brand");
    const zx = brand.querySelector(".fr-brand__zx");
    expect(zx?.textContent).toBe(FIRST_RUN_COPY.topbar.brandLead); // "0x"
    expect(brand.textContent).toContain(FIRST_RUN_COPY.topbar.brandRest); // "Copilot"
    expect(screen.getByTestId("first-run-skip").textContent).toBe(
      FIRST_RUN_COPY.topbar.skip,
    );
  });

  it("renders an injected wallet chip slot (P4) when provided", () => {
    renderSurface({
      walletChipSlot: <span data-testid="p4-chip">0x7f3C…a92C</span>,
    });
    expect(screen.getByTestId("first-run-wallet-slot")).not.toBeNull();
    expect(screen.getByTestId("p4-chip")).not.toBeNull();
  });

  it("shows the footer version (default) + privacy line", () => {
    renderSurface();
    const foot = screen.getByTestId("first-run-footer");
    expect(foot.textContent).toContain(FIRST_RUN_COPY.footer.left);
    expect(foot.textContent).toContain(FIRST_RUN_COPY.footer.right);
  });

  it("honors a custom appVersion in the footer", () => {
    renderSurface({ appVersion: "v9.9.9 · custom" });
    expect(screen.getByTestId("first-run-footer").textContent).toContain(
      "v9.9.9 · custom",
    );
  });

  it("skip calls onSkip", () => {
    const { onSkip } = renderSurface();
    fireEvent.click(screen.getByTestId("first-run-skip"));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("Start download advances to the dl composer slot (P3 placeholder)", () => {
    renderSurface();
    expect(screen.getByTestId("first-run-gate")).not.toBeNull();
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    // Gate is gone; the dl body (placeholder) shows.
    expect(screen.queryByTestId("first-run-gate")).toBeNull();
    expect(screen.getByTestId("first-run-composer-placeholder")).not.toBeNull();
  });

  it("KeyForm connect advances to ready with engine.kind==='key'", async () => {
    let composerEngineKind: string | undefined;
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={() => undefined}
        renderComposer={(ctx) => {
          composerEngineKind = ctx.engine?.kind;
          return (
            <div data-testid="p3-composer" data-stage={ctx.stage}>
              ready:{String(ctx.modelReady)}
            </div>
          );
        }}
      />,
    );

    fireEvent.click(screen.getByTestId("first-run-add-key"));
    fireEvent.change(screen.getByTestId("first-run-key-input"), {
      target: { value: "sk-ant-unit-test-placeholder-not-real" },
    });
    fireEvent.click(screen.getByTestId("first-run-key-connect"));

    await waitFor(() =>
      expect(screen.getByTestId("p3-composer")).not.toBeNull(),
    );
    expect(screen.getByTestId("p3-composer").getAttribute("data-stage")).toBe(
      "ready",
    );
    expect(composerEngineKind).toBe("key");
    // A BYOK engine is model-ready immediately.
    expect(screen.getByTestId("p3-composer").textContent).toContain(
      "ready:true",
    );
  });

  it("renders injected composer + acknowledgment slots (P3)", async () => {
    const onComplete = vi.fn();
    render(
      <FirstRunSurface
        providerKeys={fakePort()}
        onSkip={() => undefined}
        onComplete={onComplete}
        initialStage="ready"
        renderComposer={(ctx) => (
          <button type="button" data-testid="p3-send" onClick={ctx.onSent}>
            send
          </button>
        )}
        renderAcknowledgment={(ctx) => (
          <div data-testid="p3-ack">
            <button
              type="button"
              data-testid="p3-handoff"
              onClick={ctx.onComplete}
            >
              done
            </button>
          </div>
        )}
      />,
    );

    // Composer slot shows for stage=ready.
    expect(screen.getByTestId("p3-send")).not.toBeNull();
    // Sending flips to the ack slot; the slot owns the handoff timing.
    fireEvent.click(screen.getByTestId("p3-send"));
    expect(screen.getByTestId("p3-ack")).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("p3-handoff"));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("P1 placeholder ack fires onComplete once on send (no P3 slot)", async () => {
    const { onComplete } = renderSurface({ initialStage: "ready" });
    // Placeholder composer → send.
    fireEvent.click(screen.getByTestId("first-run-placeholder-send"));
    await waitFor(() =>
      expect(screen.getByTestId("first-run-ack-placeholder")).not.toBeNull(),
    );
    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1));
  });

  it("honors initialStage for tests", () => {
    renderSurface({ initialStage: "dl" });
    // Straight to the composer body — no gate.
    expect(screen.queryByTestId("first-run-gate")).toBeNull();
    expect(screen.getByTestId("first-run-composer-placeholder")).not.toBeNull();
  });

  it("renders no raw hex in the surface except provider dot swatches", () => {
    renderSurface();
    // Reveal the KeyForm so the swatches are in the DOM.
    fireEvent.click(screen.getByTestId("first-run-add-key"));
    const root = screen.getByTestId("first-run-surface");
    const swatches = new Set(
      Array.from(root.querySelectorAll("[data-swatch]")).map(
        (el) => (el as HTMLElement).style.backgroundColor,
      ),
    );
    // Every inline style with a hex color must be a declared provider swatch.
    const withInlineHex = Array.from(
      root.querySelectorAll<HTMLElement>("[style]"),
    ).filter((el) => /#[0-9a-f]{3,8}/i.test(el.getAttribute("style") ?? ""));
    for (const el of withInlineHex) {
      expect(el.hasAttribute("data-swatch")).toBe(true);
    }
    // BrandMark's fixed sky gradient stops (#9bd4ff/#4593d8) live in an <svg>,
    // not inline style attributes — so the assertion above stays swatch-only.
    expect(swatches.size).toBeGreaterThan(0);
  });
});
