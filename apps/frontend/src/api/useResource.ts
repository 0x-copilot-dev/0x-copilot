import { useCallback, useEffect, useState } from "react";
import type { RequestIdentity } from "./config";

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
      setError(err instanceof Error ? err.message : errorFallback);
    } finally {
      setLoading(false);
    }
  }, [identity, fetcher, errorFallback]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}

export function requireIdentity(
  identity: RequestIdentity | null,
): RequestIdentity {
  if (identity === null) {
    throw new Error("Session identity is not loaded.");
  }
  return identity;
}
