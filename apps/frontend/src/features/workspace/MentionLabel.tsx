// PR 3.3 — `<MentionLabel userId="usr_…" />` resolves to "@marcus" via
// the session-scoped cache + `useWorkspaceMember` hook. While loading
// or after a 404 fallback we render the raw user_id so the inline
// approval/forwarded card always says *something*. The handle prefix
// (`@`) is fixed: every workspace member is rendered as a mention.
//
// Identity resolution: pulls the request identity from the existing
// `AuthContext` so call sites don't have to thread it through. When
// the session is anonymous (signed-out preview / shared-thread view in
// W6) the hook short-circuits and we render the raw id — same outcome
// as the 404 fallback.

import { useContext, useMemo, type ReactElement } from "react";
import { AuthContext } from "../auth/AuthContext";
import type { RequestIdentity } from "../../api/config";
import { useWorkspaceMember } from "./useWorkspaceMember";

export function MentionLabel({
  userId,
  fallbackPrefix = "@",
}: {
  userId: string | null;
  fallbackPrefix?: string;
}): ReactElement | null {
  // PR 3.3 — read AuthContext directly (not through ``useAuth()``) so
  // the chip degrades to the raw user_id when rendered outside an
  // ``<AuthProvider>`` (storybook, tests, shared-thread preview). The
  // hook short-circuits to no-fetch when identity is null, mirroring
  // its 404-fallback path.
  const auth = useContext(AuthContext);
  const session = auth?.identity ?? null;
  const requestIdentity = useMemo<RequestIdentity | null>(() => {
    if (session === null) {
      return null;
    }
    return { orgId: session.org_id, userId: session.user_id };
  }, [session]);
  const member = useWorkspaceMember(userId, requestIdentity);
  if (userId === null) {
    return null;
  }
  // Prefer the explicit handle when provided; fall back to display name;
  // last resort is the raw user_id (so a member that was removed still
  // renders a label).
  const label = member?.handle ?? (member ? member.display_name : userId);
  const aria = member
    ? `${member.display_name} (member)`
    : `${userId} (member)`;
  return (
    <span
      className="atlas-mention-label"
      data-resolved={member ? "true" : undefined}
      data-user-id={userId}
      aria-label={aria}
      title={
        member?.email ?? member?.display_name ?? "Member no longer in workspace"
      }
    >
      {fallbackPrefix}
      {label}
    </span>
  );
}
