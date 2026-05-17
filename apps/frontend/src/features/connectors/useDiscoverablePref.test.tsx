// PR 4.4.7 Phase 2 (Slice A) — backend-backed override for the
// catalog's discoverable flag.
//
// Surface tests:
//  - Bootstrap: hook reads backend overrides and returns them.
//  - Catalog default fallback: missing slug returns the catalog default.
//  - setEnabled: PATCHes the backend and updates the cache.
//  - Cross-instance: a write on one slug propagates to other hook
//    instances rendering the same slug.
//  - localStorage migration: legacy entries get PATCHed and cleared.

import type {
  UpdateUserPreferencesRequest,
  UserPreferences,
} from "@enterprise-search/api-types";
import {
  KeyValueStoreProvider,
  LocalStorageKeyValueStore,
} from "@enterprise-search/chat-surface";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn<() => Promise<UserPreferences>>();
const mockPut =
  vi.fn<(patch: UpdateUserPreferencesRequest) => Promise<UserPreferences>>();

vi.mock("../../api/meApi", () => ({
  getMyPreferences: () => mockGet(),
  updateMyPreferences: (patch: UpdateUserPreferencesRequest) => mockPut(patch),
}));

import {
  _resetDiscoverablePrefForTests,
  useDiscoverablePref,
} from "./useDiscoverablePref";

const BASE: UserPreferences = {
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
  discoverable_connectors: { overrides: {} },
  updated_at: "2026-05-05T16:01:14Z",
};

function withOverrides(overrides: Record<string, boolean>): UserPreferences {
  return {
    ...BASE,
    discoverable_connectors: { overrides },
  };
}

// The CI vitest env runs with ``--localstorage-file`` set to an
// invalid path which leaves ``window.localStorage`` as a stub without
// a working ``setItem``. Substitute an in-memory implementation per
// test so the migration scenarios can exercise the legacy storage
// path deterministically.
function installInMemoryLocalStorage(): void {
  const store = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: stub,
  });
}

beforeEach(() => {
  _resetDiscoverablePrefForTests();
  mockGet.mockReset();
  mockPut.mockReset();
  installInMemoryLocalStorage();
});

// Wrapper that provides a KeyValueStore backed by the in-memory
// localStorage stub installed above. The hook now reads/writes through
// useKeyValueStore() instead of touching window.localStorage directly;
// LocalStorageKeyValueStore delegates to whatever globalThis.localStorage
// points at via its deferred-lookup pattern, so the stub still drives it.
function withKvStore({ children }: { children: ReactNode }): ReactNode {
  return (
    <KeyValueStoreProvider store={new LocalStorageKeyValueStore()}>
      {children}
    </KeyValueStoreProvider>
  );
}

afterEach(() => {
  _resetDiscoverablePrefForTests();
});

