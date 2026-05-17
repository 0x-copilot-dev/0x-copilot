import { afterEach, describe, expect, it, vi } from "vitest";

import {
  decideAdapterReviewCandidate,
  getAdapterReviewCandidate,
  listAdapterReviewCandidates,
} from "./adapterReviewApi";

const IDENTITY = { orgId: "org_atlas", userId: "usr_admin" };

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("listAdapterReviewCandidates", () => {
  it("sends allowlisted filters + identity to the facade", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidates: [],
          next_cursor: null,
          has_more: false,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await listAdapterReviewCandidates(IDENTITY, {
      status: "submitted",
      layout: "form",
      scheme: "atlas://hubspot",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/admin/adapter_registry/candidates");
    expect(url).toContain("org_id=org_atlas");
    expect(url).toContain("user_id=usr_admin");
    expect(url).toContain("status=submitted");
    expect(url).toContain("layout=form");
    expect(url).toContain("scheme=atlas%3A%2F%2Fhubspot");
    expect(url).toContain("limit=25");
  });
});

describe("getAdapterReviewCandidate", () => {
  it("requests the detail route by id", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidate_id: "cand_1",
          scheme: "atlas://linear",
          layout_template: "table",
          origin_tenant_redacted: "tenant_abc",
          generator_model: "model",
          submitted_at: "2026-05-01T00:00:00+00:00",
          status: "in-review",
          candidate_source: "/* anonymized */",
          schema_version: 1,
          history: [],
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await getAdapterReviewCandidate("cand_1");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/v1/admin/adapter_registry/candidates/cand_1");
    expect(detail.candidate_id).toBe("cand_1");
  });

  it("encodes path components", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse({
          candidate_id: "cand/with-slash",
          scheme: "atlas://x",
          layout_template: "form",
          origin_tenant_redacted: "tenant_abc",
          generator_model: "model",
          submitted_at: "2026-05-01T00:00:00+00:00",
          status: "submitted",
          candidate_source: "",
          schema_version: 1,
          history: [],
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getAdapterReviewCandidate("cand/with-slash");

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("cand%2Fwith-slash");
  });
});

describe("decideAdapterReviewCandidate", () => {
  it("POSTs the decision body to the decisions route", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        expect(init?.method).toBe("POST");
        expect(init?.body).toBeDefined();
        return jsonResponse({
          candidate_id: "cand_2",
          status: "approved",
          decided_at: "2026-05-17T00:00:00+00:00",
          decided_by_user_id: "usr_admin",
          action: "approve",
          notes: "ok",
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await decideAdapterReviewCandidate("cand_2", {
      action: "approve",
      notes: "ok",
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain(
      "/v1/admin/adapter_registry/candidates/cand_2/decisions",
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      action: "approve",
      notes: "ok",
    });
    expect(response.status).toBe("approved");
  });
});
