// ViewTierToggle (PRD-B3) — the persistent tier toggle + Regenerate cluster.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LedgerSurfaceViewState } from "./ledgerProjection";
import { ViewTierToggle } from "./ViewTierToggle";

function viewState(
  over: Partial<LedgerSurfaceViewState> = {},
): LedgerSurfaceViewState {
  return {
    tier: "shaped",
    basis: "generated",
    specRef: null,
    keep: null,
    shapedAvailable: true,
    regenCount: 0,
    effectiveTier: "shaped",
    ...over,
  };
}

describe("ViewTierToggle", () => {
  it("fires onSetViewPreference('generic') on Generic click", () => {
    const onSetViewPreference = vi.fn();
    render(
      <ViewTierToggle
        surfaceId="s1"
        viewState={viewState()}
        onSetViewPreference={onSetViewPreference}
        onRegenerateView={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-view-tier-generic"));
    expect(onSetViewPreference).toHaveBeenCalledTimes(1);
    expect(onSetViewPreference).toHaveBeenCalledWith("s1", "generic");
  });

  it("fires onRegenerateView exactly once on Regenerate click", () => {
    const onRegenerateView = vi.fn();
    render(
      <ViewTierToggle
        surfaceId="s1"
        viewState={viewState()}
        onSetViewPreference={vi.fn()}
        onRegenerateView={onRegenerateView}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-view-regenerate"));
    expect(onRegenerateView).toHaveBeenCalledTimes(1);
    expect(onRegenerateView).toHaveBeenCalledWith("s1");
  });

  it("disables the Shaped side until a shaped derivation exists", () => {
    render(
      <ViewTierToggle
        surfaceId="s1"
        viewState={viewState({
          tier: "generic",
          shapedAvailable: false,
          effectiveTier: "generic",
        })}
        onSetViewPreference={vi.fn()}
        onRegenerateView={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-view-tier-shaped")).toBeDisabled();
  });

  it("disables Regenerate at the client cap", () => {
    render(
      <ViewTierToggle
        surfaceId="s1"
        viewState={viewState({ regenCount: 3 })}
        onSetViewPreference={vi.fn()}
        onRegenerateView={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-view-regenerate")).toBeDisabled();
  });
});