describe("useDiscoverablePref", () => {
  it("falls back to the catalog default while bootstrap is in flight", async () => {
    mockGet.mockResolvedValueOnce(BASE);
    const { result } = renderHook(() => useDiscoverablePref("linear", true), {
      wrapper: withKvStore,
    });
    expect(result.current.enabled).toBe(true);
    expect(result.current.overridden).toBe(false);
    await waitFor(() => expect(mockGet).toHaveBeenCalledOnce());
  });

  it("returns the user override after bootstrap when present", async () => {
    mockGet.mockResolvedValueOnce(withOverrides({ atlassian: false }));
    const { result } = renderHook(
      () => useDiscoverablePref("atlassian", true),
      {
        wrapper: withKvStore,
      },
    );
    await waitFor(() => {
      expect(result.current.overridden).toBe(true);
    });
    expect(result.current.enabled).toBe(false);
  });

  it("setEnabled PATCHes the backend with the per-slug override", async () => {
    mockGet.mockResolvedValueOnce(BASE);
    mockPut.mockResolvedValueOnce(withOverrides({ linear: false }));
    const { result } = renderHook(() => useDiscoverablePref("linear", true), {
      wrapper: withKvStore,
    });
    await waitFor(() => expect(mockGet).toHaveBeenCalledOnce());
    act(() => {
      result.current.setEnabled(false);
    });
    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledWith({
        discoverable_connectors: { overrides: { linear: false } },
      });
    });
    await waitFor(() => {
      expect(result.current.enabled).toBe(false);
      expect(result.current.overridden).toBe(true);
    });
  });

  it("propagates writes to other hook instances watching the same slug", async () => {
    mockGet.mockResolvedValueOnce(BASE);
    mockPut.mockResolvedValueOnce(withOverrides({ notion: false }));
    const a = renderHook(() => useDiscoverablePref("notion", true), {
      wrapper: withKvStore,
    });
    const b = renderHook(() => useDiscoverablePref("notion", true), {
      wrapper: withKvStore,
    });
    await waitFor(() => expect(mockGet).toHaveBeenCalledOnce());
    act(() => {
      a.result.current.setEnabled(false);
    });
    await waitFor(() => {
      expect(b.result.current.enabled).toBe(false);
    });
  });

  // Regression: when two toggles flip in quick succession, each
  // PATCH's response only echoes its own slug (the first PATCH
  // resolves before the server has seen the second). The hook must
  // NOT trust the response's full overrides map — that would clobber
  // the second slug's optimistic state. Instead each PATCH is
  // authoritative only for the slug it sent.
  it("two consecutive toggles both stay off (no clobber from response)", async () => {
    mockGet.mockResolvedValueOnce(BASE);
    // First PATCH (asana) returns ONLY {asana: false} — the realistic
    // shape when the second PATCH hasn't been processed yet.
    mockPut.mockResolvedValueOnce(withOverrides({ asana: false }));
    // Second PATCH (linear) returns the full merged shape.
    mockPut.mockResolvedValueOnce(
      withOverrides({ asana: false, linear: false }),
    );

    const asana = renderHook(() => useDiscoverablePref("asana", true), {
      wrapper: withKvStore,
    });
    const linear = renderHook(() => useDiscoverablePref("linear", true), {
      wrapper: withKvStore,
    });
    await waitFor(() => expect(mockGet).toHaveBeenCalledOnce());

    act(() => {
      asana.result.current.setEnabled(false);
    });
    act(() => {
      linear.result.current.setEnabled(false);
    });

    // Both PATCHes flush.
    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledTimes(2);
    });

    // Both toggles remained off — the bug would have flipped Linear
    // back to ``enabled`` while the asana PATCH's response was being
    // applied.
    await waitFor(() => {
      expect(asana.result.current.enabled).toBe(false);
      expect(linear.result.current.enabled).toBe(false);
    });
  });

  it("migrates legacy localStorage overrides to the backend, then clears them", async () => {
    window.localStorage.setItem("enterprise.discoverable.linear", "off");
    window.localStorage.setItem("enterprise.discoverable.notion", "on");
    mockGet.mockResolvedValueOnce(BASE);
    mockPut.mockResolvedValueOnce(
      withOverrides({ linear: false, notion: true }),
    );

    const { result } = renderHook(() => useDiscoverablePref("linear", true), {
      wrapper: withKvStore,
    });
    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledOnce();
    });
    expect(mockPut).toHaveBeenCalledWith({
      discoverable_connectors: {
        overrides: { linear: false, notion: true },
      },
    });
    await waitFor(() => {
      expect(result.current.enabled).toBe(false);
      expect(result.current.overridden).toBe(true);
    });
    expect(
      window.localStorage.getItem("enterprise.discoverable.linear"),
    ).toBeNull();
    expect(
      window.localStorage.getItem("enterprise.discoverable.notion"),
    ).toBeNull();
  });

  it("does not overwrite a backend override with the legacy localStorage value", async () => {
    // Backend already says linear=true. Legacy storage says off. The
    // newer backend state wins; PATCH is not called for that slug.
    window.localStorage.setItem("enterprise.discoverable.linear", "off");
    mockGet.mockResolvedValueOnce(withOverrides({ linear: true }));

    const { result } = renderHook(() => useDiscoverablePref("linear", true), {
      wrapper: withKvStore,
    });
    await waitFor(() => expect(mockGet).toHaveBeenCalledOnce());
    expect(mockPut).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(result.current.enabled).toBe(true);
    });
  });
});
