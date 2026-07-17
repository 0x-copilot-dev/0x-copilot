// Phase 6.5 §4 — context-aware chat creation (P6.5-C1).
//
// Tiny resolver hook for "what `project_id` should the next-created
// conversation be filed under?"
//
// Contract (per `docs/atlas-new-design/destinations/projects-extensions-prd.md` §4.2):
//
//   1. The route is the default source — when the user is on
//      `/projects/<id>` (or any subroute) the caller resolves `<id>` and
//      passes it in as `routeProjectId`. When the user is on `/chats`
//      (or any non-project route) the caller passes `null`.
//
//   2. The composer's `[Filed under: ▾]` chip lets the user OVERRIDE
//      the inferred default — either to clear (file as Unfiled) or to
//      change to a different project. The override sticks for as long
//      as the composer is open (i.e. for the lifetime of the hook
//      instance), and is cleared by `clearOverride()` so the user can
//      revert to "follow the route".
//
//   3. NO magic inference: the resolved value is exactly the override
//      when one is set, otherwise exactly the route value. There is no
//      heuristic, no last-used-project memory, no auto-detection — the
//      compliance posture is "untrusted-input rule" (PRD §4.2): the
//      chip's selected value is what the request sends; the route is
//      the default, not a hard binding.
//
// The hook is intentionally tiny so it is testable in isolation without
// mounting the full chat surface. The chip UI and ChatScreen integration
// consume it; this file owns nothing else.

import { useCallback, useMemo, useState } from "react";

import type { ProjectId } from "@0x-copilot/api-types";

/**
 * Override state. `undefined` means "no override; follow the route".
 * `null` means "explicit Unfiled override". A concrete `ProjectId`
 * means "explicit project pick". The three-way distinction matters
 * because the user may be on a project route AND want to file the
 * chat as Unfiled — `null` is a real value, not the absence of one.
 */
type Override = ProjectId | null | undefined;

export interface ChatProjectContext {
  /**
   * The effective `project_id` to pass to `createConversation`. This
   * is what the wire request should carry. `null` ⇒ "Unfiled" (the
   * field is omitted from the wire payload by `createConversation`;
   * see agentApi.ts for the omission rule).
   */
  readonly projectId: ProjectId | null;
  /**
   * The route's inferred project_id (echoed back verbatim). Exposed so
   * the chip can render "filed under <route project> (default)" vs.
   * "filed under <override> (overridden)" affordances.
   */
  readonly routeProjectId: ProjectId | null;
  /**
   * `true` when the user has explicitly set an override (either to
   * Unfiled or to a different project). `false` when the chip is
   * showing the route default.
   */
  readonly hasOverride: boolean;
  /**
   * Set an explicit override. Pass `null` to file the chat as Unfiled
   * even when the route would default to a project; pass a concrete
   * `ProjectId` to file it under that project regardless of route.
   */
  setOverride(next: ProjectId | null): void;
  /**
   * Clear the override and revert to the route default.
   */
  clearOverride(): void;
}

export function useChatProjectContext(
  routeProjectId: ProjectId | null,
): ChatProjectContext {
  const [override, setOverrideState] = useState<Override>(undefined);

  const setOverride = useCallback((next: ProjectId | null): void => {
    setOverrideState(next);
  }, []);
  const clearOverride = useCallback((): void => {
    setOverrideState(undefined);
  }, []);

  return useMemo<ChatProjectContext>(() => {
    const hasOverride = override !== undefined;
    const projectId = hasOverride ? (override ?? null) : routeProjectId;
    return {
      projectId,
      routeProjectId,
      hasOverride,
      setOverride,
      clearOverride,
    };
  }, [override, routeProjectId, setOverride, clearOverride]);
}
