import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import type { RequestIdentity } from "./config";
import { errorMessage } from "../utils/errors";

// ---------------------------------------------------------------------------
// useResource — collection loader (identity-gated)
// ---------------------------------------------------------------------------

export interface ResourceState<T> {
  data: T[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useResource<T>(
  identity: RequestIdentity | null,
  fetcher: (identity: RequestIdentity) => Promise<T[]>,
  errorFallback: string,
): ResourceState<T> {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (identity === null) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setData(await fetcher(identity));
      setError(null);
    } catch (err) {
      setError(errorMessage(err, errorFallback));
    } finally {
      setLoading(false);
    }
  }, [identity, fetcher, errorFallback]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}

// ---------------------------------------------------------------------------
// useRecord / useMutableRecord — single-record loaders
// ---------------------------------------------------------------------------
//
// PR — see docs/architecture/prds/02-use-resource-with-mutation.md.
// Before these existed, every "fetch one record, expose loading/error/
// refresh, optionally a save()" hook hand-rolled the same StrictMode-safe
// useEffect + cancelledRef + try/catch dance (9 copies).

export interface RecordState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /**
   * Imperative setter exposed so feature hooks can layer their own
   * mutations (e.g. `useWorkspaceMembers` exposes `invite`/`revoke`
   * that update the local cache after a successful API call). Most
   * callers want `useMutableRecord` instead.
   */
  setData: Dispatch<SetStateAction<T | null>>;
}

export interface MutableRecordState<T, P> extends RecordState<T> {
  /** Apply a partial update; resolves with the server's snapshot or throws. */
  save: (patch: P) => Promise<T>;
}

/**
 * Load a single record once on mount; expose `refresh()` for explicit
 * reload and `setData` for feature-specific mutations.
 *
 * StrictMode-safe: the second mount of an effect is intentionally not
 * able to mutate state from the first mount's in-flight fetcher.
 *
 * The fetcher is closed over by the caller; deps (e.g. identity)
 * should be encoded by the caller into a memoised fetcher and passed
 * in. The hook re-fetches whenever the `fetcher` reference changes.
 */
export function useRecord<T>(
  fetcher: () => Promise<T>,
  errorFallback: string,
): RecordState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const next = await fetcher();
      if (!cancelledRef.current) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setError(errorMessage(err, errorFallback));
      }
    } finally {
      if (!cancelledRef.current) {
        setLoading(false);
      }
    }
  }, [fetcher, errorFallback]);

  useEffect(() => {
    cancelledRef.current = false;
    void refresh();
    return () => {
      cancelledRef.current = true;
    };
  }, [refresh]);

  return { data, loading, error, refresh, setData };
}

/**
 * `useRecord` + a `save(patch)` that updates the local snapshot from
 * the server's response. Fallback strings are separate so the load and
 * save error banners can stay specific.
 */
export function useMutableRecord<T, P>(
  fetcher: () => Promise<T>,
  saver: (patch: P) => Promise<T>,
  fallbacks: { load: string; save: string },
): MutableRecordState<T, P> {
  const record = useRecord(fetcher, fallbacks.load);
  const { setData } = record;
  const [saveError, setSaveError] = useState<string | null>(null);

  const save = useCallback(
    async (patch: P): Promise<T> => {
      try {
        const next = await saver(patch);
        setData(next);
        setSaveError(null);
        return next;
      } catch (err) {
        setSaveError(errorMessage(err, fallbacks.save));
        throw err;
      }
    },
    [saver, fallbacks.save, setData],
  );

  return {
    ...record,
    error: saveError ?? record.error,
    save,
  };
}

export function requireIdentity(
  identity: RequestIdentity | null,
): RequestIdentity {
  if (identity === null) {
    throw new Error("Session identity is not loaded.");
  }
  return identity;
}
