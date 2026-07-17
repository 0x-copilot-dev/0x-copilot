/**
 * PR 4.5 — Workspace tab body of the `UsagePanel`.
 *
 * Loads org usage + budgets and composes the chart with the top-users table.
 * Renders an admin-only empty state when the upstream returns 403, and a
 * loading shimmer otherwise. Pure composition — no business logic.
 */

import { Card } from "@0x-copilot/design-system";
import type { UsagePeriod } from "@0x-copilot/api-types";
import type { ReactElement } from "react";

import type { RequestIdentity } from "../../../../../api/config";
import { ByConnectorTable } from "./UsageConversationView";
import { UsageTopUsersTable } from "./UsageTopUsersTable";
import { UsageWorkspaceChart } from "./UsageWorkspaceChart";
import { useUsageOrg } from "./useUsageOrg";

export interface UsageWorkspaceViewProps {
  identity: RequestIdentity;
  period: UsagePeriod;
}

export function UsageWorkspaceView({
  identity,
  period,
}: UsageWorkspaceViewProps): ReactElement {
  const { loading, forbidden, error, orgUsage, budgets } = useUsageOrg(
    identity,
    period,
  );

  if (forbidden) {
    return (
      <Card tone="muted" className="details-panel__section">
        <h3>Workspace usage is admin-only</h3>
        <p className="details-panel__empty">
          Ask a workspace admin to share the workspace usage with you, or open
          your own usage above.
        </p>
      </Card>
    );
  }

  if (error) {
    return (
      <Card tone="danger" className="details-panel__alert" role="alert">
        {error}
      </Card>
    );
  }

  if (loading && !orgUsage) {
    return (
      <Card tone="muted" className="details-panel__section">
        Loading…
      </Card>
    );
  }

  if (!orgUsage) {
    return (
      <Card tone="muted" className="details-panel__section">
        <p className="details-panel__empty">No workspace usage to show.</p>
      </Card>
    );
  }

  const showCosts =
    orgUsage.total.cost_micro_usd !== null ||
    (orgUsage.by_connector ?? []).some((row) => row.cost_micro_usd !== null);

  return (
    <div className="details-panel__body">
      <UsageWorkspaceChart orgUsage={orgUsage} budgets={budgets} />
      <UsageTopUsersTable orgUsage={orgUsage} />
      <ByConnectorTable
        rows={orgUsage.by_connector ?? []}
        showCosts={showCosts}
      />
      {orgUsage.cold_start_fallback ? (
        <p className="details-panel__footnote">
          Aggregating live data — recent activity may take ~1 minute to appear.
        </p>
      ) : null}
    </div>
  );
}
