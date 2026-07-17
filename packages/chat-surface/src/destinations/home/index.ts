// Home destination — public surface + ItemRef resolver registration.
//
// Phase 9 rewrite (sub-PRD home-prd.md §3.1 / §3.2). The Phase 2 rich
// section vocabulary (PinnedChat / RecentRun / FavoriteTool / TodoSummary
// / MeetingSummary / StarredProject) is retired in this destination.
// Wire-types are sourced directly from `@0x-copilot/api-types`
// (no per-destination stub) so contract drift cannot creep in.
//
// Per cross-audit §1.1 + §3.3, each destination registers its kind on
// package import. Home owns the resolvers for kinds the Phase 9 payload
// surfaces: `chat` (live activity), `run` (live activity), `subagent`
// + `tool_result` (live activity click-through). Other kinds the
// Phase 9 surfaces use but OTHER destinations own (`meeting_external`,
// `routine`, `project`, `todo`, `approval`, `inbox_item`) register at
// their owning destination's package landing.

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

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// Registrations run once at package import. Guarded with
// `hasItemRefResolver` to keep test environments — which may import
// the module in multiple realms / vitest workers — from throwing
// `ItemRefResolverAlreadyRegistered`. The owning destination later
// upgrades with `{ replace: true }` when it ships a richer resolver.

// `chat` — WhatsNewDigest + LiveActivityRail surface chats.
if (!hasItemRefResolver("chat")) {
  registerItemRefResolver("chat", async (id) => ({
    label: "Chat",
    icon: null,
    route: { kind: "chat", conversationId: id as unknown as string },
    breadcrumb: "Chats",
  }));
}

// `run` — runs surfaced in live activity + timeline `run_scheduled`.
if (!hasItemRefResolver("run")) {
  registerItemRefResolver("run", async (id) => ({
    label: "Run",
    icon: null,
    route: { kind: "run", runId: id as unknown as string },
    breadcrumb: "Runs",
  }));
}

// `subagent` — placeholder; chats destination ships a richer resolver
// that correlates the parent run.
if (!hasItemRefResolver("subagent")) {
  registerItemRefResolver("subagent", async (_id) => ({
    label: "Subagent",
    icon: null,
    route: null,
    breadcrumb: "Subagents",
  }));
}

// `tool_result` — placeholder; chats destination ships the richer
// (runId, stepId) resolver.
if (!hasItemRefResolver("tool_result")) {
  registerItemRefResolver("tool_result", async (_id) => ({
    label: "Tool result",
    icon: null,
    route: null,
    breadcrumb: "Tool results",
  }));
}
