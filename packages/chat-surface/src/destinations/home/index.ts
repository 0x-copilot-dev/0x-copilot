// Home destination — public surface + ItemRef resolver registration.
//
// Phase 9 rewrite (sub-PRD home-prd.md §3.1 / §3.2). The Phase 2 rich
// section vocabulary (PinnedChat / RecentRun / FavoriteTool / TodoSummary
// / MeetingSummary / StarredProject) is retired in this destination.
// Wire-types are sourced directly from `@0x-copilot/api-types`
// (no per-destination stub) so contract drift cannot creep in.
//
// Wire-types are sourced directly from `@0x-copilot/api-types`. Cross-
// destination ROUTES are no longer registered here — PRD-04 Seam B moved all
// `ItemKind → route` registration into one table per host
// (`apps/frontend/src/app/itemRoutes.ts`, `apps/desktop/renderer/itemRoutes.ts`).
// Display text is the caller's (`<ItemLink label={…}>`, Seam A).

import { HomeDestination, type HomeDestinationProps } from "./HomeDestination";
import { HomePanel, type HomePanelProps } from "./HomePanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export { HomeDestination, type HomeDestinationProps };
export { HomePanel, type HomePanelProps };
