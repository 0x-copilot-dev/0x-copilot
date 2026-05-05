/**
 * PR 1.2 — useConversationConnectors hook.
 *
 * Covers seed + re-seed semantics. The optimistic-update + rollback
 * branches are validated end-to-end by the Wave 3.4 ConnectorPopover
 * tests (next PR), and by the ai-backend route test
 * (services/ai-backend/tests/unit/runtime_api/test_conversation_connector_scope_route.py).
 */

import type { Conversation } from "@enterprise-search/api-types";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useConversationConnectors } from "./useConversationConnectors";

const IDENTITY = { orgId: "org_pr12", userId: "user_pr12" } as const;

function conversation(
  enabled: Conversation["enabled_connectors"] = {},
): Conversation {
  return {
    conversation_id: "conv_pr12",
    org_id: "org_pr12",
    user_id: "user_pr12",
    assistant_id: "assistant_pr12",
    title: "scope test",
    status: "active",
    created_at: "2026-05-05T00:00:00Z",
    updated_at: "2026-05-05T00:00:00Z",
    archived_at: null,
    metadata: {},
    schema_version: 1,
    enabled_connectors: enabled,
    connectors_updated_at: null,
  };
}

describe("useConversationConnectors", () => {
  it("seeds from the conversation snapshot", () => {
    const { result } = renderHook(() =>
      useConversationConnectors(conversation({ slack: ["read"] }), IDENTITY),
    );
    expect(result.current.scopes).toEqual({ slack: ["read"] });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("seeds to empty object when conversation is null", () => {
    const { result } = renderHook(() =>
      useConversationConnectors(null, IDENTITY),
    );
    expect(result.current.scopes).toEqual({});
  });

  it("re-seeds when switching conversations", () => {
    const { result, rerender } = renderHook(
      ({ conv }: { conv: Conversation | null }) =>
        useConversationConnectors(conv, IDENTITY),
      { initialProps: { conv: conversation({ slack: ["read"] }) } },
    );
    expect(result.current.scopes).toEqual({ slack: ["read"] });
    rerender({ conv: conversation({ drive: ["read", "comment"] }) });
    expect(result.current.scopes).toEqual({ drive: ["read", "comment"] });
  });

  it("clears state when switching to a null conversation", () => {
    const { result, rerender } = renderHook(
      ({ conv }: { conv: Conversation | null }) =>
        useConversationConnectors(conv, IDENTITY),
      { initialProps: { conv: conversation({ slack: ["read"] }) } },
    );
    expect(result.current.scopes).toEqual({ slack: ["read"] });
    rerender({ conv: null });
    expect(result.current.scopes).toEqual({});
  });
});
