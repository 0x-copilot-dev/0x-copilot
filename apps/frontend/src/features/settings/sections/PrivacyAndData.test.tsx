// PR 4.3 — Privacy & data panel.
//
// Behaviours under test:
//   1. Toggling training opt-out fires a save with the right shape.
//   2. Retention summary renders the deployment-default badge when no
//      org-scope policy exists.
//   3. Export click queues + surfaces export_id.
//   4. Delete-all renders the 501 server message verbatim.

import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import type {
  RetentionEffectiveResponse,
  UpdateWorkspaceDefaultsRequest,
  WorkspaceDefaultsResponse,
  WorkspaceExportResponse,
} from "@0x-copilot/api-types";
import type { UseWorkspaceDefaultsResult } from "../useWorkspaceDefaults";

const mockGetRetention = vi.fn<() => Promise<RetentionEffectiveResponse>>();
const mockExport = vi.fn<() => Promise<WorkspaceExportResponse>>();
const mockDelete = vi.fn<(slug: string) => Promise<void>>();

vi.mock("../../../api/agentApi", () => ({
  getRetentionEffective: () => mockGetRetention(),
  requestWorkspaceExport: () => mockExport(),
  deleteWorkspaceData: (slug: string) => mockDelete(slug),
}));

import { PrivacyAndData } from "./PrivacyAndData";

const IDENTITY = { orgId: "org_acme", userId: "marcus@acme.com" } as const;

const SEED: WorkspaceDefaultsResponse = {
  default_model: {
    provider: "openai",
    model_name: "gpt-5.4-mini",
    reasoning: null,
  },
  default_connectors: {},
  retention_days: 90,
  behavior_overrides: { training_data_opt_out: false },
  updated_at: "2026-05-05T16:00:00Z",
  updated_by_user_id: "marcus@acme.com",
};

const RETENTION_EMPTY: RetentionEffectiveResponse = {
  effective: {
    messages: {
      kind: "messages",
      ttl_seconds: 365 * 24 * 60 * 60,
      source_scope: null,
      source_policy_id: null,
    },
    events: {
      kind: "events",
      ttl_seconds: 30 * 24 * 60 * 60,
      source_scope: null,
      source_policy_id: null,
    },
    context_payloads: {
      kind: "context_payloads",
      ttl_seconds: null,
      source_scope: null,
      source_policy_id: null,
    },
    checkpoints: {
      kind: "checkpoints",
      ttl_seconds: null,
      source_scope: null,
      source_policy_id: null,
    },
    memory_items: {
      kind: "memory_items",
      ttl_seconds: null,
      source_scope: null,
      source_policy_id: null,
    },
  },
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
  mockGetRetention.mockReset();
  mockExport.mockReset();
  mockDelete.mockReset();
  mockGetRetention.mockResolvedValue(RETENTION_EMPTY);
});

describe("PrivacyAndData", () => {
  it("toggles training opt-out via the workspace defaults save", async () => {
    const save = vi
      .fn<(request: UpdateWorkspaceDefaultsRequest) => Promise<void>>()
      .mockResolvedValue(undefined);
    render(
      <PrivacyAndData
        identity={IDENTITY}
        workspaceDefaults={makeHook({ save })}
      />,
    );
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(save).toHaveBeenCalledTimes(1);
    });
    const request = save.mock.calls[0][0];
    expect(request.behavior_overrides?.training_data_opt_out).toBe(true);
  });

  it("renders the deployment-default badge when no org policy exists", async () => {
    render(
      <PrivacyAndData identity={IDENTITY} workspaceDefaults={makeHook()} />,
    );
    await waitFor(() => {
      expect(screen.getAllByText(/deployment default/i).length).toBeGreaterThan(
        0,
      );
    });
  });

  it("export click renders the queued badge", async () => {
    mockExport.mockResolvedValue({ export_id: "exp_abc", status: "queued" });
    render(
      <PrivacyAndData identity={IDENTITY} workspaceDefaults={makeHook()} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /export workspace data/i }),
    );
    await waitFor(() => {
      expect(screen.getByText(/queued · exp_abc/i)).toBeTruthy();
    });
  });

  it("delete-all surfaces the 501 message verbatim", async () => {
    mockDelete.mockRejectedValue(
      new Error("Workspace deletion is gated. Contact support."),
    );
    render(
      <PrivacyAndData identity={IDENTITY} workspaceDefaults={makeHook()} />,
    );
    const slugInput = screen.getByPlaceholderText(/type the workspace id/i);
    fireEvent.change(slugInput, { target: { value: IDENTITY.orgId } });
    fireEvent.click(
      screen.getByRole("button", { name: /delete workspace data/i }),
    );
    await waitFor(() => {
      expect(screen.getByText(/workspace deletion is gated/i)).toBeTruthy();
    });
  });

  it("delete button is disabled until the slug matches", () => {
    render(
      <PrivacyAndData identity={IDENTITY} workspaceDefaults={makeHook()} />,
    );
    const button = screen.getByRole("button", {
      name: /delete workspace data/i,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/type the workspace id/i), {
      target: { value: IDENTITY.orgId },
    });
    expect(button.disabled).toBe(false);
  });
});
