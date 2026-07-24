// Focus Agents rail adapter.
//
// `SubagentEntry` is deliberately additive across API revisions. Keep the
// presentation-only fields guarded here: an archived entry from before the
// richer agent projection must continue to produce a useful row, while a new
// entry can safely add parent/model/current-activity context without changing
// the rich in-thread `SubagentCard` contract.

import type { SubagentEntry } from "@0x-copilot/api-types";

import { formatAgentName, truncateText } from "./subagentHelpers";
import {
  subagentCardFromEntry,
  type SubagentCardViewModel,
} from "./subagentCardViewModel";

/** Additive, safe-to-display fields supplied by the agent projection. */
export interface AgentPresentationFields {
  readonly parent_task_id?: string | null;
  readonly parent_agent_role?: string | null;
  readonly parent_agent_name?: string | null;
  readonly model_display_label?: string | null;
  readonly current_activity?: string | null;
}

/** An entry as it is held by the live run-event projection. */
export type ProjectedSubagentEntry = SubagentEntry & AgentPresentationFields;

export interface AgentActivityRowViewModel extends SubagentCardViewModel {
  readonly parentTaskId: string | null;
  readonly parentAgentRole: string | null;
  readonly parentAgentName: string | null;
  readonly modelDisplayLabel: string | null;
  /** Compact, safe progress copy for the scan row. */
  readonly currentActivity: string | null;
}

const ACTIVITY_MAX = 180;

/**
 * Read future-safe presentation fields without making old API clients reject
 * pre-contract payloads. Empty / non-string values become `null`; absent
 * fields stay `undefined` so the event reducer can preserve an earlier value.
 */
export function agentPresentationFields(
  value: unknown,
): AgentPresentationFields {
  if (!isRecord(value)) return {};
  return {
    parent_task_id: optionalText(value.parent_task_id),
    parent_agent_role: optionalText(value.parent_agent_role),
    parent_agent_name: optionalText(value.parent_agent_name),
    model_display_label: optionalText(value.model_display_label),
    current_activity: optionalText(value.current_activity),
  };
}

/**
 * Shape a workspace entry for the quiet Focus rail. Legacy entries use their
 * objective (or terminal finding) as a sensible activity fallback; they never
 * regress to the old bright status badge / large task treatment.
 */
export function agentActivityRowFromEntry(
  entry: SubagentEntry,
): AgentActivityRowViewModel {
  const card = subagentCardFromEntry(entry);
  const fields = agentPresentationFields(entry);
  const currentActivity = compactActivity(
    fields.current_activity ??
      (card.terminal ? (card.finding ?? card.task) : card.task),
  );

  return {
    ...card,
    parentTaskId: fields.parent_task_id ?? null,
    parentAgentRole: fields.parent_agent_role ?? null,
    parentAgentName: fields.parent_agent_name ?? null,
    modelDisplayLabel: fields.model_display_label ?? null,
    currentActivity,
  };
}

/** A human label for a synthetic parent row when the API sends a role only. */
export function displayAgentRole(role: string | null): string | null {
  if (role === null) return null;
  const trimmed = role.trim();
  if (!trimmed) return null;
  // The runtime's factual role is `supervisor`; the Focus vocabulary calls
  // that same lead agent the Orchestrator. This is a label alias, not a
  // fabricated display identity (which remains optional in the contract).
  if (trimmed === "supervisor" || trimmed === "orchestrator") {
    return "Orchestrator";
  }
  return formatAgentName(trimmed.replace(/[\s-]+/g, "_"));
}

function compactActivity(value: string | null): string | null {
  if (!value) return null;
  const collapsed = value.replace(/\s+/g, " ").trim();
  return collapsed ? truncateText(collapsed, ACTIVITY_MAX) : null;
}

function optionalText(value: unknown): string | null | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
