// Chats destination (desktop redesign, Phase 4) — archive read model.
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §5 (new types) +
// FR-4.5/4.6/4.9, and docs/plan/desktop-redesign/design-reference/
// DESIGN-SPEC.md §3 (List destinations — Chats) + §8 (data entities:
// `CHATS`).
//
// The Chats destination is a conversation ARCHIVE (pinned / recent /
// archived), not a live thread canvas — a row click reopens the thread
// in the Run cockpit. The bucketed `ChatsArchive` is the shape the
// destination consumes; the host binder composes it from
// `/v1/agent/conversations` (including archived) until a dedicated
// bucketed endpoint exists (PRD §11 — `/v1/chats/projects` stub retired).
//
// Wire-only file: no business logic, no HTTP client, no view models. The
// server is the source of truth; this package mirrors the public payloads
// exactly as the facade serves them.
//
// Canonical types reused from elsewhere (DO NOT re-declare):
// * `ConversationId` — branded ID in ./brands.ts (a chat IS a conversation;
//   `ItemRef` kind="chat" resolves to `ConversationId` in ./refs.ts).

import type { ConversationId } from "./brands";

// ---------------------------------------------------------------------------
// Status taxonomy (DESIGN-SPEC §3 — Chats row status chip)
// ---------------------------------------------------------------------------

/**
 * Canonical status values for a chat archive row, as the runtime SSOT
 * (value tuple) the union derives from. Kept as an `as const` tuple so
 * the union is also runtime-enumerable (state-chip mapping, tests) with a
 * single declaration site — no value/type drift.
 *
 * * `running`  — the conversation has an in-flight run (live/jade chip).
 * * `done`     — last run completed; no in-flight work (muted chip).
 * * `paused`   — a run is paused awaiting input/approval (amber chip).
 * * `archived` — user-archived; surfaces under the Archived section.
 */
export const CHAT_ARCHIVE_STATUSES = [
  "running",
  "done",
  "paused",
  "archived",
] as const;

/** Chat archive row status. Drift from the server CHECK constraint is a bug. */
export type ChatArchiveStatus = (typeof CHAT_ARCHIVE_STATUSES)[number];

// ---------------------------------------------------------------------------
// Archive row
// ---------------------------------------------------------------------------

/**
 * One conversation in the archive list. `updated_at` drives the mono
 * relative time (formatted client-side from the ISO string, never a
 * pre-formatted string on the wire). `preview` is a one-line truncated
 * snippet of the latest turn; `model` is the mono model tag rendered on
 * the row. `pinned` lets a flat list be bucketed in the shell when the
 * server returns an unbucketed page.
 */
export interface ChatArchiveRow {
  readonly id: ConversationId;
  readonly title: string;
  readonly status: ChatArchiveStatus;
  /** One-line, truncated preview of the latest turn. */
  readonly preview: string;
  /** Mono model tag (e.g. `"gpt-4o"`); empty when unknown. */
  readonly model: string;
  /** ISO-8601 UTC; server-stamped. Client renders relative time. */
  readonly updated_at: string;
  readonly pinned: boolean;
}

// ---------------------------------------------------------------------------
// Bucketed archive response
// ---------------------------------------------------------------------------

/**
 * The Chats destination input: rows pre-bucketed into the three sections
 * DESIGN-SPEC §3 renders in order (Pinned / Recent / Archived). Empty
 * buckets are simply empty arrays; the destination hides empty sections.
 *
 * The host binder derives this from `/v1/agent/conversations` (incl.
 * archived): `pinned` rows → `pinned`, `status === "archived"` →
 * `archived`, the remainder → `recent` (PRD §11 — until a dedicated
 * bucketed endpoint lands, the composition lives in the binder and this
 * shape stays endpoint-agnostic).
 */
export interface ChatsArchive {
  readonly pinned: ReadonlyArray<ChatArchiveRow>;
  readonly recent: ReadonlyArray<ChatArchiveRow>;
  readonly archived: ReadonlyArray<ChatArchiveRow>;
}
