// PaletteSearchPort — substrate-agnostic search transport for the
// global ⌘K command palette.
//
// Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
//   §1.3 (palette is substrate-shared), §3.3 (palette wire shape),
//   §4.3 (single `/v1/palette/search` endpoint), §7.3 (FE surface).
//
// The contract describes behavior, not the underlying transport:
//   * Web substrate: HTTPS fetch to `backend-facade` `/v1/palette/search`.
//   * Desktop substrate: same HTTPS call, optionally merged with hits
//     from a local index in the main process.
//   * Tests: an in-memory implementation that returns a canned list.
//
// All shapes (`PaletteSearchRequest`, `PaletteSearchResponse`,
// `PaletteHit`, etc.) come from `@0x-copilot/api-types/palette`
// — this file does not re-declare them. Zero `__brand:` declarations.

import type {
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@0x-copilot/api-types";

export interface PaletteSearchPort {
  /**
   * Returns hits within a ~200ms p95 budget. The host MAY return early
   * with fewer hits if the budget is exceeded — partial results are
   * preferred over a stall, and the palette renders whatever the port
   * returns. Rejection is reserved for hard failures (network down,
   * unauthorized); the palette surfaces those by clearing the hit list
   * and showing the contextual "No results / Connect a tool →" hint.
   */
  search(req: PaletteSearchRequest): Promise<PaletteSearchResponse>;
}
