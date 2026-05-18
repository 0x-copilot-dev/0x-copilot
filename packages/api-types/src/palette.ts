// ⌘K command palette (Phase 12) — wire contract.
//
// Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
//   §3.3 (Palette wire shapes), §4.3 (single search endpoint),
//   §6.3 (ACL — read-only; results pre-filtered by per-entity ACL),
//   §7.3 (frontend surface — CommandPalette + PaletteSearchPort),
//   §9 (RoutineProposal + AtlasCronSuggestion ride this surface).
//
// One search endpoint (`GET /v1/palette/search`) fans out to per-
// destination indexes server-side; the wire shape is a flat list of
// `PaletteHit`s with a discriminator (`kind`) so the FE can render each
// row with the right affordance (navigate / open ItemRef / run action /
// run command).
//
// The palette is substrate-shared (sub-PRD §1.3): the same payload
// drives web, Mac, Windows; only the transport (`PaletteSearchPort`)
// differs per substrate.
//
// Single declaration site for: PaletteHitKind, PaletteHit,
// PaletteSearchRequest, PaletteSearchResponse, and the search context
// shape. Zero new `__brand:` declarations (cross-audit §2.1).

import type { ConversationId, ProjectId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * Discriminator on `PaletteHit`. Each variant routes the Enter key to a
 * different host action:
 *
 *   * `navigation` — jump-to-route. Use `route` (e.g. `/team`, `/inbox`).
 *   * `entity`     — jump-to-item. Use `target` (an `ItemRef`); host
 *     resolves via the canonical `<ItemLink>` registry (cross-audit §1.1).
 *   * `action`     — do-something contextual ("Make this a routine?",
 *     "Onboard a calendar"). Use `action_token` (server-defined opaque
 *     string the host's action registry knows how to dispatch).
 *   * `command`    — run-a-command, e.g. `/help`, `/context`. Use
 *     `action_token` set to the slash command.
 */
export type PaletteHitKind = "navigation" | "entity" | "action" | "command";

// ---------------------------------------------------------------------------
// Composite types
// ---------------------------------------------------------------------------

/**
 * One row of the palette result list.
 *
 * Exactly one of `route` / `target` / `action_token` is set, keyed by
 * `kind`. The FE narrows via `switch (hit.kind)` and reads the
 * corresponding field — `route` for `navigation`, `target` for `entity`,
 * `action_token` for `action` / `command`.
 *
 * `score` is the server's blended (BM25 + embedding + fuzzy) rank in
 * 0–1; it is used only for tiebreak when the host merges hits from
 * multiple substrates (e.g. desktop merging server hits with local
 * filesystem hits). Display order is the array order — server-decided.
 */
export interface PaletteHit {
  /** Server-generated id, prefix `hit_`; opaque to the FE. */
  readonly id: string;
  readonly kind: PaletteHitKind;
  readonly title: string;
  readonly subtitle?: string;
  /** Icon hint for the FE (`person`, `library_file`, `routine`, …). */
  readonly icon_hint?: string;
  /** When `kind === "entity"`: the ItemRef target. */
  readonly target?: ItemRef;
  /** When `kind === "navigation"`: the route to navigate to. */
  readonly route?: string;
  /** When `kind === "action"` / `"command"`: opaque action token. */
  readonly action_token?: string;
  /** Server score; 0–1. Used for substrate-merge tiebreak only. */
  readonly score: number;
}

/**
 * Caller-supplied context for ranking. Server uses this to bias suggestions
 * (e.g. when `current_chat_id` is set, the "Make this a routine?" action
 * ranks higher; when `current_project_id` is set, project-scoped library
 * pages rank higher).
 *
 * All fields are optional; the server treats unset fields as "no signal".
 */
export interface PaletteSearchContext {
  readonly current_route?: string;
  readonly current_chat_id?: ConversationId;
  readonly current_project_id?: ProjectId;
}

/** Body for `GET /v1/palette/search?q=…&context=…`. */
export interface PaletteSearchRequest {
  readonly q: string;
  /** Optional ranking context; see {@link PaletteSearchContext}. */
  readonly context?: PaletteSearchContext;
  /** Soft cap on the number of hits returned; server may clamp. */
  readonly limit?: number;
}

/** Response from `GET /v1/palette/search`. */
export interface PaletteSearchResponse {
  readonly hits: ReadonlyArray<PaletteHit>;
  /** Server-measured latency for the search call. */
  readonly took_ms: number;
}
