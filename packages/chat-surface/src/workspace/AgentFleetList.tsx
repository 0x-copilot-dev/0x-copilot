// AgentFleetList — the Agents rail's fleet view (PRD-E2 / FR-E6). 🎨
//
// This run plus the OTHER runs (running or with held work), each a row with its
// status pill, conversation title, and a "N waiting" count when it has pending
// items in the one Approvals queue. Held work from ANY agent lands in that one
// queue — the static footer note says so (FR-E6). Clicking a row opens the
// owning run (host-routed). Pure presentational: the host threads the merged
// `agents` and the `onOpenRun` router.
//
// A `scheduledSlot` reserves the scheduled-agents section: a routines scheduler
// exists server-side but is NOT a pending-work source, so E2 folds nothing there
// and hosts pass nothing — no section renders (never a stub list). Wiring a
// scheduled feed is out of scope.
//
// Kit-only styling: `StatusPill` (status), `.ui-pill` (waiting count),
// `.ui-eyebrow` ("This run"). Titles are UNTRUSTED — rendered as text nodes only.
//
// Boundary: framework-agnostic — no bare window/document/fetch; tokens only.

import type { PendingAgentRow } from "@0x-copilot/api-types";
import type { CSSProperties, ReactElement, ReactNode } from "react";

import { StatusPill } from "../shell/StatusPill";
import { statusTone } from "../shell/statusTone";

export interface AgentFleetListProps {
  readonly agents: readonly PendingAgentRow[];
  /** The run currently open in the cockpit — marked "This run". */
  readonly currentRunId: string | null;
  readonly onOpenRun: (agent: PendingAgentRow) => void;
  /** Reserved slot for a future scheduled-agents section (unused in E2). */
  readonly scheduledSlot?: ReactNode;
}

const FOOTER_NOTE = "Held work from any agent lands in Approvals.";
const UNTITLED = "Untitled run";

export function AgentFleetList({
  agents,
  currentRunId,
  onOpenRun,
  scheduledSlot,
}: AgentFleetListProps): ReactElement {
  return (
    <div data-testid="agent-fleet-list" style={wrapStyle}>
      {agents.length === 0 ? (
        <div data-testid="agent-fleet-empty" style={emptyStyle}>
          No other agents are running.
        </div>
      ) : (
        <ul aria-label="Agent fleet" style={listStyle}>
          {agents.map((agent) => {
            const presentation = statusTone(agent.run_status);
            const isCurrent = agent.run_id === currentRunId;
            return (
              <li key={agent.run_id} style={rowStyle}>
                <button
                  type="button"
                  className="ui-button"
                  data-testid="agent-fleet-row"
                  data-current={isCurrent ? "true" : "false"}
                  onClick={() => onOpenRun(agent)}
                  aria-label={`Open run "${agent.conversation_title ?? UNTITLED}"`}
                  style={rowButtonStyle}
                >
                  <span style={rowHeadStyle}>
                    <StatusPill
                      status={presentation.tone}
                      label={presentation.label}
                      showDot={presentation.showDot}
                    />
                    {isCurrent ? (
                      <span
                        className="ui-eyebrow"
                        data-testid="agent-fleet-this-run"
                      >
                        This run
                      </span>
                    ) : null}
                  </span>
                  <span style={titleStyle} data-testid="agent-fleet-title">
                    {agent.conversation_title ?? UNTITLED}
                  </span>
                  {agent.pending_count > 0 ? (
                    <span
                      className="ui-pill"
                      data-testid="agent-fleet-waiting"
                      style={pillStyle}
                    >
                      {agent.pending_count} waiting
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {/* Scheduled-agents section — renders only when a host supplies it. */}
      {scheduledSlot ?? null}
      <p
        className="ui-mono-caps ui-mono-caps--9"
        data-testid="agent-fleet-note"
        style={noteStyle}
      >
        {FOOTER_NOTE}
      </p>
    </div>
  );
}

// ── styles (layout only; type/color from kit recipes + tokens) ───────────────

const wrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm, 8px)",
  padding: "var(--space-sm, 8px)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2xs, 4px)",
};

const rowStyle: CSSProperties = {
  display: "block",
};

const rowButtonStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: "var(--space-2xs, 4px)",
  width: "100%",
  textAlign: "left",
  padding: "var(--space-sm, 8px) var(--space-md, 12px)",
};

const rowHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm, 8px)",
};

const titleStyle: CSSProperties = {
  color: "var(--color-text, #f4f5f6)",
  wordBreak: "break-word",
};

const pillStyle: CSSProperties = {
  alignSelf: "flex-start",
};

const noteStyle: CSSProperties = {
  margin: 0,
  padding: "var(--space-2xs, 4px) var(--space-md, 12px)",
};

const emptyStyle: CSSProperties = {
  padding: "var(--space-lg, 16px)",
  color: "var(--color-text-muted, #9aa0aa)",
};
