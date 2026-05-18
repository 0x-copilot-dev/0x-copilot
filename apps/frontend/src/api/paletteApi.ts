// Typed wrappers for the Phase 12 ⌘K palette
// (sub-PRD `team-memory-cmdk-prd.md` §4.3).
//
// The palette has a single read surface: `GET /v1/palette/search`. The
// server fans out to per-destination indexes; the wire shape is a flat
// list of `PaletteHit`s with a `kind` discriminator (sub-PRD §3.3).
//
// Substrate boundary: the chat-surface package owns the
// `PaletteSearchPort` interface. The web host wires the port to this
// HTTP call; the desktop substrate will wire it to its IPC instead
// (sub-PRD §7.3 / cross-audit §5.4).

import type {
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "@enterprise-search/api-types";

import type { RequestIdentity } from "./config";
import { httpGet } from "./http";

/**
 * GET /v1/palette/search — substrate-shared search endpoint
 * (sub-PRD §4.3). Returns a ranked list of hits within a 200ms p95
 * budget (sub-PRD §12 done definition).
 *
 * `context` is an optional caller-supplied ranking hint; the server
 * uses it to bias suggestions (sub-PRD §3.3 `PaletteSearchContext`).
 * Empty `q` is allowed — the server returns its default "no-query"
 * suggestions (recent items / pinned actions).
 */
export function searchPalette(
  identity: RequestIdentity,
  req: PaletteSearchRequest,
): Promise<PaletteSearchResponse> {
  const params: Record<string, string | undefined> = { q: req.q };
  if (req.limit !== undefined) {
    params.limit = String(req.limit);
  }
  if (req.context?.current_route !== undefined) {
    params["context[current_route]"] = req.context.current_route;
  }
  if (req.context?.current_chat_id !== undefined) {
    params["context[current_chat_id]"] = req.context.current_chat_id;
  }
  if (req.context?.current_project_id !== undefined) {
    params["context[current_project_id]"] = req.context.current_project_id;
  }
  return httpGet<PaletteSearchResponse>("/v1/palette/search", identity, params);
}
