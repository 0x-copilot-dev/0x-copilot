// PR 4.1 — `useUserPreferences` data hook contract.
//
// Same shape as `useUserProfile`: hydrate on mount, save reflects server,
// errors re-throw. The deep-merge is enforced server-side; the FE just
// passes the partial through.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import type {
  UpdateUserPreferencesRequest,
  UserPreferences,
} from "@0x-copilot/api-types";

const mockGet = vi.fn<() => Promise<UserPreferences>>();
const mockPut =
  vi.fn<(patch: UpdateUserPreferencesRequest) => Promise<UserPreferences>>();

vi.mock("../../api/meApi", () => ({
  getMyPreferences: () => mockGet(),
  updateMyPreferences: (patch: UpdateUserPreferencesRequest) => mockPut(patch),
}));

import { useUserPreferences } from "./useUserPreferences";
import { UserPreferencesProvider } from "./UserPreferencesContext";

// PRD 04 collapsed the hook into a shared provider — every caller now
// renders inside <UserPreferencesProvider>. Wrap renderHook so the
// hook resolves the context instead of throwing.
function withProvider({ children }: { children: ReactNode }) {
  return <UserPreferencesProvider>{children}</UserPreferencesProvider>;
}

const SEED: UserPreferences = {
  appearance: {
    theme: "dark",
    accent: "atlas-orange",
    density: "comfortable",
    reduce_motion: "auto",
  },
  shortcuts: { overrides: {} },
  notifications: {
    matrix: {
      mention: { email: true, slack: false, desktop: true },
      approval_needed: { email: true, slack: false, desktop: true },
      run_finished: { email: false, slack: false, desktop: true },
      weekly_digest: { email: true, slack: false, desktop: false },
    },
  },
  // PR 4.4.7 Phase 2 (Slice A) — empty by default; absent slugs
  // inherit the catalog entry's ``discoverable`` flag.
  discoverable_connectors: { overrides: {} },
  updated_at: "2026-05-05T16:01:14Z",
};

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

describe("useUserPreferences", () => {
  it("hydrates from GET", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const { result } = renderHook(() => useUserPreferences(), {
      wrapper: withProvider,
    });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(SEED);
  });

  it("save reflects server response (e.g. accent flip)", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const next: UserPreferences = {
      ...SEED,
      appearance: { ...SEED.appearance, accent: "violet" },
    };
    mockPut.mockResolvedValueOnce(next);

    const { result } = renderHook(() => useUserPreferences(), {
      wrapper: withProvider,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.save({ appearance: { accent: "violet" } });
    });
    expect(result.current.data?.appearance.accent).toBe("violet");
  });

  it("save re-throws and stores error", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    mockPut.mockRejectedValueOnce(new Error("invalid_request"));

    const { result } = renderHook(() => useUserPreferences(), {
      wrapper: withProvider,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    let caught: unknown = null;
    await act(async () => {
      try {
        // @ts-expect-error - exercising server-side rejection
        await result.current.save({ appearance: { accent: "neon-pink" } });
      } catch (err) {
        caught = err;
      }
    });
    expect((caught as Error).message).toBe("invalid_request");
    expect(result.current.error).toBe("invalid_request");
  });
});
