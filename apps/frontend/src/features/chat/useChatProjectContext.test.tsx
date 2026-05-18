// Phase 6.5 §4 — P6.5-C1 context-aware chat creation.
//
// Tests pin the §4.2 contract:
//   1. Route with project_id → hook resolves to that id (the wire
//      payload's `project_id` will be set).
//   2. Route without project_id (null) → hook resolves to null (Unfiled;
//      `createConversation` omits the field).
//   3. User override → overrides the route; clearing the override
//      reverts to the route default.
//   4. Explicit `null` override on a project route → resolves to null
//      ("file as Unfiled even though I'm on a project page" — required
//      by §4.2's untrusted-input rule).
//
// These mirror PRD §4.4 test gates and the §10.2 acceptance test list.

import type { ProjectId } from "@enterprise-search/api-types";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useChatProjectContext } from "./useChatProjectContext";

const PROJECT_A = "proj_a" as ProjectId;
const PROJECT_B = "proj_b" as ProjectId;

describe("useChatProjectContext", () => {
  it("returns the route project_id when no override is set", () => {
    const { result } = renderHook(() => useChatProjectContext(PROJECT_A));
    expect(result.current.projectId).toBe(PROJECT_A);
    expect(result.current.routeProjectId).toBe(PROJECT_A);
    expect(result.current.hasOverride).toBe(false);
  });

  it("returns null when the route has no project_id (e.g. /chats)", () => {
    const { result } = renderHook(() => useChatProjectContext(null));
    expect(result.current.projectId).toBeNull();
    expect(result.current.routeProjectId).toBeNull();
    expect(result.current.hasOverride).toBe(false);
  });

  it("override to a different project beats the route default", () => {
    const { result } = renderHook(() => useChatProjectContext(PROJECT_A));

    act(() => {
      result.current.setOverride(PROJECT_B);
    });

    expect(result.current.projectId).toBe(PROJECT_B);
    expect(result.current.routeProjectId).toBe(PROJECT_A);
    expect(result.current.hasOverride).toBe(true);
  });

  it("override to null files the chat as Unfiled even on a project route", () => {
    // PRD §4.2: the chip's selected value is what the request sends.
    // The user must be able to opt out of the route default explicitly.
    const { result } = renderHook(() => useChatProjectContext(PROJECT_A));

    act(() => {
      result.current.setOverride(null);
    });

    expect(result.current.projectId).toBeNull();
    expect(result.current.hasOverride).toBe(true);
  });

  it("clearOverride reverts to the route default", () => {
    const { result } = renderHook(() => useChatProjectContext(PROJECT_A));

    act(() => {
      result.current.setOverride(null);
    });
    expect(result.current.projectId).toBeNull();
    expect(result.current.hasOverride).toBe(true);

    act(() => {
      result.current.clearOverride();
    });

    expect(result.current.projectId).toBe(PROJECT_A);
    expect(result.current.hasOverride).toBe(false);
  });

  it("override on /chats (null route) can pin a chat to a project", () => {
    // The /chats direct route defaults to Unfiled, but the user can
    // still pick a project via the chip — symmetric with the
    // override-to-null path above.
    const { result } = renderHook(() => useChatProjectContext(null));

    act(() => {
      result.current.setOverride(PROJECT_B);
    });

    expect(result.current.projectId).toBe(PROJECT_B);
    expect(result.current.routeProjectId).toBeNull();
    expect(result.current.hasOverride).toBe(true);
  });

  it("tracks route project_id changes when no override is set", () => {
    // Navigation between project pages should update the resolved
    // default without the user touching the chip.
    const { result, rerender } = renderHook(
      ({ route }: { route: ProjectId | null }) => useChatProjectContext(route),
      { initialProps: { route: PROJECT_A as ProjectId | null } },
    );

    expect(result.current.projectId).toBe(PROJECT_A);

    rerender({ route: PROJECT_B });
    expect(result.current.projectId).toBe(PROJECT_B);
    expect(result.current.hasOverride).toBe(false);

    rerender({ route: null });
    expect(result.current.projectId).toBeNull();
  });

  it("override sticks across route changes until cleared", () => {
    // Per task spec: "chip's override sticks per chat for as long as
    // composer is open (resets on navigation)" — interpreted at the
    // hook level as: override survives any rerender of the SAME hook
    // instance. Navigation that unmounts the chat surface remounts the
    // hook with no override (covered by re-renderHook).
    const { result, rerender } = renderHook(
      ({ route }: { route: ProjectId | null }) => useChatProjectContext(route),
      { initialProps: { route: PROJECT_A as ProjectId | null } },
    );

    act(() => {
      result.current.setOverride(null);
    });
    expect(result.current.projectId).toBeNull();

    // Route changes underneath; override is still in effect.
    rerender({ route: PROJECT_B });
    expect(result.current.projectId).toBeNull();
    expect(result.current.hasOverride).toBe(true);
  });
});
