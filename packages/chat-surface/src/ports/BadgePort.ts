// BadgePort — destination numeric badge (dock/tray on desktop, no-op on web
// for now). Source: cross-audit.md §1.2 (binding 2026-05-17).
//
// Substrate-agnostic contract: destinations call `setBadge(slug, count)`
// without checking `if (window…)`. The host injects an implementation;
// the web no-op implementation lives in `apps/frontend/src/ports/` and
// the desktop native implementation in `apps/desktop/src/main/ports/`
// (when desktop ships). The interface itself never touches `window`.

import type { ShellDestinationSlug } from "../shell/destinations";

export interface BadgePort {
  /**
   * Set the numeric badge for a destination slug. `count = 0` clears.
   *
   * Web substrate: no-op (favicon overlay deferred to Wave 4+).
   * Desktop substrate: shows the count on the dock / tray icon scoped
   *   to the destination slug; the host aggregates per-slug counts into
   *   a single OS badge.
   */
  setBadge(slug: ShellDestinationSlug, count: number): void;
}
