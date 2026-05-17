import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdapterReviewQueue } from "./AdapterReviewQueue";
import type { AdapterReviewCandidateSummary } from "./types";

const IDENTITY = { orgId: "org_atlas", userId: "usr_admin" };

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function candidate(
  overrides: Partial<AdapterReviewCandidateSummary> = {},
): AdapterReviewCandidateSummary {
  return {
    candidate_id: "cand_default",
    scheme: "atlas://hubspot",
    layout_template: "form",
    origin_tenant_redacted: "tenant_abc12345",
    generator_model: "anthropic:opus-4.7",
    submitted_at: "2026-05-01T12:00:00+00:00",
    status: "submitted",
    session_count: 10,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdapterReviewQueue", () => {
  it("fetches candidates and renders them sorted by submitted_at desc", async () => {
    const olderCandidate = candidate({
      candidate_id: "cand_old",
      submitted_at: "2026-04-01T00:00:00+00:00",
    });
    const newerCandidate = candidate({
      candidate_id: "cand_new",
      submitted_at: "2026-05-15T00:00:00+00:00",
    });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidates: [olderCandidate, newerCandidate],
          next_cursor: null,
          has_more: false,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewQueue identity={IDENTITY} onOpen={() => {}} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("adapter-review-row-cand_new"),
      ).toBeInTheDocument();
    });

    const rows = screen.getAllByText(/cand_/);
    // Sorted desc — newer first.
    expect(rows[0].textContent).toContain("cand_new");
    expect(rows[1].textContent).toContain("cand_old");
  });

  it("filters by status and re-fetches", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidates: [candidate()],
          next_cursor: null,
          has_more: false,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewQueue identity={IDENTITY} onOpen={() => {}} />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByTestId("filter-status"), {
      target: { value: "approved" },
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    });
    const latestUrl = String(
      fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0],
    );
    expect(latestUrl).toContain("status=approved");
  });

  it("filters by layout and scheme through the URL query", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidates: [candidate()],
          next_cursor: null,
          has_more: false,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewQueue identity={IDENTITY} onOpen={() => {}} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId("filter-layout"), {
      target: { value: "table" },
    });
    fireEvent.change(screen.getByTestId("filter-scheme"), {
      target: { value: "atlas://linear" },
    });

    await waitFor(() => {
      const latest = String(
        fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0],
      );
      expect(latest).toContain("layout=table");
      expect(latest).toContain("scheme=atlas%3A%2F%2Flinear");
    });
  });

  it("invokes onOpen with the candidate id when a row is clicked", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidates: [candidate({ candidate_id: "cand_pick" })],
          next_cursor: null,
          has_more: false,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onOpen = vi.fn();
    render(<AdapterReviewQueue identity={IDENTITY} onOpen={onOpen} />);

    const row = await screen.findByTestId("adapter-review-row-cand_pick");
    fireEvent.click(row);

    expect(onOpen).toHaveBeenCalledWith("cand_pick");
  });

  it("renders the empty-state row when the API returns no candidates", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({ candidates: [], next_cursor: null, has_more: false }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewQueue identity={IDENTITY} onOpen={() => {}} />);

    await waitFor(() =>
      expect(screen.getByTestId("adapter-review-empty")).toBeInTheDocument(),
    );
  });
});
