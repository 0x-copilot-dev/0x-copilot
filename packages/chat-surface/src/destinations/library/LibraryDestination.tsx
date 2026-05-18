import { type ReactElement } from "react";

import { DestinationPlaceholder } from "../../shell/DestinationPlaceholder";

// Wave-0 Library surface is a dignified placeholder. The earlier
// implementation fetched /v1/library?kind=adapter (404) and rendered
// three tabs — Adapters / Results / Knowledge — whose names don't
// match the upcoming Phase 7 design (Files / Pages / Datasets) and
// would mislead users about what the destination is for. The real
// destination ships in Phase 7 — see
// docs/atlas-new-design/destinations-master-prd.md §8.

function LibraryHeroIcon(): ReactElement {
  // Mirrors AppRail's library glyph (two book spines) so the user
  // recognizes the destination they navigated to.
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
      <path d="M4 5h6v14H4z" />
      <path d="M14 5h6v14h-6z" />
      <path d="M7 8h0M7 11h0" />
    </svg>
  );
}

export function LibraryDestination(): ReactElement {
  return (
    <DestinationPlaceholder
      icon={<LibraryHeroIcon />}
      title="Your knowledge library"
      description="A workspace for the files, pages, and datasets your agents can search and cite. Upload PDFs and docs, link external knowledge bases, and let the agent pull from them in chats and runs."
      phaseLabel="Coming in Phase 7"
      bridges={[
        {
          label: "Browse what's connected today in Connectors",
          slug: "connectors",
        },
        { label: "Cite a source in a new chat", slug: "chats" },
      ]}
    />
  );
}
