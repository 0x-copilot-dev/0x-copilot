// Local Agent shape for P8-B1 (gallery + card + panel).
//
// Phase 8 lands the production `Agent` shape in `@enterprise-search/api-types`
// (P8-A5 wired Projects.default_agent_id; P8-A1 will land the full Agent
// table). Until then, the gallery surface needs a stable shape it can render
// against — that is what this file is. Consumers wire real data from
// apps/frontend in P8-C (data-binder phase).
//
// SCOPE: presentation-only. No transport, no router, no service calls.

/**
 * Branded id — keeps Agent ids distinct from Run ids, Skill ids, etc. in
 * the type system. Same pattern as `ConversationId` / `RunId` in
 * `destinations/home`.
 */
export type AgentId = string & { readonly __brand: "AgentId" };

/**
 * Where the agent came from — drives the gallery's filter tabs and
 * informs install / customize affordances.
 *
 * - `installed`  — the user (or workspace) has installed this agent and it
 *                  is invokable in chats; appears under "My agents".
 * - `available`  — a first-party or workspace agent the user can install
 *                  with one tap.
 * - `custom`     — an agent the user built from scratch in the editor.
 */
export type AgentOrigin = "installed" | "available" | "custom";

/**
 * Cost tier — surfaced as a chip on every card. Cost is a load-bearing
 * dimension for trust per the UI/UX preamble (and the agents-prd in
 * destinations-master-prd §5.6), so we model it as a discrete enum rather
 * than a free-form number string. The card renders these as:
 *   free     → "Free"
 *   low      → "Low cost"
 *   medium   → "Medium cost"
 *   high     → "High cost"
 *   per_use  → "Per-use pricing"
 */
export type AgentCostTier = "free" | "low" | "medium" | "high" | "per_use";

/**
 * Stub Agent — the minimum shape the gallery surface needs to render a
 * card. Fields mirror the master-prd `Agent` contract (§5.6):
 *
 *   { id, name, description, owner, skills[], status, ...}
 *
 * Names are camelCase here (presentation contract); the wire contract
 * (api-types/agents.ts) will use snake_case and the data-binder phase
 * normalizes between them.
 */
export interface AgentStub {
  readonly id: AgentId;
  readonly name: string;
  /** One-line description — the gallery clamps it to 2 lines visually. */
  readonly description: string;
  /** Optional emoji or short token rendered as the card icon. */
  readonly icon?: string;
  readonly origin: AgentOrigin;
  readonly costTier: AgentCostTier;
  /** Skills the agent uses (display names) — used by the by-skill filter. */
  readonly skills: ReadonlyArray<string>;
  /** True when the user can invoke this agent today. */
  readonly installed: boolean;
}

/**
 * Filter axes for the gallery. Mirrors the master-prd categories
 * (Yours / Workspace / Marketplace) but re-frames as the App-Store
 * model in the UI/UX preamble:
 *
 *   - my       — user has installed AND is the user's own (custom or
 *                installed-from-marketplace; the "team of helpers" view)
 *   - installed— anything installed in the workspace
 *   - available— installable but not installed
 *   - custom   — agents the user built from scratch
 *   - by_skill — filtered by a chosen skill (skill picker lives in panel)
 */
export type AgentFilter =
  | "my"
  | "installed"
  | "available"
  | "custom"
  | "by_skill";

/** Human label for a filter — single source for tab + panel + a11y. */
export const AGENT_FILTER_LABELS: Readonly<Record<AgentFilter, string>> = {
  my: "My agents",
  installed: "Installed",
  available: "Available",
  custom: "Custom",
  by_skill: "By skill",
};

/** Human label for a cost tier — single source. */
export const AGENT_COST_LABELS: Readonly<Record<AgentCostTier, string>> = {
  free: "Free",
  low: "Low cost",
  medium: "Medium cost",
  high: "High cost",
  per_use: "Per-use",
};

/**
 * Pure filter — given a candidate set + active filter (+ optional skill),
 * return the agents that should be shown. Lifted out of the destination so
 * tests can pin the matrix without rendering.
 */
export function filterAgents(
  agents: ReadonlyArray<AgentStub>,
  filter: AgentFilter,
  skillFilter: string | null,
): ReadonlyArray<AgentStub> {
  switch (filter) {
    case "my":
      // "My agents" = installed (the user's working set). Custom-only would
      // hide installed-from-marketplace, which is the wrong default.
      return agents.filter((a) => a.installed);
    case "installed":
      return agents.filter((a) => a.installed);
    case "available":
      return agents.filter((a) => !a.installed);
    case "custom":
      return agents.filter((a) => a.origin === "custom");
    case "by_skill":
      if (skillFilter === null || skillFilter.trim() === "") return agents;
      return agents.filter((a) =>
        a.skills.some((s) => s.toLowerCase() === skillFilter.toLowerCase()),
      );
  }
}

/**
 * Pure search — case-insensitive name + description match. Lifted for
 * the same reason as filterAgents.
 */
export function searchAgents(
  agents: ReadonlyArray<AgentStub>,
  query: string,
): ReadonlyArray<AgentStub> {
  const needle = query.trim().toLowerCase();
  if (needle === "") return agents;
  return agents.filter((a) => {
    const hay = `${a.name} ${a.description}`.toLowerCase();
    return hay.includes(needle);
  });
}

/**
 * Starter recommendations rendered when "My agents" is empty.
 *
 * Per the UI/UX preamble: an empty installed list MUST suggest 3-4 starter
 * agents with one-tap install. Doing this here (rather than over the wire)
 * keeps the surface useful even before the backend ships an
 * `/v1/agents/recommended` endpoint — and per the master-prd this destination
 * lives behind the facade anyway; the data-binder phase swaps these out for
 * a real fetch.
 */
export const STARTER_RECOMMENDATIONS: ReadonlyArray<AgentStub> = [
  {
    id: "starter_research" as AgentId,
    name: "Research Assistant",
    description:
      "Searches the web, your library, and your inbox. Cites every claim.",
    icon: "🔎",
    origin: "available",
    costTier: "low",
    skills: ["web-search", "library-search", "summarize"],
    installed: false,
  },
  {
    id: "starter_email" as AgentId,
    name: "Email Drafter",
    description:
      "Drafts replies that match your tone. Schedules sends. Asks before sending.",
    icon: "✉️",
    origin: "available",
    costTier: "low",
    skills: ["email-read", "email-draft", "calendar"],
    installed: false,
  },
  {
    id: "starter_sheets" as AgentId,
    name: "Sheets Analyst",
    description:
      "Pulls data, builds pivots, and explains the answer in plain language.",
    icon: "📊",
    origin: "available",
    costTier: "medium",
    skills: ["sheets", "summarize"],
    installed: false,
  },
  {
    id: "starter_slides" as AgentId,
    name: "Slides Builder",
    description:
      "Turns a Doc or transcript into a presentation. Keeps your brand colors.",
    icon: "🎞️",
    origin: "available",
    costTier: "medium",
    skills: ["slides", "summarize"],
    installed: false,
  },
];
