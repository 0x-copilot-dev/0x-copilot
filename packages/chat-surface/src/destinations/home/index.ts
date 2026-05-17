// Home destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3, each destination registers its kind on
// package import. Home owns the resolvers for kinds the Home payload
// surfaces in the agent-activity feed (sub-PRD §4.3): `chat`, `run`,
// `subagent`, and `tool_result`. Other kinds (`todo`, `inbox_item`,
// `meeting_external`, `project`, `library_dataset`, …) register at
// their own destination's package landing.
//
// The resolvers here are *minimal* — they return display labels +
// `ArtifactRoute`s the Atlas shell already understands. Richer
// resolvers (with breadcrumb + denormalized icons) ship per-destination
// later; cross-audit §3.3 requires the registry to be populated before
// any `<ItemLink>` render, not that every kind have a polished resolver.
//
// Branded IDs (ConversationId / RunId / etc.) live in
// `@enterprise-search/api-types` — chat-surface's top-level `index.ts`
// re-exports them from the canonical declaration site.

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import { HomeDestination, type HomeDestinationProps } from "./HomeDestination";
import { HomePanel, type HomePanelProps } from "./HomePanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export { HomeDestination, type HomeDestinationProps };
export { HomePanel, type HomePanelProps };

// Wire-type re-exports (forwarded from `_home-stub.ts`; the orchestrator
// rewires the stub to `@enterprise-search/api-types` at merge — see
// `_home-stub.ts` header).
//
// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  AgentActivityEntry,
  AgentActivityKind,
  FavoriteToolSummary,
  HomeGreeting,
  HomePayload,
  HomeResponse,
  HomeSectionKey,
  MeetingSummary,
  PinnedChatSummary,
  QuickAction,
  RecentRunStatus,
  RecentRunSummary,
  StarredProjectSummary,
  TimeOfDay,
  TodoSummary,
} from "./HomeDestination";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// Registrations run once at package import. Guarded with
// `hasItemRefResolver` to keep test environments — which may import
// the module in multiple realms / vitest workers — from throwing
// `ItemRefResolverAlreadyRegistered`. The owning destination later
// upgrades with `{ replace: true }` when it ships a richer resolver.

// `chat` — the activity feed and pinned-chats grid both surface chats.
if (!hasItemRefResolver("chat")) {
  registerItemRefResolver("chat", async (id) => ({
    // Display label: best-effort fallback used by `<ItemLink>` before
    // the owning destination registers a richer resolver. Phase 2 ships
    // only the route binding; chats destination Phase 1 may later
    // replace this with a label that pulls the conversation title from
    // its store.
    label: "Chat",
    icon: null,
    route: { kind: "chat", conversationId: id as unknown as string },
    breadcrumb: "Chats",
  }));
}

// `run` — recent runs + completed/failed_run activity entries.
if (!hasItemRefResolver("run")) {
  registerItemRefResolver("run", async (id) => ({
    label: "Run",
    icon: null,
    route: { kind: "run", runId: id as unknown as string },
    breadcrumb: "Runs",
  }));
}

// `subagent` — surface deep-dive into a specific subagent within a run.
// `ArtifactRoute.subagent` requires both `runId` + `subagentId`; the
// registry only carries `subagentId`, so the chats destination's
// richer resolver (which can correlate the parent run from its store)
// replaces this later. Until then, return `route: null` so `<ItemLink>`
// renders the graceful "deleted subagent" chip rather than a dead-link
// link.
if (!hasItemRefResolver("subagent")) {
  registerItemRefResolver("subagent", async (_id) => ({
    label: "Subagent",
    icon: null,
    route: null,
    breadcrumb: "Subagents",
  }));
}

// `tool_result` — completed-run activity entries link out to the
// tool-result detail surface for inspection. Same caveat as `subagent`:
// the chats destination later registers a richer resolver that maps
// `tool_result` → (runId, stepId).
if (!hasItemRefResolver("tool_result")) {
  registerItemRefResolver("tool_result", async (_id) => ({
    label: "Tool result",
    icon: null,
    route: null,
    breadcrumb: "Tool results",
  }));
}
