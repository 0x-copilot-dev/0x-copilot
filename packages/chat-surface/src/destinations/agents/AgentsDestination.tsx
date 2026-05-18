import { type ReactElement } from "react";

import { DestinationPlaceholder } from "../../shell/DestinationPlaceholder";

// Wave-0 Agents surface is a dignified placeholder, not a fetched
// table. The real destination ships in Phase 8 — see
// docs/atlas-new-design/destinations-master-prd.md §8. Until then
// the runtime exposes no GET /v1/agent/runs index endpoint, so the
// previous "loading → 405 → Retry" loop was misleading the user
// into thinking something was broken. The placeholder names the
// phase, explains the intended surface, and points at the working
// chats/home destinations that approximate the intent today.

function AgentsHeroIcon(): ReactElement {
  // Geometry mirrors the AppRail's agents glyph for visual continuity —
  // user sees the same shape they clicked on in the rail.
  return (
    <svg
      aria-hidden
      focusable={false}
      width={48}
      height={48}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.25}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
    </svg>
  );
}

export function AgentsDestination(): ReactElement {
  return (
    <DestinationPlaceholder
      icon={<AgentsHeroIcon />}
      title="Manage your agents"
      description="Browse, customize, and configure the AI agents that work on your behalf. Install community agents, fork them to make your own, and track cost per agent."
      phaseLabel="Coming in Phase 8"
      bridges={[
        { label: "See recent agent activity in Home", slug: "home" },
        { label: "View past runs in your Chats", slug: "chats" },
      ]}
    />
  );
}
