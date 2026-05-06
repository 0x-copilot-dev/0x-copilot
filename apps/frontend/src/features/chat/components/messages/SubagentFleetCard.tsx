// PR A2 / F1 — parallel-batch subagent fleet card.
//
// Renders when the orchestrator emits `subagent_fleet_started` —
// children that carry `parent_fleet_id` matching this fleet's id are
// nested inside the card. The card head shows running / total counts
// and a "View in workspace →" link that opens the Agents tab. Fleet
// finish (`subagent_fleet_finished`) flips the head from running to
// done and records the elapsed total.
//
// Reuses the existing `<SubagentActivityList>` row primitive for the
// per-child layout so progress, status, and findings render the same
// as a non-fleet subagent does. The grouping is *only* the head + the
// indent / count badge — children are not re-implemented.

import type { ReactElement, ReactNode } from "react";

export interface SubagentFleetCardProps {
  fleetId: string;
  title: string;
  sub?: string | null;
  /** Total agent count (= length of `agent_ids` from the started event). */
  total: number;
  /** Children currently running (derived from child events). */
  running: number;
  /** Children currently completed. */
  done: number;
  /** Wall-clock elapsed once the fleet finishes; null while still running. */
  elapsed?: string | null;
  /** Renders the host's existing per-subagent rows for the fleet's children. */
  children?: ReactNode;
  /** Opens the workspace pane Agents tab. */
  onOpenWorkspace?: () => void;
}

export function SubagentFleetCard({
  fleetId,
  title,
  sub,
  total,
  running,
  done,
  elapsed,
  children,
  onOpenWorkspace,
}: SubagentFleetCardProps): ReactElement {
  const headStatus =
    running > 0
      ? `${running} running · ${done}/${total} done`
      : `${done}/${total} done`;
  const displayTitle =
    total > 0 ? `Dispatched ${total} subagents in parallel` : title;
  return (
    <section
      className="aui-fleet-card"
      data-fleet-id={fleetId}
      data-status={running > 0 ? "running" : "done"}
    >
      <header className="aui-fleet-card__head">
        <span className="aui-fleet-card__icon" aria-hidden="true">
          ⌘
        </span>
        <span className="aui-fleet-card__title">{displayTitle}</span>
        <span className="aui-fleet-card__count">{headStatus}</span>
      </header>
      <p className="aui-fleet-card__sub">
        {sub ??
          "They'll keep working while we draft. Live status in the Agents tab."}
      </p>
      {children ? <div className="aui-fleet-card__rows">{children}</div> : null}
      <footer className="aui-fleet-card__foot">
        <span>
          Subagents run in parallel — keep chatting and they'll report back.
        </span>
        {onOpenWorkspace ? (
          <button
            type="button"
            className="aui-fleet-card__link"
            onClick={onOpenWorkspace}
          >
            View in workspace →
          </button>
        ) : null}
        {elapsed ? (
          <span className="aui-fleet-card__elapsed">{elapsed}</span>
        ) : null}
      </footer>
    </section>
  );
}
