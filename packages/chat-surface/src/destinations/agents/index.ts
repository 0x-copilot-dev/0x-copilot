// Public surface for the Agents destination (P8-B1 + P8-B2 + P8-B3).
//
// Three building blocks:
//   - AgentsDestination — gallery + header + search + filter tabs
//   - AgentsPanel       — context panel with origin / skill / connector facets
//   - AgentCard         — card primitive
//
// The destination owns its own state today. The data-binder phase (P8-C)
// wires real data through Transport. Detail + editor are P8-B2.

export {
  AgentsDestination,
  type AgentsDestinationProps,
} from "./AgentsDestination";
export {
  AgentsPanel,
  AGENTS_PANEL_WIDTH,
  type AgentsPanelProps,
} from "./AgentsPanel";
export { AgentCard, type AgentCardProps } from "./AgentCard";

export {
  AGENT_COST_LABELS,
  AGENT_FILTER_LABELS,
  STARTER_RECOMMENDATIONS,
  filterAgents,
  searchAgents,
  type AgentCostTier,
  type AgentFilter,
  type AgentId,
  type AgentOrigin,
  type AgentStub,
} from "./_agents-stub";

import type { AgentId, AgentStub } from "./_agents-stub";

/**
 * Discriminated ItemRef for an agent. The full ItemRef registry lands in
 * SP-1 (`packages/chat-surface/src/refs/registry.ts`). Until it ships,
 * destinations expose a typed shape here that the registry will pick up
 * verbatim — minimizing churn at the time of integration.
 *
 * Wire compatibility: matches the chats master-prd `@mention` shape for an
 * agent (`{ kind: "agent", id }`). The registry's `resolve()` function
 * accepts this shape and returns the display payload below.
 */
export interface AgentItemRef {
  readonly kind: "agent";
  readonly id: AgentId;
}

/**
 * Display payload returned by the ItemRef resolver. Mirrors what
 * `<ItemLink>` will render: a label (the agent name), an optional
 * icon, and a destination route for click-through.
 */
export interface AgentItemDisplay {
  readonly kind: "agent";
  readonly id: AgentId;
  readonly label: string;
  readonly icon?: string;
  /**
   * Substrate-agnostic route for navigating to the agent's detail page.
   * Hosts wire this to their router (web HashRouter, desktop deep-link).
   */
  readonly route: { readonly kind: "agent"; readonly agentId: AgentId };
}

/**
 * ItemRef resolver for `kind: "agent"`.
 *
 * The registry calls this with the ref and a lookup function that returns
 * the AgentStub for the given id (or null when unknown — deleted /
 * tenant-mismatch / not-yet-fetched). The resolver maps the stub into the
 * display payload that `<ItemLink>` will render.
 *
 * Pure function: no transport, no router. The caller (the registry)
 * supplies the lookup so the resolver works against either an in-memory
 * cache or a paginated remote source.
 */
export function resolveAgentItemRef(
  ref: AgentItemRef,
  lookup: (id: AgentId) => AgentStub | null,
): AgentItemDisplay | null {
  const stub = lookup(ref.id);
  if (stub === null) return null;
  return {
    kind: "agent",
    id: stub.id,
    label: stub.name,
    icon: stub.icon,
    route: { kind: "agent", agentId: stub.id },
  };
}

// === P8-B2 detail + editor + fork dialog + version history ===
export {
  AgentEditor,
  AGENT_EDITOR_DEFAULTS,
  type AgentAutonomy,
  type AgentEditorModelDefault,
  type AgentEditorPermissions,
  type AgentEditorProps,
  type AgentEditorSaveState,
  type AgentEditorTabId,
  type AgentEditorValue,
  type AgentReasoningDepth,
} from "./AgentEditor";
export {
  AgentDetailView,
  type AgentDetailViewModel,
  type AgentDetailViewProps,
} from "./AgentDetailView";
export { ForkDialog, type ForkDialogProps } from "./ForkDialog";
export {
  VersionHistoryTab,
  type AgentVersionRow,
  type VersionHistoryTabProps,
} from "./VersionHistoryTab";

// === P8-B3 usage chart ===
export {
  AgentUsageChart,
  formatCostMicroUsd,
  type AgentUsageBucket,
  type AgentUsageChartProps,
  type AgentUsagePeriod,
  type AgentUsageResponse,
} from "./AgentUsageChart";
