// Spend-guardrail data seam (DESIGN-SPEC §4 Spend guardrail · PRD PR-5.18 /
// D4). The Model & behavior "Spend guardrail" card (monthly API cap + pause-at-
// cap) is bound to the B7 budget engine at `/v1/budgets` — a REAL persisted
// per-user monthly cap that the runtime enforces before each run (hard cap →
// rejected, soft cap → warned). No fake success: a failed save rejects.
//
// The card maps to exactly ONE budget: the caller's `scope=user`, `period=month`
// row. `pauseAtCap` is the enforcement mode (hard = pause runs at cap; soft =
// warn but proceed). The dollars↔micro-USD conversion is encapsulated HERE so
// neither host duplicates it.
//
// The page depends on this PORT, not on `Transport` directly, so budget CRUD is
// a host concern the substrate injects (trivially testable with a mock port).
// `createSpendGuardrailPort(transport)` is the default Transport-backed adapter;
// it is fully self-contained — `save` reads the caller's current cap to decide
// POST (create) / PATCH (update) / DELETE (clear), and the server derives the
// caller's identity from the bearer (a `scope=user` create omits `user_id`).
//
// Substrate-agnostic: no bare `fetch` / `window` — the adapter only builds
// `TypedRequest`s and calls the injected `Transport.request()`.
//
// Facade routes (user bearer, RBAC scope RUNTIME_USE):
//
//   GET    /v1/budgets/me         → BudgetMeResponse (caller-scoped)
//   POST   /v1/budgets            → BudgetView       (self user-scoped allowed)
//   PATCH  /v1/budgets/{id}       → BudgetView
//   DELETE /v1/budgets/{id}       → { status: "deleted" }

import type {
  BudgetCreateRequest,
  BudgetMeResponse,
  BudgetUpdateRequest,
  BudgetView,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";
import type { SpendGuardrailValue } from "../ModelBehaviorPage";

/** Micro-USD per US dollar — the budget engine stores integer micro-USD. */
const MICRO_PER_USD = 1_000_000;

/**
 * The hydrated spend-guardrail state. `monthlyCapUsd === null` means "no cap".
 * `budgetId` / `remainingUsd` are extra context the page may surface (the
 * remaining-headroom hint); `budgetId` is the row `save` mutates in place.
 */
export interface SpendGuardrailSnapshot {
  readonly monthlyCapUsd: number | null;
  readonly pauseAtCap: boolean;
  readonly budgetId: string | null;
  readonly remainingUsd: number | null;
}

export interface SpendGuardrailPort {
  /** `GET /v1/budgets/me` → the caller's monthly user cap (or "no cap"). */
  read(signal?: AbortSignal): Promise<SpendGuardrailSnapshot>;
  /**
   * Upsert the caller's monthly user cap. Cap cleared → DELETE the row; cap set
   * with no row → POST; cap set with an existing row → PATCH. Rejects on
   * failure (the host surfaces an honest error — never a fabricated success).
   */
  save(next: SpendGuardrailValue, signal?: AbortSignal): Promise<void>;
}

/** Whole dollars → integer micro-USD (rounded, clamped ≥ 0). */
export function capUsdToMicro(capUsd: number): number {
  return Math.max(0, Math.round(capUsd * MICRO_PER_USD));
}

/** Integer micro-USD → whole-dollar number (for display / hydration). */
export function microToCapUsd(micro: number): number {
  return micro / MICRO_PER_USD;
}

/** Enforcement mode for a given pause-at-cap intent. */
function enforcementFor(pauseAtCap: boolean): "hard" | "soft" {
  return pauseAtCap ? "hard" : "soft";
}

/**
 * The caller's monthly user-scoped budget row, if any. The unique constraint
 * (org + user + scope + period) guarantees at most one.
 */
function pickMonthlyUserRow(
  response: BudgetMeResponse,
): BudgetMeResponse["budgets"][number] | null {
  return (
    response.budgets.find(
      (row) => row.scope === "user" && row.period === "month",
    ) ?? null
  );
}

/**
 * Default `SpendGuardrailPort` backed by the injected `Transport`. Encapsulates
 * the `/v1/budgets` upsert logic + the dollars↔micro conversion so both hosts
 * bind the identical behaviour.
 */
export function createSpendGuardrailPort(
  transport: Transport,
): SpendGuardrailPort {
  async function readRow(
    signal?: AbortSignal,
  ): Promise<BudgetMeResponse["budgets"][number] | null> {
    const response = await transport.request<BudgetMeResponse>({
      method: "GET",
      path: "/v1/budgets/me",
      signal,
    });
    return pickMonthlyUserRow(response);
  }

  return {
    async read(signal) {
      const row = await readRow(signal);
      if (row === null) {
        return {
          monthlyCapUsd: null,
          pauseAtCap: false,
          budgetId: null,
          remainingUsd: null,
        };
      }
      return {
        monthlyCapUsd:
          row.limit_micro_usd !== null
            ? microToCapUsd(row.limit_micro_usd)
            : null,
        pauseAtCap: row.enforcement === "hard",
        budgetId: row.id,
        remainingUsd:
          row.remaining_micro_usd !== null
            ? microToCapUsd(row.remaining_micro_usd)
            : null,
      };
    },

    async save(next, signal) {
      const existing = await readRow(signal);
      const enforcement = enforcementFor(next.pauseAtCap);

      // Cap cleared → remove the row (idempotent when none exists).
      if (next.monthlyCapUsd === null) {
        if (existing !== null) {
          await transport.request<{ status: string }>({
            method: "DELETE",
            path: `/v1/budgets/${encodeURIComponent(existing.id)}`,
            signal,
          });
        }
        return;
      }

      const limit_micro_usd = capUsdToMicro(next.monthlyCapUsd);

      // Existing row → PATCH the cap + enforcement in place (never a blind
      // POST, which would 409 on the unique scope+period constraint).
      if (existing !== null) {
        const body: BudgetUpdateRequest = { limit_micro_usd, enforcement };
        await transport.request<BudgetView>({
          method: "PATCH",
          path: `/v1/budgets/${encodeURIComponent(existing.id)}`,
          body,
          signal,
        });
        return;
      }

      // No row → create the caller's monthly user cap. `user_id` is omitted;
      // the server defaults it to the authenticated caller.
      const body: BudgetCreateRequest = {
        scope: "user",
        period: "month",
        enforcement,
        limit_micro_usd,
      };
      await transport.request<BudgetView>({
        method: "POST",
        path: "/v1/budgets",
        body,
        signal,
      });
    },
  };
}
