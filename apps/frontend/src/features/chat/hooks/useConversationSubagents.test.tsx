/**
 * PR 1.5 — `useConversationSubagents` hook.
 *
 * Verifies the seed + live-merge composition that backs the Workspace
 * pane Agents tab:
 *
 * - mounts and seeds from `GET /v1/agent/conversations/{cid}/subagents`
 * - exposes `loading` while the seed is in flight and clears it on resolve
 * - reduces `SUBAGENT_*` events through the existing pure projector
 *   (`subagentReducer.applySubagentEvent`) without re-fetching
 * - propagates `token_usage` (PR 1.5 AC-2) through to the entries
 * - resets to empty when the conversation switches to `null`
 *
 * Stubs `window.fetch` directly (mirrors agentApi.test.ts /
 * useConversationConnectors.test.tsx) to avoid mock-hoisting fragility.
 */

import type {
  RuntimeEventEnvelope,
  SubagentEntry,
  SubagentListResponse,
} from "@enterprise-search/api-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConversationSubagents } from "./useConversationSubagents";

const IDENTITY = { orgId: "org_pr15", userId: "user_pr15" } as const;
const CONV = "conv_launch";
const RUN = "run_alpha";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function entry(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_research",
    parent_run_id: RUN,
    subagent_name: "research",
    status: "running",
    display_title: "Reviewing positioning",
    objective_summary: "Investigate competitive frame",
    started_at: "2026-05-04T12:00:00Z",
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

function seed(entries: SubagentEntry[]): SubagentListResponse {
  return {
    conversation_id: CONV,
    subagents: entries,
    truncated: false,
  };
}

function subagentEvent(
  overrides: Partial<RuntimeEventEnvelope> &
    Pick<RuntimeEventEnvelope, "event_type" | "task_id">,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_${overrides.task_id}_${overrides.event_type}`,
    run_id: RUN,
    conversation_id: CONV,
    sequence_no: 1,
    activity_kind: "subagent",
    created_at: "2026-05-04T12:00:12Z",
    source: "subagent",
    payload: {},
    metadata: {},
    visibility: "user",
    redaction_state: "redacted",
    trace_id: "trace_1",
    ...overrides,
  } as RuntimeEventEnvelope;
}

describe("useConversationSubagents", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("seeds from listSubagents on mount", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        seed([
          entry({
            task_id: "task_with_tokens",
            token_usage: {
              input_tokens: 500,
              output_tokens: 100,
              cached_input_tokens: 120,
              total_tokens: 600,
            },
          }),
        ]),
      ),
    );
    const { result } = renderHook(() =>
      useConversationSubagents({
        conversationId: CONV,
        identity: IDENTITY,
        liveEvent: null,
      }),
    );
    expect(result.current.loading).toBe(true);
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.entries).toHaveLength(1);
    const e = result.current.entries[0];
    expect(e.task_id).toBe("task_with_tokens");
    // PR 1.5 AC-2 — token_usage rolled up server-side.
    expect(e.token_usage).toEqual({
      input_tokens: 500,
      output_tokens: 100,
      cached_input_tokens: 120,
      total_tokens: 600,
    });
    // The hook hits `/v1/agent/conversations/{cid}/subagents`.
    const url = String(fetchMock.mock.calls[0]?.[0] ?? "");
    expect(url).toContain(`/v1/agent/conversations/${CONV}/subagents`);
  });

  it("merges a SUBAGENT_COMPLETED event into the seeded map without re-fetching", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(seed([entry()])));
    const { result, rerender } = renderHook(
      ({ liveEvent }: { liveEvent: RuntimeEventEnvelope | null }) =>
        useConversationSubagents({
          conversationId: CONV,
          identity: IDENTITY,
          liveEvent,
        }),
      { initialProps: { liveEvent: null as RuntimeEventEnvelope | null } },
    );
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.entries[0]?.status).toBe("running");

    await act(async () => {
      rerender({
        liveEvent: subagentEvent({
          event_type: "subagent_completed",
          task_id: "task_research",
          summary: "Glean leads on legacy search",
          status: "completed",
          subagent_id: "research",
        }),
      });
    });

    expect(result.current.entries[0]?.status).toBe("completed");
    expect(result.current.entries[0]?.result_summary).toBe(
      "Glean leads on legacy search",
    );
    // No second fetch — live merge is purely client-side.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces fetch errors and stops loading", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    const { result } = renderHook(() =>
      useConversationSubagents({
        conversationId: CONV,
        identity: IDENTITY,
        liveEvent: null,
      }),
    );
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBe("network down");
    expect(result.current.entries).toHaveLength(0);
  });

  it("resets to empty when the conversation becomes null", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(seed([entry()])));
    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId: string | null }) =>
        useConversationSubagents({
          conversationId,
          identity: IDENTITY,
          liveEvent: null,
        }),
      { initialProps: { conversationId: CONV as string | null } },
    );
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.entries).toHaveLength(1);

    await act(async () => {
      rerender({ conversationId: null });
    });

    expect(result.current.entries).toHaveLength(0);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
