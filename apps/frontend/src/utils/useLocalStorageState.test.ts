import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useLocalStorageState } from "./useLocalStorageState";

const KEY = "atlas:test-key";
const isString = (value: unknown): value is string => typeof value === "string";

/**
 * JSDOM in this project ships a placeholder `window.localStorage` without
 * the Storage methods. Install a minimal in-memory Storage for these tests.
 */
function installStorageShim(): Storage {
  const store = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    key(index) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key) {
      store.delete(key);
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: shim,
  });
  return shim;
}

describe("useLocalStorageState", () => {
  beforeEach(() => {
    installStorageShim();
  });
  afterEach(() => {
    window.localStorage.clear();
  });

  it("seeds from default when nothing is persisted", () => {
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "default", isString),
    );
    expect(result.current[0]).toBe("default");
  });

  it("seeds from localStorage when a valid value is present", () => {
    window.localStorage.setItem(KEY, JSON.stringify("persisted"));
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "default", isString),
    );
    expect(result.current[0]).toBe("persisted");
  });

  it("falls back to default when persisted value fails validation", () => {
    window.localStorage.setItem(KEY, JSON.stringify(42));
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "default", isString),
    );
    expect(result.current[0]).toBe("default");
  });

  it("falls back to default when persisted value is corrupt JSON", () => {
    window.localStorage.setItem(KEY, "{not-json");
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "default", isString),
    );
    expect(result.current[0]).toBe("default");
  });

  it("writes to localStorage on update", () => {
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "default", isString),
    );
    act(() => result.current[1]("next"));
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toBe("next");
  });

  it("supports updater function", () => {
    const { result } = renderHook(() =>
      useLocalStorageState(KEY, "a", isString),
    );
    act(() => result.current[1]((current) => current + "b"));
    expect(result.current[0]).toBe("ab");
  });
});
