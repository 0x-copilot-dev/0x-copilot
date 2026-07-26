// PR A2 / F1 — parallel-batch subagent fleet card.
//
// Renders when the orchestrator emits `subagent_fleet_started` —
// children that carry `parent_fleet_id` matching this fleet's id are
// nested inside the card. The card head shows running / total counts
// and an explicit disclosure for the child rows. Fleet finish
// (`subagent_fleet_finished`) flips the head from running to done, records
// the elapsed total, and folds the card back to its compact summary.
//
// Reuses the existing `<SubagentActivityList>` row primitive for the
// per-child layout so progress, status, and findings render the same
// as a non-fleet subagent does. The grouping is *only* the head + the
// indent / count badge — children are not re-implemented.

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  ACTIVITY_CARD_INTERACTION_CSS,
  activityCardChevronStyle,
  activityCardDetailStyle,
  activityCardFrameStyle,
  activityCardHeaderStyle,
  activityCardMetaStyle,
  activityCardTileStyle,
  activityCardTitleStyle,
} from "../activity/ActivityCardChrome";

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
  /** Terminal children that did not complete successfully. */
  failed?: number;
  /** Wall-clock elapsed once the fleet finishes; null while still running. */
  elapsed?: string | null;
  /** Renders the host's existing per-subagent rows for the fleet's children. */
  children?: ReactNode;
  /** Opens the host's Agents view; shown only with the expanded details. */
  onOpenWorkspace?: () => void;
}

export function SubagentFleetCard({
  fleetId,
  title,
  sub,
  total,
  running,
  done,
  failed = 0,
  elapsed,
  children,
  onOpenWorkspace,
}: SubagentFleetCardProps): ReactElement {
  const solo = total === 1;
  const terminal = running === 0;
  const [expanded, setExpanded] = useState(!terminal);
  const wasRunning = useRef(!terminal);
  const detailId = useId();

  // While work is in flight the rows are open by default. Once the fleet
  // becomes terminal, fold it exactly once so completed fan-out does not
  // dominate the transcript. A user's later manual choice is preserved.
  useEffect(() => {
    if (wasRunning.current && terminal) {
      setExpanded(false);
    }
    wasRunning.current = !terminal;
  }, [terminal]);

  const headStatus = fleetMeta({ done, elapsed, failed, running, total });
  const fleetStatus = running > 0 ? "running" : failed > 0 ? "error" : "done";
  const displayTitle =
    total > 0
      ? solo
        ? "Dispatched a subagent"
        : `Dispatched ${total} subagents in parallel`
      : title;
  return (
    <section
      className="aui-fleet-card tc-activity-card"
      style={activityCardFrameStyle}
      data-fleet-id={fleetId}
      data-status={fleetStatus}
      data-expanded={expanded ? "true" : "false"}
    >
      <style>{ACTIVITY_CARD_INTERACTION_CSS}</style>
      <button
        type="button"
        className="tc-activity-card__head"
        style={activityCardHeaderStyle}
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${displayTitle}, ${headStatus}. ${
          expanded ? "Hide" : "Show"
        } subagent details`}
        onClick={() => setExpanded((value) => !value)}
        data-testid={`subagent-fleet-toggle-${fleetId}`}
      >
        <span style={activityCardTileStyle} aria-hidden="true">
          <FleetBotIcon />
        </span>
        <span
          style={{ ...activityCardTitleStyle, flex: "1 1 auto", minWidth: 0 }}
        >
          {displayTitle}
        </span>
        <span
          style={{
            ...activityCardMetaStyle,
            flex: "0 1 auto",
            maxWidth: "42%",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {headStatus}
        </span>
        <span
          className="tc-activity-card__chevron"
          style={activityCardChevronStyle}
          aria-hidden="true"
        >
          ▾
        </span>
      </button>
      <div
        id={detailId}
        className="aui-fleet-card__details"
        style={{
          ...activityCardDetailStyle,
          display: expanded ? "grid" : "none",
          gap: 8,
          maxBlockSize: "min(20rem, 45vh)",
          overflowY: "auto",
        }}
        hidden={!expanded}
      >
        {sub ? (
          <p
            style={{
              ...activityCardMetaStyle,
              margin: 0,
              whiteSpace: "normal",
            }}
          >
            {sub}
          </p>
        ) : null}
        {children ? (
          <div style={{ display: "grid", gap: 8 }}>{children}</div>
        ) : null}
        {onOpenWorkspace ? (
          <button
            type="button"
            className="aui-fleet-card__link"
            onClick={onOpenWorkspace}
          >
            View in Agents
          </button>
        ) : null}
      </div>
    </section>
  );
}

function fleetMeta({
  done,
  elapsed,
  failed,
  running,
  total,
}: {
  readonly done: number;
  readonly elapsed: string | null | undefined;
  readonly failed: number;
  readonly running: number;
  readonly total: number;
}): string {
  if (running > 0) {
    if (done > 0 || failed > 0) {
      const completed = [`${done}/${total} done`];
      if (failed > 0) completed.push(`${failed} failed`);
      return completed.join(" · ");
    }
    return `${running} running`;
  }

  const terminal = [`${done}/${total} done`];
  if (failed > 0) terminal.push(`${failed} failed`);
  if (failed === 0 && elapsed) terminal.push(elapsed);
  return terminal.join(" · ");
}

/** Small bot/agent glyph for the fleet card's primary icon. Inline SVG
 *  rather than an emoji so it inherits ``currentColor`` and tracks the
 *  ``--color-accent-strong`` set by ``.aui-fleet-card__icon``. */
function FleetBotIcon(): ReactElement {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
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
