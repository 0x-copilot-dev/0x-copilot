import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { SourceEntry, SourceListResponse } from "@0x-copilot/api-types";
import * as agentApi from "../../../../api/agentApi";
import type { RequestIdentity } from "../../../../api/config";
import { useArchivedSources } from "./useArchivedSources";

const IDENTITY: RequestIdentity = {
  orgId: "org_test",
  userId: "usr_test",
};

function source(overrides: Partial<SourceEntry> = {}): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "notion",
    source_doc_id: "doc1",
    source_url: "https://example.com",
    title: "Doc 1",
    snippet: null,
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-05T12:00:00Z",
    ...overrides,
  };
}

describe("useArchivedSources", () => {
  it("seeds the SourceEntryMap from listSources on conversation switch", async () => {
    const response: SourceListResponse = {
      conversation_id: "conv-1",
      run_id: null,
      sources: [
        source({ source_doc_id: "a", title: "Alpha" }),
        source({
          source_doc_id: "b",
          title: "Beta",
          source_connector: "drive",
        }),
      ],
      truncated: false,
    };
    const spy = vi.spyOn(agentApi, "listSources").mockResolvedValue(response);
    const { result } = renderHook(() => useArchivedSources("conv-1", IDENTITY));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledWith("conv-1", IDENTITY);
    expect(result.current.sources.size).toBe(2);
    expect([...result.current.sources.values()].map((s) => s.title)).toEqual([
      "Alpha",
      "Beta",
    ]);
    expect(result.current.error).toBeNull();
    spy.mockRestore();
  });

  it("clears state when conversationId becomes null", async () => {
    const spy = vi.spyOn(agentApi, "listSources").mockResolvedValue({
      conversation_id: "conv-1",
      run_id: null,
      sources: [source()],
      truncated: false,
    });
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useArchivedSources(id, IDENTITY),
      { initialProps: { id: "conv-1" as string | null } },
    );
    await waitFor(() => expect(result.current.sources.size).toBe(1));
    rerender({ id: null });
    expect(result.current.sources.size).toBe(0);
    spy.mockRestore();
  });

  it("surfaces a friendly error on failure", async () => {
    const spy = vi
      .spyOn(agentApi, "listSources")
      .mockRejectedValue(new Error("nope"));
    const { result } = renderHook(() => useArchivedSources("conv-1", IDENTITY));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("nope");
    expect(result.current.sources.size).toBe(0);
    spy.mockRestore();
  });

  it("does not apply a stale resolution after a conversation switch", async () => {
    const slow = new Promise<SourceListResponse>((resolve) => {
      setTimeout(
        () =>
          resolve({
            conversation_id: "conv-1",
            run_id: null,
            sources: [source({ title: "Stale" })],
            truncated: false,
          }),
        20,
      );
    });
    const fresh: SourceListResponse = {
      conversation_id: "conv-2",
      run_id: null,
      sources: [source({ source_doc_id: "z", title: "Fresh" })],
      truncated: false,
    };
    const spy = vi
      .spyOn(agentApi, "listSources")
      .mockImplementationOnce(() => slow)
      .mockResolvedValueOnce(fresh);

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useArchivedSources(id, IDENTITY),
      { initialProps: { id: "conv-1" } },
    );
    rerender({ id: "conv-2" });
    await waitFor(() =>
      expect([...result.current.sources.values()].map((s) => s.title)).toEqual([
        "Fresh",
      ]),
    );
    // Allow the slow promise to settle and confirm it never overwrote.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect([...result.current.sources.values()].map((s) => s.title)).toEqual([
      "Fresh",
    ]);
    spy.mockRestore();
  });
});
