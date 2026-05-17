/**
 * PR 4.5 — Hook for the workspace usage view.
 *
 * Loads `/v1/usage/org` and `/v1/budgets/me` in parallel for the selected
 * period. Exposes a `forbidden` flag so the UI can render the admin-only
 * empty state without surfacing a generic error message.
 *
 * No caching layer — this view is opened on demand and the response is small.
 * Refetches when `period` or `identity` changes.
 */

import type {
  BudgetMeResponse,
  UsageOrgResponse,
  UsagePeriod,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useState } from "react";

import { getMyBudgets, getOrgUsage } from "../../../../../api/agentApi";
import type { RequestIdentity } from "../../../../../api/config";
import { errorMessage } from "../../../../../utils/errors";

export interface UseUsageOrgState {
  loading: boolean;
  forbidden: boolean;
  error: string | null;
  orgUsage: UsageOrgResponse | null;
  budgets: BudgetMeResponse | null;
  reload: () => void;
}

/**
 * The facade returns an HTTP response whose `Response` object surfaces the
 * status. Our `httpGet` throws an `Error` whose message carries the upstream
 * status text — `"403 Forbidden"` is the marker we test for. If that contract
 * ever changes, only this one site moves.
 */
function isForbiddenError(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }
  return /\b403\b|\bforbidden\b/i.test(err.message);
}

export function useUsageOrg(
  identity: RequestIdentity,
  period: UsagePeriod,
): UseUsageOrgState {
  const [orgUsage, setOrgUsage] = useState<UsageOrgResponse | null>(null);
  const [budgets, setBudgets] = useState<BudgetMeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [tick, setTick] = useState(0);

  const reload = useCallback((): void => {
    setTick((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setLoading(true);
    setError(null);
    setForbidden(false);
    void Promise.all([
      getOrgUsage(period, identity).catch((err: unknown) => {
        if (isForbiddenError(err)) {
          if (!cancelled) {
            setForbidden(true);
          }
          return null;
        }
        throw err;
      }),
      getMyBudgets(identity).catch(() => null),
    ])
      .then(([usage, budget]) => {
        if (cancelled || controller.signal.aborted) return;
        setOrgUsage(usage ?? null);
        setBudgets(budget);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errorMessage(err, "could not load usage"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [identity, period, tick]);

  return { loading, forbidden, error, orgUsage, budgets, reload };
}
