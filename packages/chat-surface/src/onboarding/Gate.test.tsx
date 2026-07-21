// Gate — State A (PRD-P1 §6.1). Both cards render with verbatim copy;
// "Start download" fires onStartDownload; "Add a key" reveals the inline
// KeyForm; a renderLocalCard slot replaces the default local card.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Gate } from "./Gate";
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
});
