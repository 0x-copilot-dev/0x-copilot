/**
 * PR 4.5 — Top users table for the workspace usage view.
 *
 * Sortable by tokens or cost; folds the long tail into a single "Other" row
 * when there are more users than the chart's stack budget. Reuses the existing
 * `details-panel__table` styles — no new CSS required.
 *
 * Cost column auto-hides when every row has `cost_micro_usd === null`,
 * matching the same behaviour as the per-conversation table.
 */

import { Badge, Card } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";

import { formatMicroUsd } from "../../../utils/formatMicroUsd";
import { formatTokens } from "./format";
import {
  pickTopUsers,
  type UsageRankBy,
  type UsageUserRow,
} from "./usageWorkspaceData";
import type { UsageOrgResponse } from "@0x-copilot/api-types";

export interface UsageTopUsersTableProps {
  orgUsage: UsageOrgResponse;
  /** Defaults to 25 — covers most workspaces; chart uses 6 for visual reasons. */
  limit?: number;
}

export function UsageTopUsersTable({
  orgUsage,
  limit = 25,
}: UsageTopUsersTableProps): ReactElement | null {
  const [rankBy, setRankBy] = useState<UsageRankBy | null>(null);

  const result = useMemo(
    () => pickTopUsers({ orgUsage, limit, rankBy: rankBy ?? undefined }),
    [orgUsage, limit, rankBy],
  );

  if (result.top.length === 0) {
    return null;
  }

  const showCosts =
    result.top.some((row) => row.cost_micro_usd !== null) ||
    (result.other?.cost_micro_usd ?? null) !== null;

  // Default sort header derives from the resolved rankBy so users see what
  // the table actually ordered by; clicking a header switches.
  const activeRankBy: UsageRankBy = rankBy ?? result.rankBy;

  return (
    <Card tone="muted" className="details-panel__section">
      <h3>Top users</h3>
      <table className="details-panel__table">
        <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Runs</th>
            <SortHeader
              label="Total tokens"
              active={activeRankBy === "tokens"}
              onClick={() => setRankBy("tokens")}
            />
            {showCosts ? (
              <SortHeader
                label="Cost"
                active={activeRankBy === "cost"}
                onClick={() => setRankBy("cost")}
              />
            ) : null}
          </tr>
        </thead>
        <tbody>
          {result.top.map((row) => (
            <UserRow key={row.user_id} row={row} showCosts={showCosts} />
          ))}
          {result.other ? (
            <UserRow row={result.other} showCosts={showCosts} variant="other" />
          ) : null}
        </tbody>
      </table>
    </Card>
  );
}

function SortHeader({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}): ReactElement {
  return (
    <th scope="col" aria-sort={active ? "descending" : "none"}>
      <button
        type="button"
        className="details-panel__sort"
        onClick={onClick}
        aria-pressed={active}
      >
        {label}
        {active ? " ↓" : ""}
      </button>
    </th>
  );
}

function UserRow({
  row,
  showCosts,
  variant,
}: {
  row: UsageUserRow;
  showCosts: boolean;
  variant?: "other";
}): ReactElement {
  const label = row.display_name ?? row.user_id;
  return (
    <tr data-variant={variant}>
      <td>
        {label}
        {variant === "other" ? (
          <Badge tone="neutral" className="details-panel__badge">
            tail
          </Badge>
        ) : null}
      </td>
      <td>{row.runs_count}</td>
      <td>{formatTokens(row.total)}</td>
      {showCosts ? <td>{formatMicroUsd(row.cost_micro_usd)}</td> : null}
    </tr>
  );
}
