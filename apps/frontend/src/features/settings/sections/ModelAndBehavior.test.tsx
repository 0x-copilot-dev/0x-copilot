// PR 4.3 — Model & behavior panel.
//
// Three behaviours under test:
//   1. Loading state renders before the workspace defaults hydrate.
//   2. Editing a pill triggers a debounced save with the right shape.
//   3. The system-prompt textarea respects the 8 KB cap.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import type {
  UpdateWorkspaceDefaultsRequest,
  WorkspaceDefaultsResponse,
} from "@enterprise-search/api-types";
import type { UseWorkspaceDefaultsResult } from "../useWorkspaceDefaults";

import { ModelAndBehavior } from "./ModelAndBehavior";

const SEED: WorkspaceDefaultsResponse = {
  default_model: {
    provider: "openai",
    model_name: "gpt-5.4-mini",
    reasoning: null,
  },
  default_connectors: { notion: ["read"] },
  retention_days: 90,
  behavior_overrides: {
    training_data_opt_out: false,
  },
  updated_at: "2026-05-05T16:00:00Z",
  updated_by_user_id: "marcus@acme.com",
};

function makeHook(
  override: Partial<UseWorkspaceDefaultsResult> = {},
): UseWorkspaceDefaultsResult {
  return {
    defaults: SEED,
    loading: false,
    error: null,
    save: vi
      .fn<UseWorkspaceDefaultsResult["save"]>()
      .mockResolvedValue(undefined),
    ...override,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

describe("ModelAndBehavior", () => {
  it("renders the loading state before defaults hydrate", () => {
    render(
      <ModelAndBehavior
        workspaceDefaults={makeHook({ defaults: null, loading: true })}
      />,
    );
    expect(screen.getByText(/loading workspace defaults/i)).toBeTruthy();
  });

  it("debounced-saves a citation density change", async () => {
    const save = vi
      .fn<(request: UpdateWorkspaceDefaultsRequest) => Promise<void>>()
      .mockResolvedValue(undefined);
    render(<ModelAndBehavior workspaceDefaults={makeHook({ save })} />);
    // Click the "Thorough" pill.
    fireEvent.click(screen.getByRole("radio", { name: "Thorough" }));
    // Save is debounced ~300 ms — advance timers and flush microtasks.
    await act(async () => {
      vi.advanceTimersByTime(310);
    });
    expect(save).toHaveBeenCalledTimes(1);
    const request = save.mock.calls[0][0];
    expect(request.behavior_overrides?.citation_density).toBe("thorough");
    // Other defaults pass through unchanged.
    expect(request.default_model).toEqual(SEED.default_model);
    expect(request.retention_days).toBe(SEED.retention_days);
  });

  it("caps the system-prompt textarea at the documented length", () => {
    render(<ModelAndBehavior workspaceDefaults={makeHook()} />);
    const textarea = screen.getByPlaceholderText(
      /always sign off as the gtm team/i,
    ) as HTMLTextAreaElement;
    expect(textarea.maxLength).toBe(8 * 1024);
  });
});
