import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Mirror a piece of state into localStorage so it survives a reload.
 *
 * Reads the persisted value once on first render through a `validate`
 * predicate (so a corrupt or stale value never leaks into typed state).
 * Writes asynchronously after every set, but never blocks render.
 *
 * SSR-safe: when `window` is undefined the hook behaves like a regular
 * `useState` against `defaultValue`.
 */
export function useLocalStorageState<T>(
  key: string,
  defaultValue: T,
  validate: (value: unknown) => value is T,
): [T, (next: T | ((current: T) => T)) => void] {
  const [value, setValue] = useState<T>(() =>
    readPersisted(key, defaultValue, validate),
  );
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      window.localStorage.setItem(keyRef.current, JSON.stringify(value));
    } catch {
      // Quota / private mode — silently skip; in-memory state still works.
    }
  }, [value]);

  const set = useCallback((next: T | ((current: T) => T)) => {
    setValue((current) =>
      typeof next === "function" ? (next as (c: T) => T)(current) : next,
    );
  }, []);

  return [value, set];
}

function readPersisted<T>(
  key: string,
  defaultValue: T,
  validate: (value: unknown) => value is T,
): T {
  if (typeof window === "undefined") {
    return defaultValue;
  }
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) {
      return defaultValue;
    }
    const parsed = JSON.parse(raw);
    return validate(parsed) ? parsed : defaultValue;
  } catch {
    return defaultValue;
  }
}
