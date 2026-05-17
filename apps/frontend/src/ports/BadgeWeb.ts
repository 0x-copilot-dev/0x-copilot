// Web BadgePort — no-op implementation.
//
// Source: cross-audit.md §1.2 + chats-canvas-prd §5.4 (port injection
// convention). The web substrate has no dock / tray icon today, so this
// is a deliberate no-op. A future favicon-overlay variant could write a
// numeric badge onto the page favicon (deferred to Wave 4+ per the
// BadgePort doc comment in `packages/chat-surface/src/ports/BadgePort.ts`).
//
// Destinations call `setBadge(slug, count)` without checking the
// substrate; we satisfy the contract by doing nothing.

import type {
  BadgePort,
  ShellDestinationSlug,
} from "@enterprise-search/chat-surface";

export class WebBadgePort implements BadgePort {
  setBadge(_slug: ShellDestinationSlug, _count: number): void {
    // No-op on web; the OS dock / tray icon is desktop-only.
  }
}
