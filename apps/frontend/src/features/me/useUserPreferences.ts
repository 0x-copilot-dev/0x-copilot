import type {
  UpdateUserPreferencesRequest,
  UserPreferences,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { getMyPreferences, updateMyPreferences } from "../../api/meApi";

/**
 * Hydrate the per-user preferences blob once on mount, expose ``save``
 * for partial updates. Same shape as ``useUserProfile``.
 *
 * The Appearance panel calls ``save`` debounced (300ms) so accent /
 * density / reduce-motion clicks coalesce — see ``Appearance.tsx``.
 * Theme + accent re-render live via ``useThemeSync``; a save round-trip
 * just persists what the FE already applied.
 */
export interface UserPreferencesState {
  data: UserPreferences | null;
  loading: boolean;
  error: string | null;
  save: (patch: UpdateUserPreferencesRequest) => Promise<UserPreferences>;
  refresh: () => Promise<void>;
}

export function useUserPreferences(): UserPreferencesState {
  const [data, setData] = useState<UserPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const fetchOnce = useCallback(async (): Promise<void> => {
    try {
      const next = await getMyPreferences();
      if (!cancelledRef.current) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setError(
          err instanceof Error ? err.message : "Could not load preferences.",
        );
      }
    } finally {
      if (!cancelledRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void fetchOnce();
    return () => {
      cancelledRef.current = true;
    };
  }, [fetchOnce]);

  const save = useCallback(
    async (patch: UpdateUserPreferencesRequest): Promise<UserPreferences> => {
      try {
        const next = await updateMyPreferences(patch);
        setData(next);
        setError(null);
        return next;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not save.");
        throw err;
      }
    },
    [],
  );

  return { data, loading, error, save, refresh: fetchOnce };
}
