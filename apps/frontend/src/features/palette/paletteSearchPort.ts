// PaletteSearchPort — substrate-shared port the ⌘K palette consumes.
//
// Sub-PRD §7.3: "Host wires a `PaletteSearchPort` (new port; mirrors
// NotificationPort/BadgePort pattern). Web → facade. Desktop → IPC."
//
// Until the canonical interface lands in `@enterprise-search/chat-surface`
// (P12-B3), this is the local declaration the web host satisfies. When
// the chat-surface package exports `PaletteSearchPort`, swap the import
// and delete this file — the shape MUST match.

import type {
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@enterprise-search/api-types";

export interface PaletteSearchPort {
  /**
   * Run a palette search. Implementations on web call `paletteApi.search`
   * through the facade; on desktop the implementation marshals the same
   * request over IPC to the trusted host.
   *
   * Implementations MUST be safe to call from any component (no implicit
   * identity / scope state). The port owns its own auth resolution.
   */
  search(req: PaletteSearchRequest): Promise<PaletteSearchResponse>;
}
