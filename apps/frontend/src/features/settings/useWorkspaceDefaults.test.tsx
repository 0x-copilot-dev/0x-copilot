// PR 3.5 / G1 — `useWorkspaceDefaults` data hook contract.
//
// Three behaviours under test:
//   1. Initial GET hydrates `defaults` and clears `loading`.
//   2. `save()` does an optimistic update, persists, and reflects the
//      server response (which may carry server-side fields like
//      `updated_at`).
//   3. `save()` rolls back on 4xx/5xx, surfaces the error, AND re-throws
//      so callers can chain (matches the documented contract in
//      `useWorkspaceDefaults.ts`).

import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import type {
  UpdateWorkspaceDefaultsRequest,
  WorkspaceDefaultsResponse,
} from "@enterprise-search/api-types";

const mockGet = vi.fn<() => Promise<WorkspaceDefaultsResponse>>();
const mockPut =
  vi.fn<
    (
      request: UpdateWorkspaceDefaultsRequest,
    ) => Promise<WorkspaceDefaultsResponse>
  >();

vi.mock("../../api/agentApi", () => ({
  getWorkspaceDefaults: () => mockGet(),
  putWorkspaceDefaults: (request: UpdateWorkspaceDefaultsRequest) =>
    mockPut(request),
}));

import { useWorkspaceDefaults } from "./useWorkspaceDefaults";

const IDENTITY = {
  orgId: "org_acme",
  userId: "sarah@acme.com",
  bearer: null,
} as const;

const SEED: WorkspaceDefaultsResponse = {
  default_model: {
    provider: "openai",
    model_name: "gpt-5.4-mini",
    reasoning: { enabled: true, effort: "medium" },
  },
  default_connectors: { notion: ["read"], drive: ["read"] },
  retention_days: 365,
  updated_at: "2026-04-29T10:00:00Z",
  updated_by_user_id: "marcus@acme.com",
};

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

describe("useWorkspaceDefaults", () => {
  it("hydrates from GET and clears loading", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const { result } = renderHook(() => useWorkspaceDefaults(IDENTITY));
    expect(result.current.loading).toBe(true);
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.defaults).toEqual(SEED);
    expect(result.current.error).toBeNull();
  });

  it("save() optimistically updates and reflects the server response", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const next: UpdateWorkspaceDefaultsRequest = {
      default_model: SEED.default_model,
      default_connectors: { notion: ["read"], drive: null },
      retention_days: 90,
    };
    const persisted: WorkspaceDefaultsResponse = {
      ...next,
      updated_at: "2026-05-05T13:00:00Z",
      updated_by_user_id: "sarah@acme.com",
    };
    mockPut.mockResolvedValueOnce(persisted);

    const { result } = renderHook(() => useWorkspaceDefaults(IDENTITY));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.save(next);
    });
    expect(result.current.defaults).toEqual(persisted);
    expect(result.current.error).toBeNull();
  });

  it("save() rolls back on 4xx and re-throws", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    mockPut.mockRejectedValueOnce(new Error("Forbidden"));

    const { result } = renderHook(() => useWorkspaceDefaults(IDENTITY));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const next: UpdateWorkspaceDefaultsRequest = {
      default_model: SEED.default_model,
      default_connectors: SEED.default_connectors,
      retention_days: 90,
    };

    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.save(next);
      } catch (err) {
        caught = err;
      }
    });

    expect((caught as Error).message).toBe("Forbidden");
    // Rolled back to the original SEED.
    expect(result.current.defaults).toEqual(SEED);
    expect(result.current.error).toBe("Forbidden");
  });

  it("surfaces a fetch error in `error` and leaves defaults null", async () => {
    mockGet.mockRejectedValueOnce(new Error("Network unreachable"));
    const { result } = renderHook(() => useWorkspaceDefaults(IDENTITY));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.defaults).toBeNull();
    expect(result.current.error).toBe("Network unreachable");
  });
});
