// PR 4.1 — `useUserProfile` data hook contract.
//
// Three behaviours under test:
//   1. Initial GET hydrates `data` and clears `loading`.
//   2. `save()` reflects the server response.
//   3. `save()` re-throws on 4xx so the caller can react in its try/catch.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import type {
  UpdateUserProfileRequest,
  UserProfile,
} from "@enterprise-search/api-types";

const mockGet = vi.fn<() => Promise<UserProfile>>();
const mockPut =
  vi.fn<(patch: UpdateUserProfileRequest) => Promise<UserProfile>>();

vi.mock("../../api/meApi", () => ({
  getMyProfile: () => mockGet(),
  updateMyProfile: (patch: UpdateUserProfileRequest) => mockPut(patch),
}));

import { useUserProfile } from "./useUserProfile";

const SEED: UserProfile = {
  user_id: "usr_sarah",
  email: "sarah@acme.com",
  email_verified_at: "2026-01-12T09:01:24Z",
  display_name: "Sarah Chen",
  title: "Marketing Ops",
  timezone: "America/Los_Angeles",
  locale: "en-US",
  working_hours: null,
  avatar_url: null,
  updated_at: "2026-05-05T16:01:14Z",
};

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
});

describe("useUserProfile", () => {
  it("hydrates from GET", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const { result } = renderHook(() => useUserProfile());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(SEED);
    expect(result.current.error).toBeNull();
  });

  it("save reflects the server response", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    const next: UserProfile = { ...SEED, title: "Director, Marketing Ops" };
    mockPut.mockResolvedValueOnce(next);

    const { result } = renderHook(() => useUserProfile());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.save({ title: "Director, Marketing Ops" });
    });
    expect(result.current.data).toEqual(next);
  });

  it("save re-throws on 4xx and surfaces the error", async () => {
    mockGet.mockResolvedValueOnce(SEED);
    mockPut.mockRejectedValueOnce(new Error("invalid_timezone"));

    const { result } = renderHook(() => useUserProfile());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.save({ timezone: "Mars/Olympus" });
      } catch (err) {
        caught = err;
      }
    });
    expect((caught as Error).message).toBe("invalid_timezone");
    // The hook surfaces the error; data remains the last server snapshot.
    expect(result.current.error).toBe("invalid_timezone");
    expect(result.current.data).toEqual(SEED);
  });
});
