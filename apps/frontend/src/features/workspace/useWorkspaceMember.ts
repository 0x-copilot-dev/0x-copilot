// PR 3.3 — single-id workspace member resolver with a process-wide
// cache.
//
// The hook resolves ``user_id -> { display_name, email?, handle? }``
// for one user at a time and caches the lookup for the lifetime of
// the page. The cache is intentionally global (module-level) so two
// components rendering the same user (e.g. <ApprovalTool> and a future
// @-mention picker) share a single in-flight request and a single
// resolved entry. Cache invalidation is a hard reload — short enough
// for our use case (display names change rarely) and simple enough
// that we don't need a query client to manage it.
//
// On 404 the hook caches an "unresolved" marker so we don't keep
// hammering the endpoint while the facade route is still a follow-up.
// On 5xx the entry is left missing so the next mount tries again.
//
// DRY anchor: ``WorkspaceMemberPicker`` (PR 1.4.1 Phase C) consumes the
// same module via :func:`primeWorkspaceMember` so a typeahead pick
// pre-warms the cache for the inline chip that renders right after.

import { useEffect, useState } from "react";
import {
  getWorkspaceMember,
  type WorkspaceMemberResponse,
} from "../../api/workspaceApi";
import type { RequestIdentity } from "../../api/config";

export interface WorkspaceMember {
  user_id: string;
  display_name: string;
  email?: string | null;
  handle?: string | null;
}

type CacheEntry =
  | { status: "loading"; promise: Promise<WorkspaceMember | null> }
  | { status: "resolved"; member: WorkspaceMember }
  | { status: "unresolved"; user_id: string };

const _cache = new Map<string, CacheEntry>();

/** Pre-warm the cache from a known member (e.g. picker selection). */
export function primeWorkspaceMember(member: WorkspaceMember): void {
  _cache.set(member.user_id, { status: "resolved", member });
}

/** Test-only hatch — never call in app code. */
export function _resetWorkspaceMemberCache(): void {
  _cache.clear();
}

/** Read the cached member if any. Does not trigger a fetch. */
export function peekWorkspaceMember(userId: string): WorkspaceMember | null {
  const entry = _cache.get(userId);
  return entry && entry.status === "resolved" ? entry.member : null;
}

async function fetchMember(
  userId: string,
  identity: RequestIdentity,
): Promise<WorkspaceMember | null> {
  try {
    const response: WorkspaceMemberResponse = await getWorkspaceMember(
      userId,
      identity,
    );
    const member: WorkspaceMember = {
      user_id: response.user_id,
      display_name: response.display_name,
      email: response.email ?? null,
      handle: response.handle ?? null,
    };
    _cache.set(userId, { status: "resolved", member });
    return member;
  } catch (err) {
    // Cache 404 ("Member no longer in workspace" or facade route not
    // shipped yet) so we don't keep hammering. Other errors leave the
    // entry missing — next mount retries.
    if (err instanceof Error && /\b404\b/.test(err.message)) {
      _cache.set(userId, { status: "unresolved", user_id: userId });
      return null;
    }
    return null;
  }
}

/**
 * Resolve a workspace member by user_id with a session-scoped cache.
 *
 * Returns:
 *  - the cached member, or
 *  - ``null`` while loading (or when the lookup permanently failed —
 *    the call site renders a fallback in either case).
 *
 * The hook is safe for the loading-then-resolved transition: a second
 * mount for the same user during the in-flight fetch shares the
 * promise and re-renders once it resolves.
 */
export function useWorkspaceMember(
  userId: string | null,
  identity: RequestIdentity | null,
): WorkspaceMember | null {
  const [, setVersion] = useState(0);

  useEffect(() => {
    if (userId === null || identity === null) {
      return;
    }
    const cached = _cache.get(userId);
    if (cached && cached.status === "resolved") {
      return;
    }
    if (cached && cached.status === "unresolved") {
      return;
    }
    if (cached && cached.status === "loading") {
      let cancelled = false;
      void cached.promise.then(() => {
        if (!cancelled) setVersion((v) => v + 1);
      });
      return () => {
        cancelled = true;
      };
    }

    let cancelled = false;
    const promise = fetchMember(userId, identity).then((member) => {
      if (!cancelled) {
        setVersion((v) => v + 1);
      }
      return member;
    });
    _cache.set(userId, { status: "loading", promise });
    return () => {
      cancelled = true;
    };
  }, [userId, identity]);

  if (userId === null) {
    return null;
  }
  const entry = _cache.get(userId);
  if (entry && entry.status === "resolved") {
    return entry.member;
  }
  return null;
}
