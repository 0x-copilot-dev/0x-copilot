import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequestIdentity } from "../../api/config";

const listConversations = vi.fn();
vi.mock("../../api/agentApi", () => ({
  listConversations: (...args: unknown[]) => listConversations(...args),
}));

import { useActiveRunCount } from "./useActiveRunCount";

const IDENTITY = { orgId: "o", userId: "u" } as unknown as RequestIdentity;

function convo(latest_run_status: string | null): unknown {
  return { id: "c", latest_run_status };
}

afterEach(() => {
  listConversations.mockReset();
});

describe("useActiveRunCount", () => {
  it("counts only in-flight run statuses", async () => {
    listConversations.mockResolvedValue({
      conversations: [
        convo("running"),
        convo("queued"),
        convo("waiting_for_approval"),
        convo("cancelling"),
        convo("completed"), // terminal — not counted
        convo("failed"), // terminal — not counted
        convo(null), // no run — not counted
      ],
    });
    const { result } = renderHook(() => useActiveRunCount(IDENTITY));
    await waitFor(() => expect(result.current).toBe(4));
  });

  it("returns 0 and does not fetch when signed out", async () => {
    const { result } = renderHook(() => useActiveRunCount(null));
    expect(result.current).toBe(0);
    expect(listConversations).not.toHaveBeenCalled();
  });

  it("keeps the last known count when a poll fails", async () => {
    listConversations.mockResolvedValueOnce({
      conversations: [convo("running")],
    });
    const { result } = renderHook(() => useActiveRunCount(IDENTITY));
    await waitFor(() => expect(result.current).toBe(1));
    // A subsequent rejection must not clear the badge.
    listConversations.mockRejectedValue(new Error("network"));
    await new Promise((r) => setTimeout(r, 0));
    expect(result.current).toBe(1);
  });
});
