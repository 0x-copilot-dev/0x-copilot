import { type ReactElement } from "react";

import { DestinationPlaceholder } from "../../shell/DestinationPlaceholder";

// Wave-0 Memory surface is a dignified placeholder. The earlier
// implementation fetched /v1/memory?type=user (404 — no backend
// endpoint exists). The real destination ships in Phase 11 — see
// docs/atlas-new-design/destinations-master-prd.md §8.

function MemoryHeroIcon(): ReactElement {
  // Mirrors AppRail's memory glyph so the navigation context is
  // visually consistent.
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
      <path d="M9 5a4 4 0 0 0-4 4v6a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4V9a4 4 0 0 0-4-4z" />
      <path d="M9 9h6M9 12h6M9 15h4" />
    </svg>
  );
}

export function MemoryDestination(): ReactElement {
  return (
    <DestinationPlaceholder
      icon={<MemoryHeroIcon />}
      title="What the agent remembers"
      description="Long-term memory across chats and runs — what you've taught the agent about your role, your projects, and the people you work with. Review, edit, pin, and forget memories from one place."
      phaseLabel="Coming in Phase 11"
      bridges={[
        { label: "Teach the agent something new in a chat", slug: "chats" },
        { label: "See your team's shared context in Team", slug: "team" },
      ]}
    />
  );
}
