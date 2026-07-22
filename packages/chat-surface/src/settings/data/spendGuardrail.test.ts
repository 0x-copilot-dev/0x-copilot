// D4 — the spend-guardrail data seam builds the expected TypedRequests against
// /v1/budgets (hydrate → GET /me; upsert → POST/PATCH/DELETE) and encapsulates
// the dollars↔micro-USD conversion. Proves there is no bare `fetch` and no
// fabricated success.

import { describe, expect, it, vi } from "vitest";

import type { BudgetMeResponse, BudgetMeRow } from "@0x-copilot/api-types";

import type { Transport, TypedRequest } from "../../ports/Transport";
import {
  capUsdToMicro,
  createSpendGuardrailPort,
  microToCapUsd,
} from "./spendGuardrail";

function fakeTransport(handler: (req: TypedRequest) => unknown): {
  readonly transport: Transport;
  readonly calls: TypedRequest[];
} {
  const calls: TypedRequest[] = [];
  const request = (async (req: TypedRequest) => {
    calls.push(req);
    return handler(req);
  }) as Transport["request"];
  const transport: Transport = {
    request,
    subscribeServerSentEvents: vi.fn(() => ({ close: () => undefined })),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
  return { transport, calls };
}

function userMonthRow(overrides: Partial<BudgetMeRow> = {}): BudgetMeRow {
  return {
    id: "bdg_1",
    scope: "user",
    period: "month",
    enforcement: "hard",
    status: "active",
    limit_micro_usd: 50_000_000,
    limit_tokens: null,
    current_micro_usd: 10_000_000,
    current_tokens: 0,
    remaining_micro_usd: 40_000_000,
    remaining_tokens: null,
    period_start: "2026-07-01",
    period_end: "2026-07-31",
    ...overrides,
  };
}

function meResponse(rows: BudgetMeRow[]): BudgetMeResponse {
  return { currency: "USD", budgets: rows };
}

describe("dollars↔micro conversion", () => {
  it("rounds and clamps at the boundary", () => {
    expect(capUsdToMicro(50)).toBe(50_000_000);
    expect(capUsdToMicro(49.99)).toBe(49_990_000);
    expect(capUsdToMicro(-5)).toBe(0);
    expect(microToCapUsd(49_990_000)).toBe(49.99);
  });
});

describe("createSpendGuardrailPort.read", () => {
  it("hydrates the user/month row and converts micro→dollars", async () => {
    const { transport, calls } = fakeTransport(() =>
      meResponse([userMonthRow()]),
    );
    const snap = await createSpendGuardrailPort(transport).read();
    expect(calls[0]).toMatchObject({ method: "GET", path: "/v1/budgets/me" });
    expect(snap).toEqual({
      monthlyCapUsd: 50,
      pauseAtCap: true,
      budgetId: "bdg_1",
      remainingUsd: 40,
    });
  });

  it("returns the no-cap snapshot when there is no matching row", async () => {
    // An org-scoped row must be ignored — only user/month is the cap.
    const { transport } = fakeTransport(() =>
      meResponse([userMonthRow({ scope: "org", period: "month" })]),
    );
    const snap = await createSpendGuardrailPort(transport).read();
    expect(snap).toEqual({
      monthlyCapUsd: null,
      pauseAtCap: false,
      budgetId: null,
      remainingUsd: null,
    });
  });

  it("reflects soft enforcement as pauseAtCap=false", async () => {
    const { transport } = fakeTransport(() =>
      meResponse([userMonthRow({ enforcement: "soft" })]),
    );
    const snap = await createSpendGuardrailPort(transport).read();
    expect(snap.pauseAtCap).toBe(false);
  });
});

describe("createSpendGuardrailPort.save", () => {
  it("POSTs a new user/month cap when none exists (user_id omitted)", async () => {
    const { transport, calls } = fakeTransport((req) => {
      if (req.method === "GET") return meResponse([]);
      return userMonthRow();
    });
    await createSpendGuardrailPort(transport).save({
      monthlyCapUsd: 50,
      pauseAtCap: true,
    });
    const post = calls.find((c) => c.method === "POST");
    expect(post).toMatchObject({
      method: "POST",
      path: "/v1/budgets",
      body: {
        scope: "user",
        period: "month",
        enforcement: "hard",
        limit_micro_usd: 50_000_000,
      },
    });
    // The server derives the caller — the body must NOT carry a user_id.
    expect((post?.body as Record<string, unknown>).user_id).toBeUndefined();
  });

  it("PATCHes the existing row in place (never a blind POST)", async () => {
    const { transport, calls } = fakeTransport((req) => {
      if (req.method === "GET") return meResponse([userMonthRow()]);
      return userMonthRow();
    });
    await createSpendGuardrailPort(transport).save({
      monthlyCapUsd: 49.99,
      pauseAtCap: false,
    });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch).toMatchObject({
      method: "PATCH",
      path: "/v1/budgets/bdg_1",
      body: { limit_micro_usd: 49_990_000, enforcement: "soft" },
    });
  });

  it("DELETEs the row when the cap is cleared", async () => {
    const { transport, calls } = fakeTransport((req) => {
      if (req.method === "GET") return meResponse([userMonthRow()]);
      return { status: "deleted" };
    });
    await createSpendGuardrailPort(transport).save({
      monthlyCapUsd: null,
      pauseAtCap: false,
    });
    expect(calls.find((c) => c.method === "DELETE")).toMatchObject({
      method: "DELETE",
      path: "/v1/budgets/bdg_1",
    });
  });

  it("clearing an already-absent cap is a no-op (no DELETE)", async () => {
    const { transport, calls } = fakeTransport(() => meResponse([]));
    await createSpendGuardrailPort(transport).save({
      monthlyCapUsd: null,
      pauseAtCap: false,
    });
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });
});
