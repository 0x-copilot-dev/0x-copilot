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
          <FleetBotIcon />
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
        <span className="aui-fleet-card__foot-text">
          <FleetStackIcon />
          Subagents run in parallel — keep chatting and they&apos;ll report
          back.
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

/** Small bot/agent glyph for the fleet card's primary icon. Inline SVG
 *  rather than an emoji so it inherits ``currentColor`` and tracks the
 *  ``--color-accent-strong`` set by ``.aui-fleet-card__icon``. */
function FleetBotIcon(): ReactElement {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="10" height="8" rx="2" />
      <path d="M8 3v2" />
      <circle cx="6" cy="9" r="0.7" fill="currentColor" />
      <circle cx="10" cy="9" r="0.7" fill="currentColor" />
      <path d="M6.5 11.5h3" />
    </svg>
  );
}

/** Stack-of-cards glyph next to the footer copy. Hints at the fanout
 *  semantics ("multiple subagents") without leaning on emoji. */
function FleetStackIcon(): ReactElement {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 1.5 11 4 6 6.5 1 4l5-2.5Z" />
      <path d="M1 6.5 6 9l5-2.5" />
      <path d="M1 9 6 11.5 11 9" />
    </svg>
  );
}
