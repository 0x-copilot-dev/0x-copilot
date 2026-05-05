// PR 3.3 — `useWorkspaceMember` hook tests.
//
// The hook is the single seam ``MentionLabel`` consumes for "@marcus"
// resolution. These tests pin:
//
//  - cache hit: a previously primed member resolves synchronously
//  - 404 fallback: stays unresolved, doesn't keep hammering
//  - happy-path fetch resolves and caches
//  - module-level cache deduplicates across mounts

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as workspaceApi from "../../api/workspaceApi";
import {
  _resetWorkspaceMemberCache,
  peekWorkspaceMember,
  primeWorkspaceMember,
  useWorkspaceMember,
} from "./useWorkspaceMember";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

describe("useWorkspaceMember", () => {
  beforeEach(() => {
    _resetWorkspaceMemberCache();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    _resetWorkspaceMemberCache();
  });

  it("returns null while loading and the resolved member after fetch", async () => {
    const spy = vi.spyOn(workspaceApi, "getWorkspaceMember").mockResolvedValue({
      user_id: "marcus",
      display_name: "Marcus Tate",
      email: "marcus@acme.com",
      handle: "marcus",
    });
    const { result } = renderHook(() => useWorkspaceMember("marcus", IDENTITY));
    expect(result.current).toBeNull();
    await waitFor(() => {
      expect(result.current?.display_name).toBe("Marcus Tate");
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("caches across mounts so two consumers share one fetch", async () => {
    const spy = vi.spyOn(workspaceApi, "getWorkspaceMember").mockResolvedValue({
      user_id: "marcus",
      display_name: "Marcus Tate",
    });
    const first = renderHook(() => useWorkspaceMember("marcus", IDENTITY));
    const second = renderHook(() => useWorkspaceMember("marcus", IDENTITY));
    await waitFor(() => {
      expect(first.result.current?.display_name).toBe("Marcus Tate");
    });
    await waitFor(() => {
      expect(second.result.current?.display_name).toBe("Marcus Tate");
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("falls back to null on 404 and caches the unresolved marker", async () => {
    const spy = vi
      .spyOn(workspaceApi, "getWorkspaceMember")
      .mockRejectedValue(new Error("Request failed with 404"));
    const { result, rerender } = renderHook(() =>
      useWorkspaceMember("ghost", IDENTITY),
    );
    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });
    expect(result.current).toBeNull();
    // Second mount must not refetch — the unresolved marker is sticky.
    rerender();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("primeWorkspaceMember populates the cache without a fetch", () => {
    const member = {
      user_id: "marcus",
      display_name: "Marcus Tate",
      handle: "marcus",
    };
    primeWorkspaceMember(member);
    expect(peekWorkspaceMember("marcus")).toEqual({
      ...member,
    });
  });

  it("returns null when identity is null (anonymous / shared view)", () => {
    const { result } = renderHook(() => useWorkspaceMember("marcus", null));
    expect(result.current).toBeNull();
  });
});
