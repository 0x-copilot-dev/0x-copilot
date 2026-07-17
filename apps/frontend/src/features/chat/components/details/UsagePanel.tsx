/**
 * `/usage` slash-command panel — host for the two views described by the
 * Atlas design doc (PR 4.5):
 *
 *   - **This conversation** (default) — token breakdown by message, by model,
 *     and top conversations. Lives in `<UsageConversationView>`.
 *   - **Workspace** — past 30 days of usage as a stacked area chart by user,
 *     with seat count and plan-limit overlay. Lives in `<UsageWorkspaceView>`.
 *
 * The host owns the period selector and the tab switch only. Each view loads
 * its own data on mount and refetches when `period` changes — UsagePanel
 * never knows what's inside.
 *
 * Cost columns auto-hide inside each view when every row has
 * `cost_micro_usd === null` — single-tenant deploys without seeded pricing
 * see token-only rows with no missing-data noise.
 */

import { Button, Card } from "@0x-copilot/design-system";
import type { UsagePeriod } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { useState } from "react";

import type { RequestIdentity } from "../../../../api/config";
import { UsageConversationView } from "./usage/UsageConversationView";
import { UsageWorkspaceView } from "./usage/UsageWorkspaceView";

const _PERIODS: ReadonlyArray<{ id: UsagePeriod; label: string }> = [
  { id: "today", label: "Today" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "month", label: "This month" },
];

type UsageTab = "conversation" | "workspace";

const _TABS: ReadonlyArray<{ id: UsageTab; label: string }> = [
  { id: "conversation", label: "This conversation" },
  { id: "workspace", label: "Workspace" },
];

export interface UsagePanelProps {
  identity: RequestIdentity;
  onClose: () => void;
}

export function UsagePanel({
  identity,
  onClose,
}: UsagePanelProps): ReactElement {
  const [period, setPeriod] = useState<UsagePeriod>("30d");
  const [tab, setTab] = useState<UsageTab>("conversation");

  return (
    <aside className="details-panel" data-testid="usage-panel">
      <header className="details-panel__header">
        <div>
          <h2>Usage</h2>
          <p className="details-panel__subtitle">
            Tokens and cost across this conversation and the workspace.
          </p>
        </div>
        <div className="details-panel__header-actions">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close usage panel"
          >
            ✕
          </Button>
        </div>
      </header>

      <div
        className="details-panel__tab-switcher"
        role="tablist"
        aria-label="Usage view"
      >
        {_TABS.map((entry) => (
          <Button
            key={entry.id}
            type="button"
            variant={entry.id === tab ? "primary" : "ghost"}
            size="sm"
            role="tab"
            aria-selected={entry.id === tab}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      <div className="details-panel__period-switcher" role="tablist">
        {_PERIODS.map((entry) => (
          <Button
            key={entry.id}
            type="button"
            variant={entry.id === period ? "primary" : "ghost"}
            size="sm"
            role="tab"
            aria-selected={entry.id === period}
            onClick={() => setPeriod(entry.id)}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      {tab === "conversation" ? (
        <UsageConversationView identity={identity} period={period} />
      ) : (
        <UsageWorkspaceView identity={identity} period={period} />
      )}

      {tab === "workspace" ? null : null}

      {/* Defensive guard — both views render their own loading/error/empty states. */}
      <noscript>
        <Card tone="muted" className="details-panel__section">
          JavaScript is required to view usage.
        </Card>
      </noscript>
    </aside>
  );
}
