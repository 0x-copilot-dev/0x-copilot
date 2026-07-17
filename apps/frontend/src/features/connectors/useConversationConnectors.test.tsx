/**
 * PR 1.2 + PR 1.2.1 — useConversationConnectors hook.
 *
 * Covers seed + re-seed (PR 1.2) and the visibilitychange refetch path
 * with the in-flight-PATCH guard (PR 1.2.1). The optimistic-update +
 * rollback branches stay validated end-to-end by the ai-backend route
 * test (services/ai-backend/tests/unit/runtime_api/test_conversation_connector_scope_route.py)
 * and the Wave 3.4 ConnectorPopover tests.
 *
 * Refetch tests stub ``window.fetch`` directly (mirrors agentApi.test.ts)
 * to avoid the vi.mock-hoisting hang we hit when mocking the module
 * surface.
 */

import type { Conversation } from "@0x-copilot/api-types";
import {
  DocumentPresenceSignal,
  PresenceSignalProvider,
} from "@0x-copilot/chat-surface";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConversationConnectors } from "./useConversationConnectors";

// Wrap renderHook calls so the hook reads a real PresenceSignal backed
// by document. The visibilitychange events dispatched in flushVisibility
// then reach the hook via the port instead of being silently swallowed
// by the tolerant ALWAYS_VISIBLE default in the provider context.
function withPresenceSignal({ children }: { children: ReactNode }): ReactNode {
  return (
    <PresenceSignalProvider signal={new DocumentPresenceSignal()}>
      {children}
    </PresenceSignalProvider>
  );
}

const IDENTITY = { orgId: "org_pr12", userId: "user_pr12" } as const;

function conversation(
  enabled: Conversation["enabled_connectors"] = {},
  connectorsUpdatedAt: string | null = null,
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
    connectors_updated_at: connectorsUpdatedAt,
  };
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", {
    value: state,
    configurable: true,
  });
}

async function flushVisibility(): Promise<void> {
  await act(async () => {
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    // Drain the .then chain inside the listener.
    await Promise.resolve();
    await Promise.resolve();
  });
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
    const initialProps: { conv: Conversation | null } = {
      conv: conversation({ slack: ["read"] }),
    };
    const { result, rerender } = renderHook(
      ({ conv }: { conv: Conversation | null }) =>
        useConversationConnectors(conv, IDENTITY),
      { initialProps },
    );
    expect(result.current.scopes).toEqual({ slack: ["read"] });
    rerender({ conv: null });
    expect(result.current.scopes).toEqual({});
  });
});

describe("useConversationConnectors · visibilitychange refetch (PR 1.2.1)", () => {
  beforeEach(() => {
    setVisibility("visible");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reconciles to server scopes when the server is strictly newer", async () => {
    const fetchSpy = vi
      .spyOn(window, "fetch")
      .mockResolvedValue(
        jsonResponse(
          conversation(
            { slack: null, drive: ["read"] },
            "2026-05-05T00:01:00Z",
          ),
        ),
      );
    const seed = conversation({ slack: ["read"] }, "2026-05-05T00:00:00Z");
    const { result } = renderHook(
      () => useConversationConnectors(seed, IDENTITY),
      { wrapper: withPresenceSignal },
    );

    setVisibility("hidden");
    await flushVisibility();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0][0])).toContain(
      "/v1/agent/conversations/conv_pr12",
    );
    await waitFor(() => {
      expect(result.current.scopes).toEqual({ slack: null, drive: ["read"] });
    });
  });

  it("does not overwrite local state when the server timestamp is not newer", async () => {
    const stamp = "2026-05-05T00:00:00Z";
    vi.spyOn(window, "fetch").mockResolvedValue(
      jsonResponse(conversation({ drive: ["read"] }, stamp)),
    );
    const seed = conversation({ slack: ["read"] }, stamp);
    const { result } = renderHook(
      () => useConversationConnectors(seed, IDENTITY),
      { wrapper: withPresenceSignal },
    );

    await flushVisibility();

    // Server's connectors_updated_at equals the local one — no overwrite.
    expect(result.current.scopes).toEqual({ slack: ["read"] });
  });

  it("removes its visibilitychange listener on unmount", async () => {
    const fetchSpy = vi
      .spyOn(window, "fetch")
      .mockResolvedValue(
        jsonResponse(conversation({ slack: ["read"] }, "2026-05-05T00:00:00Z")),
      );
    const { unmount } = renderHook(
      () =>
        useConversationConnectors(
          conversation({ slack: ["read"] }, "2026-05-05T00:00:00Z"),
          IDENTITY,
        ),
      { wrapper: withPresenceSignal },
    );
    unmount();
    await flushVisibility();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
