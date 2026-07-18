// Skills destination (desktop redesign, Phase 4) — skill catalog summary.
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §5 (new types) +
// FR-4.26/4.27/4.29, and docs/plan/desktop-redesign/design-reference/
// DESIGN-SPEC.md §3 (List destinations — Skills).
//
// The Skills destination is a card grid of SAVED MULTI-STEP WORKFLOWS
// (name, sub, N runs; Run / Edit / New skill) — its own destination, not a
// Settings tab and not the tool-integration catalog (PRD §11 flags the
// PLAN §5 "Skills ← tools" mapping as inaccurate). Backed by `/v1/skills`.
//
// `SkillSummary` is the lightweight ROW projection the card grid renders;
// it is intentionally distinct from the richer authoring `Skill`
// (`skill_id` / `markdown` / `scope` / …, declared in ./index.ts) that
// `/v1/skills` serves for the editor. The host binder projects `Skill` →
// `SkillSummary` (adding `run_count`) until the backend exposes per-skill
// run counts directly (see PRD §11 backend gaps).
//
// Wire-only file: no business logic, no HTTP client, no view models. The
// server is the source of truth; this package mirrors the public payloads
// exactly as the facade serves them.
//
// Canonical types reused from elsewhere (DO NOT re-declare):
// * `SkillId` — branded ID in ./brands.ts (`ItemRef` kind="skill" resolves
//   to `SkillId` in ./refs.ts; the Run / Edit target).

import type { SkillId } from "./brands";

// ---------------------------------------------------------------------------
// Skill summary — the destination's card row
// ---------------------------------------------------------------------------

/**
 * One saved skill as rendered on a catalog card. `description` is the card
 * sub-line; `run_count` is the `N runs` badge (0 until the run has ever
 * fired, or until the backend surfaces the count — the binder defaults it
 * to 0). `updated_at` drives the mono relative time (formatted client-side
 * from the ISO string, never pre-formatted on the wire).
 */
export interface SkillSummary {
  readonly id: SkillId;
  readonly name: string;
  /** Card sub-line; the skill's short description. */
  readonly description: string;
  /** `N runs` badge — total historical runs of this skill. */
  readonly run_count: number;
  /** ISO-8601 UTC; server-stamped last-updated. Client renders relative. */
  readonly updated_at: string;
}
