/**
 * PR 4.5 — Conversation tab body of the `UsagePanel`.
 *
 * Extracted verbatim from the legacy `UsagePanel`: totals card + by-model
 * table + top-conversations table. Cost columns auto-hide when every row
 * has `cost_micro_usd === null`.
 */

import { Badge, Card, classNames } from "@enterprise-search/design-system";
import type {
  UsageConversationRow,
  UsageMeResponse,
  UsagePeriod,
} from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import { getMyTopConversations, getMyUsage } from "../../../../../api/agentApi";
import type { RequestIdentity } from "../../../../../api/config";
import { formatMicroUsd } from "../../../utils/formatMicroUsd";

export interface UsageConversationViewProps {
  identity: RequestIdentity;
  period: UsagePeriod;
}

export function UsageConversationView({
  identity,
  period,
}: UsageConversationViewProps): ReactElement {
  const [usage, setUsage] = useState<UsageMeResponse | null>(null);
  const [topConversations, setTopConversations] = useState<
    UsageConversationRow[] | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    async (signal: AbortSignal): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const [usageResponse, top] = await Promise.all([
          getMyUsage(period, identity),
          getMyTopConversations(period, identity, 10),
        ]);
        if (signal.aborted) return;
        setUsage(usageResponse);
        setTopConversations(top);
      } catch (err) {
        if (signal.aborted) return;
        setError(err instanceof Error ? err.message : "could not load usage");
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [identity, period],
  );

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);

  const showCosts =
    usage !== null &&
    (usage.total.cost_micro_usd !== null ||
      usage.by_model.some((row) => row.cost_micro_usd !== null) ||
      (topConversations ?? []).some((row) => row.cost_micro_usd !== null));

  if (error) {
    return (
      <Card tone="danger" className="details-panel__alert" role="alert">
        {error}
      </Card>
    );
  }

  if (loading && !usage) {
    return (
      <Card tone="muted" className="details-panel__section">
        Loading…
      </Card>
    );
  }

  if (!usage) {
    return (
      <Card tone="muted" className="details-panel__section">
        <p className="details-panel__empty">No usage to show.</p>
      </Card>
    );
  }

  return (
    <div className="details-panel__body">
      <UsageTotalsCard usage={usage} showCosts={showCosts} />
      <ByModelTable usage={usage} showCosts={showCosts} />
      <TopConversationsTable
        rows={topConversations ?? []}
        showCosts={showCosts}
      />
      {usage.cold_start_fallback ? (
        <p className="details-panel__footnote">
          Aggregating live data — recent activity may take ~1 minute to appear.
        </p>
      ) : null}
    </div>
  );
}

function UsageTotalsCard({
  usage,
  showCosts,
}: {
  usage: UsageMeResponse;
  showCosts: boolean;
}): ReactElement {
  const isEmpty = usage.total.runs_count === 0;
  return (
    <Card tone="default" className="details-panel__section">
      <div className="details-panel__row">
        <strong>{usage.total.runs_count} runs</strong>
        {showCosts ? (
          <Badge tone="accent" className="details-panel__badge">
            {formatMicroUsd(usage.total.cost_micro_usd)} {usage.currency}
          </Badge>
        ) : null}
      </div>
      {isEmpty ? (
        <p className="details-panel__empty">No usage in this period.</p>
      ) : (
        <dl className="details-panel__metrics">
          <Metric label="Input" value={usage.total.input} />
          <Metric label="Cached input" value={usage.total.cached_input} />
          <Metric label="Output" value={usage.total.output} />
          <Metric label="Total" value={usage.total.total} />
        </dl>
      )}
    </Card>
  );
}

function ByModelTable({
  usage,
  showCosts,
}: {
  usage: UsageMeResponse;
  showCosts: boolean;
}): ReactElement | null {
  if (usage.by_model.length === 0) return null;
  return (
    <Card tone="muted" className="details-panel__section">
      <h3>By model</h3>
      <table className="details-panel__table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Runs</th>
            <th scope="col">Input</th>
            <th scope="col">Output</th>
            {showCosts ? <th scope="col">Cost</th> : null}
          </tr>
        </thead>
        <tbody>
          {usage.by_model.map((row) => (
            <tr key={`${row.provider}-${row.model}`}>
              <td>
                <span>{row.model}</span>{" "}
                <Badge tone="neutral" className="details-panel__badge">
                  {row.provider}
                </Badge>
              </td>
              <td>{row.runs_count}</td>
              <td>{formatTokens(row.input)}</td>
              <td>{formatTokens(row.output)}</td>
              {showCosts ? <td>{formatMicroUsd(row.cost_micro_usd)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function TopConversationsTable({
  rows,
  showCosts,
}: {
  rows: UsageConversationRow[];
  showCosts: boolean;
}): ReactElement | null {
  if (rows.length === 0) return null;
  return (
    <Card tone="muted" className="details-panel__section">
      <h3>Top conversations</h3>
      <table className="details-panel__table">
        <thead>
          <tr>
            <th scope="col">Conversation</th>
            <th scope="col">Runs</th>
            <th scope="col">Total tokens</th>
            {showCosts ? <th scope="col">Cost</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.conversation_id}
              className={classNames(
                row.cost_micro_usd === null && "details-panel__row--no-cost",
              )}
            >
              <td>{row.title ?? row.conversation_id}</td>
              <td>{row.runs_count}</td>
              <td>{formatTokens(row.total)}</td>
              {showCosts ? <td>{formatMicroUsd(row.cost_micro_usd)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: number;
}): ReactElement {
  return (
    <div className="details-panel__metric">
      <dt>{label}</dt>
      <dd>{formatTokens(value)}</dd>
    </div>
  );
}

function formatTokens(value: number): string {
  return `${value.toLocaleString()} tok`;
}
