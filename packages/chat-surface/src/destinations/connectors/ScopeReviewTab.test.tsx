// ScopeReviewTab — dirty-state + save callback.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorScopeEntry } from "@enterprise-search/api-types";

import { ScopeReviewTab } from "./ScopeReviewTab";

function makeScopes(): ReadonlyArray<ConnectorScopeEntry> {
  return [
    { scope: "gmail.readonly", granted: true, description: "Read mail" },
    { scope: "gmail.modify", granted: false, description: "Modify mail" },
  ];
}

describe("ScopeReviewTab", () => {
  it("renders one row per scope", () => {
    render(<ScopeReviewTab scopes={makeScopes()} />);
    expect(screen.getAllByTestId("connector-scope-row").length).toBe(2);
  });

  it("Save is disabled until the user toggles a row", () => {
    render(<ScopeReviewTab scopes={makeScopes()} onSave={() => {}} />);
    const save = screen.getByTestId("connector-scope-save");
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("data-dirty", "false");
  });

  it("toggling a row enables Save and surfaces the dirty flag", () => {
    render(<ScopeReviewTab scopes={makeScopes()} onSave={() => {}} />);
    fireEvent.click(
      screen.getByTestId("connector-scope-checkbox-gmail.modify"),
    );
    const save = screen.getByTestId("connector-scope-save");
    expect(save).not.toBeDisabled();
    expect(save).toHaveAttribute("data-dirty", "true");
  });

  it("Save callback receives the desired set with updated granted flags", () => {
    const onSave = vi.fn();
    render(<ScopeReviewTab scopes={makeScopes()} onSave={onSave} />);
    fireEvent.click(
      screen.getByTestId("connector-scope-checkbox-gmail.modify"),
    );
    fireEvent.click(screen.getByTestId("connector-scope-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock
      .calls[0]?.[0] as ReadonlyArray<ConnectorScopeEntry>;
    expect(payload).toEqual([
      { scope: "gmail.readonly", granted: true, description: "Read mail" },
      { scope: "gmail.modify", granted: true, description: "Modify mail" },
    ]);
  });

  it("Reset returns the form to the initial granted set", () => {
    render(<ScopeReviewTab scopes={makeScopes()} onSave={() => {}} />);
    fireEvent.click(
      screen.getByTestId("connector-scope-checkbox-gmail.modify"),
    );
    fireEvent.click(screen.getByTestId("connector-scope-reset"));
    expect(screen.getByTestId("connector-scope-save")).toBeDisabled();
  });

  it("renders the empty state when no scopes are provided", () => {
    render(<ScopeReviewTab scopes={[]} />);
    expect(screen.getByTestId("connector-scope-empty")).toBeInTheDocument();
  });
});
