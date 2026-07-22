// Gate — State A (PRD-P1 §6.1). Both cards render with verbatim copy;
// "Start download" fires onStartDownload; "Add a key" reveals the inline
// KeyForm; a renderLocalCard slot replaces the default local card.
//
// PRD-P8 D4 splits the single advance callback in two — `onStartDownload`
// (explicit click: start the pull AND advance) and `onContinue` (D4a: advance
// only, without restarting an in-flight pull). The Gate's job is to hand BOTH
// to the slot as independent seams; conflating them is what made states ③/④
// unreachable.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Gate, type FirstRunLocalCardCtx } from "./Gate";
import { FIRST_RUN_COPY } from "./firstRun";
import type { ProviderKeysPort } from "../settings/data/providerKeys";

function fakePort(): ProviderKeysPort {
  return {
    list: vi.fn(() => Promise.resolve([])),
    save: vi.fn(() => Promise.reject(new Error("unused"))),
    remove: vi.fn(() => Promise.resolve()),
  };
}

function renderGate(overrides = {}) {
  return render(
    <Gate
      keyPort={fakePort()}
      onStartDownload={() => undefined}
      onKeyConnected={() => undefined}
      {...overrides}
    />,
  );
}

describe("<Gate>", () => {
  it("renders both cards with byte-verbatim SPEC copy", () => {
    renderGate();
    const local = screen.getByTestId("first-run-local-card");
    expect(local.textContent).toContain(FIRST_RUN_COPY.local.title);
    expect(local.textContent).toContain(FIRST_RUN_COPY.local.meta);
    expect(local.textContent).toContain(FIRST_RUN_COPY.local.body);
    expect(local.textContent).toContain(FIRST_RUN_COPY.local.btn);
    expect(local.textContent).toContain(FIRST_RUN_COPY.local.note);

    const key = screen.getByTestId("first-run-key-card");
    expect(key.textContent).toContain(FIRST_RUN_COPY.key.title);
    expect(key.textContent).toContain(FIRST_RUN_COPY.key.meta);
    expect(key.textContent).toContain(FIRST_RUN_COPY.key.body);
    expect(key.textContent).toContain(FIRST_RUN_COPY.key.btn);
  });

  it("has no shelved trial hatch or Haiku row", () => {
    renderGate();
    expect(screen.queryByText(/25 free runs/i)).toBeNull();
    expect(screen.queryByText(/Haiku/i)).toBeNull();
    expect(screen.queryByText(/just exploring/i)).toBeNull();
  });

  it("fires onStartDownload from the local card", () => {
    const onStartDownload = vi.fn();
    renderGate({ onStartDownload });
    fireEvent.click(screen.getByTestId("first-run-start-download"));
    expect(onStartDownload).toHaveBeenCalledTimes(1);
  });

  it("can disable the local download (until P2 preset lands)", () => {
    renderGate({ localDownloadDisabled: true });
    expect(
      (screen.getByTestId("first-run-start-download") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("reveals the inline KeyForm when Add a key is clicked", () => {
    renderGate();
    expect(screen.queryByTestId("first-run-keyform")).toBeNull();
    fireEvent.click(screen.getByTestId("first-run-add-key"));
    expect(screen.getByTestId("first-run-keyform")).not.toBeNull();
    // The button is replaced by the form.
    expect(screen.queryByTestId("first-run-add-key")).toBeNull();
  });

  it("lets a renderLocalCard slot (P2) replace the default local card", () => {
    const renderLocalCard = vi.fn((ctx) => (
      <div data-testid="p2-local-card" data-pct={String(ctx.localModelPct)}>
        custom
      </div>
    ));
    renderGate({ renderLocalCard, localModelPct: 42 });
    expect(screen.queryByTestId("first-run-local-card")).toBeNull();
    const slot = screen.getByTestId("p2-local-card");
    expect(slot.getAttribute("data-pct")).toBe("42");
    expect(renderLocalCard).toHaveBeenCalledTimes(1);
  });

  // --- PRD-P8 D4 / D4a — the two advance seams --------------------------

  /** A slot card exposing both ctx callbacks as buttons. */
  function slotCard(ctx: FirstRunLocalCardCtx) {
    return (
      <div data-testid="p8-local-card">
        <button
          type="button"
          data-testid="p8-start"
          onClick={ctx.onStartDownload}
        >
          start
        </button>
        <button
          type="button"
          data-testid="p8-continue"
          onClick={ctx.onContinue}
        >
          continue
        </button>
      </div>
    );
  }

  it("hands the slot Continue → as a seam SEPARATE from Start download (D4a)", () => {
    const onStartDownload = vi.fn();
    const onContinue = vi.fn();
    renderGate({ renderLocalCard: slotCard, onStartDownload, onContinue });

    fireEvent.click(screen.getByTestId("p8-continue"));
    expect(onContinue).toHaveBeenCalledTimes(1);
    // Continuing must NOT be mistaken for asking for a download — that is what
    // would restart a pull already in flight.
    expect(onStartDownload).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("p8-start"));
    expect(onStartDownload).toHaveBeenCalledTimes(1);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("defaults onContinue to an inert callback when the host wires none", () => {
    const onStartDownload = vi.fn();
    renderGate({ renderLocalCard: slotCard, onStartDownload });

    // A slot that renders D4a's action against a host that has not wired it
    // must be harmless — never a throw, never a phantom download.
    expect(() =>
      fireEvent.click(screen.getByTestId("p8-continue")),
    ).not.toThrow();
    expect(onStartDownload).not.toHaveBeenCalled();
  });
});
