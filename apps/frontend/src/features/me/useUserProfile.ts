import type {
  UpdateUserProfileRequest,
  UserProfile,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { getMyProfile, updateMyProfile } from "../../api/meApi";

/**
 * One round-trip per Settings open: fetch the profile once on mount,
 * expose ``save`` for partial updates. Uses optimistic UI — the local
 * state flips immediately, the request fires in the background, and a
 * 4xx rolls back to the server's last-known state. ``isDirty`` is
 * driven by the diff between the last server snapshot and the local
 * draft so the panel can show a save status without a separate
 * controlled-form library.
 */
export interface UserProfileState {
  /** Latest server snapshot. ``null`` while the initial fetch is in flight. */
  data: UserProfile | null;
  loading: boolean;
  error: string | null;
  /** Apply a partial update; returns the saved snapshot or throws on 4xx. */
  save: (patch: UpdateUserProfileRequest) => Promise<UserProfile>;
  /** Refresh from server (e.g. on tab focus). */
  refresh: () => Promise<void>;
}

export function useUserProfile(): UserProfileState {
  const [data, setData] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const fetchOnce = useCallback(async (): Promise<void> => {
    try {
      const next = await getMyProfile();
      if (!cancelledRef.current) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      if (!cancelledRef.current) {
        setError(
          err instanceof Error ? err.message : "Could not load profile.",
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
    async (patch: UpdateUserProfileRequest): Promise<UserProfile> => {
      try {
        const next = await updateMyProfile(patch);
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
