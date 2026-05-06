// PR 8.0.2 — shared lazy `/v1/me/profile` hook.
//
// Fetches the caller's profile once per mount keyed on the bearer's
// `user_id`. Returns null until the response lands; failures swallow
// silently — every consumer must have a safe fallback. The bearer
// already gives us `user_id` + `org_id`; the profile endpoint is what
// supplies `display_name`, `email`, `title`, `timezone`, etc.
//
// Two consumers today:
//   - sidebar `UserCard` (avatar + name + workspace · role)
//   - chat `ThreadWelcome` (`Good afternoon, Sarah.` greeting)
//
// We deliberately don't pull a global cache layer — both call sites
// mount once at the top of their respective trees and keep the
// snapshot for the lifetime of the page. The browser's HTTP cache
// dedupes the identical concurrent requests.

import { useEffect, useState } from "react";
import type { UserProfile } from "@enterprise-search/api-types";
import { getMyProfile } from "../../api/meApi";
import { useAuth } from "./AuthContext";

export interface ProfileSnapshot {
  display_name: string | null;
  email: string;
}

export function useMyProfile(): ProfileSnapshot | null {
  const auth = useAuth();
  const userId = auth.identity?.user_id ?? null;
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);

  useEffect(() => {
    if (userId === null) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response: UserProfile = await getMyProfile();
        if (cancelled) return;
        setProfile({
          display_name: response.display_name,
          email: response.email,
        });
      } catch {
        if (!cancelled) setProfile(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  return profile;
}
