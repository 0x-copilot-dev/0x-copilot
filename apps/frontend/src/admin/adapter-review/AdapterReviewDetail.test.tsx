import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdapterReviewDetail } from "./AdapterReviewDetail";
import type { AdapterReviewCandidateDetail } from "./types";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function candidate(
  overrides: Partial<AdapterReviewCandidateDetail> = {},
): AdapterReviewCandidateDetail {
  return {
    candidate_id: "cand_42",
    scheme: "atlas://linear",
    layout_template: "form",
    origin_tenant_redacted: "tenant_abc12345",
    generator_model: "anthropic:opus-4.7",
    submitted_at: "2026-05-02T14:00:00+00:00",
    status: "in-review",
    candidate_source:
      "module.exports = { renderCurrent: () => '', renderDiff: () => '' };",
    schema_version: 1,
    history: [],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdapterReviewDetail", () => {
  it("fetches the candidate and renders the three panes", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(candidate()),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId("adapter-review-source")).toBeInTheDocument();
    });
    expect(screen.getByTestId("adapter-review-state")).toBeInTheDocument();
    expect(screen.getByTestId("adapter-preview")).toBeInTheDocument();
    // Synthetic state must be visible to the reviewer — the literal
    // marker "synthetic" appears in every sample.
    const state = screen.getByTestId("adapter-review-state");
    expect(state.textContent?.toLowerCase()).toContain("synthetic");
  });

  it("never displays raw tenant identifiers — only the redacted handle", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        candidate({ origin_tenant_redacted: "tenant_redacted_xyz" }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    await waitFor(() =>
      expect(screen.getByTestId("adapter-review-meta")).toBeInTheDocument(),
    );
    const meta = screen.getByTestId("adapter-review-meta");
    expect(meta.textContent).toContain("tenant_redacted_xyz");
    // The raw ``org_id`` of the origin tenant is never on the wire (7A
    // redacts it). This test pins that the FE doesn't try to render
    // anything else under the meta strip.
    expect(meta.textContent).not.toMatch(/org_[a-z0-9]+/);
  });

  it("submits an approve decision and re-fetches the candidate", async () => {
    const updated = candidate({
      status: "approved",
      history: [
        {
          decided_at: "2026-05-17T10:00:00+00:00",
          decided_by_user_id: "usr_admin",
          action: "approve",
          notes: "Looks good.",
        },
      ],
    });
    const fetchMock = vi
      .fn()
      // initial GET
      .mockResolvedValueOnce(jsonResponse(candidate()))
      // POST decision
      .mockResolvedValueOnce(
        jsonResponse({
          candidate_id: "cand_42",
          status: "approved",
          decided_at: "2026-05-17T10:00:00+00:00",
          decided_by_user_id: "usr_admin",
          action: "approve",
          notes: "Looks good.",
        }),
      )
      // re-fetch
      .mockResolvedValueOnce(jsonResponse(updated));
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    const notes = await screen.findByTestId("adapter-review-notes");
    fireEvent.change(notes, { target: { value: "Looks good." } });

    fireEvent.click(screen.getByTestId("adapter-review-approve"));

    await waitFor(() => {
      // The POST was issued.
      const decisionCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).includes(
          "/v1/admin/adapter_registry/candidates/cand_42/decisions",
        ),
      );
      expect(decisionCall).toBeDefined();
      const init = decisionCall?.[1] as RequestInit;
      expect(init.method).toBe("POST");
      const body = JSON.parse(init.body as string);
      expect(body).toEqual({ action: "approve", notes: "Looks good." });
    });

    // After re-fetch, the decision history surface shows the new entry.
    await waitFor(() =>
      expect(
        screen.getByTestId("adapter-review-history-0"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("adapter-review-history-0").textContent,
    ).toContain("approve");
  });

  it("submits a reject decision via the reject button", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate()))
      .mockResolvedValueOnce(
        jsonResponse({
          candidate_id: "cand_42",
          status: "rejected",
          decided_at: "2026-05-17T11:00:00+00:00",
          decided_by_user_id: "usr_admin",
          action: "reject",
          notes: "",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(candidate({ status: "rejected" })));
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    await screen.findByTestId("adapter-review-reject");
    fireEvent.click(screen.getByTestId("adapter-review-reject"));

    await waitFor(() => {
      const decisionCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).includes(
          "/v1/admin/adapter_registry/candidates/cand_42/decisions",
        ),
      );
      const body = JSON.parse(
        (decisionCall?.[1] as RequestInit).body as string,
      );
      expect(body.action).toBe("reject");
    });
  });

  it("submits a request-changes decision via the dedicated button", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(candidate()))
      .mockResolvedValueOnce(
        jsonResponse({
          candidate_id: "cand_42",
          status: "changes-requested",
          decided_at: "2026-05-17T12:00:00+00:00",
          decided_by_user_id: "usr_admin",
          action: "request-changes",
          notes: "Regenerate",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(candidate()));
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    await screen.findByTestId("adapter-review-request-changes");
    fireEvent.change(screen.getByTestId("adapter-review-notes"), {
      target: { value: "Regenerate" },
    });
    fireEvent.click(screen.getByTestId("adapter-review-request-changes"));

    await waitFor(() => {
      const decisionCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).includes("/decisions"),
      );
      const body = JSON.parse(
        (decisionCall?.[1] as RequestInit).body as string,
      );
      expect(body.action).toBe("request-changes");
      expect(body.notes).toBe("Regenerate");
    });
  });

  it("toggles preview mode between current and diff", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(candidate()),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AdapterReviewDetail candidateId="cand_42" onBack={() => {}} />);

    await screen.findByTestId("adapter-review-mode-current");
    fireEvent.click(screen.getByTestId("adapter-review-mode-current"));
    // The state pane re-renders with the current state body.
    expect(screen.getByTestId("adapter-review-state")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("adapter-review-mode-diff"));
    expect(screen.getByTestId("adapter-review-state")).toBeInTheDocument();
  });
});
