// Single source of truth for the shell's slug ↔ label mapping.
//
// This file has two consumers with different information architectures that
// share the SAME build:
//
//   1. The hosted web app (`apps/frontend`) renders the legacy 12-destination
//      Atlas rail. Its URL routing (`HashRouter.ts`, `routes.ts`, `App.tsx`)
//      is pinned to those 12 slugs, so their identity and order MUST NOT
//      change. `SHELL_DESTINATIONS` / `DEFAULT_SHELL_DESTINATION` are the
//      stable web contract.
//
//   2. The solo desktop app renders a profile-gated 6-destination rail
//      (`single_user_desktop`) or a 9-destination rail (`team`). Those views
//      are derived, per `DeploymentProfile`, via `destinationsForProfile` /
//      `defaultDestinationForProfile`.
//
// To keep ONE source of truth for slug↔label (the file's original invariant),
// every view is derived from a single `DESTINATION_REGISTRY`:
//   - the registry maps each slug to its canonical (legacy/web) label and,
//     where a profile view relabels it, an optional `profileLabel`;
//   - per-view ORDER is expressed as slug-only arrays (no label duplication),
//     because the legacy and solo orders are genuinely different sequences,
//     not one filtered subset of the other.
//
// Slug identity is preserved across profiles (regression-safe): the solo/team
// views relabel `connectors` → "Tools" and `tools` → "Skills" but keep those
// underlying slugs so web URLs/tests stay byte-identical. Only `run`,
// `activity`, `members`, `billing` are genuinely new slugs.

import type { DeploymentProfile } from "../providers/DeploymentProfileProvider";

export type ShellDestinationSlug =
  // --- legacy 12 (web Atlas rail; slug identity is a frozen contract) ---
  | "home"
  | "chats"
  | "agents"
  | "library"
  | "inbox"
  | "tools"
  | "projects"
  | "todos"
  | "connectors"
  | "team"
  | "memory"
  | "routines"
  // --- Phase 2 additions (solo/team shell IA); no legacy slug renamed ---
  | "run"
  | "activity"
  | "members"
  | "billing";

export interface ShellDestination {
  readonly slug: ShellDestinationSlug;
  readonly label: string;
}

interface DestinationMeta {
  /** Canonical label — used by the legacy web rail and as the default. */
  readonly label: string;
  /**
   * Label shown when this slug appears in a profile-gated view
   * (`single_user_desktop` / `team`). Only set where a view relabels a slug
   * without renaming it: `connectors` → "Tools", `tools` → "Skills".
   */
  readonly profileLabel?: string;
}

// Canonical slug → metadata. The ONLY place a slug's label lives.
const DESTINATION_REGISTRY: Readonly<
  Record<ShellDestinationSlug, DestinationMeta>
> = {
  home: { label: "Home" },
  chats: { label: "Chats" },
  agents: { label: "Agents" },
  library: { label: "Library" },
  inbox: { label: "Inbox" },
  tools: { label: "Tools", profileLabel: "Skills" },
  projects: { label: "Projects" },
  todos: { label: "Todos" },
  connectors: { label: "Connectors", profileLabel: "Tools" },
  team: { label: "Team" },
  memory: { label: "Memory" },
  routines: { label: "Routines" },
  run: { label: "Run" },
  activity: { label: "Activity" },
  members: { label: "Members" },
  billing: { label: "Billing" },
};

// Per-view ORDER — slug-only sequences derived against the registry above.

const LEGACY_ORDER: readonly ShellDestinationSlug[] = [
  "home",
  "chats",
  "agents",
  "library",
  "inbox",
  "tools",
  "projects",
  "todos",
  "connectors",
  "team",
  "memory",
  "routines",
];

// Solo desktop: Run, Chats, Projects, Activity, Tools (slug `connectors`),
// Skills (slug `tools`). Slugs preserved; labels come from `profileLabel`.
const SOLO_ORDER: readonly ShellDestinationSlug[] = [
  "run",
  "chats",
  "projects",
  "activity",
  "connectors",
  "tools",
];

// Team desktop: the 6 solo destinations plus the team-only surfaces.
const TEAM_ORDER: readonly ShellDestinationSlug[] = [
  ...SOLO_ORDER,
  "team",
  "members",
  "billing",
];

function toDestination(
  slug: ShellDestinationSlug,
  useProfileLabel: boolean,
): ShellDestination {
  const meta = DESTINATION_REGISTRY[slug];
  const label =
    useProfileLabel && meta.profileLabel !== undefined
      ? meta.profileLabel
      : meta.label;
  return { slug, label };
}

// Legacy web rail — derived from the registry, not a parallel hand-list, so
// there is no second source of truth for slug↔label. Order/labels unchanged.
export const SHELL_DESTINATIONS: readonly ShellDestination[] = LEGACY_ORDER.map(
  (slug) => toDestination(slug, false),
);

export const DEFAULT_SHELL_DESTINATION: ShellDestinationSlug = "home";

/**
 * The rail destinations for a deployment profile, in display order.
 *
 * - `single_user_desktop` → `[Run, Chats, Projects, Activity, Tools, Skills]`
 *   (slugs `run, chats, projects, activity, connectors, tools`).
 * - `team` → the 6 solo destinations followed by `Team, Members, Billing`.
 * - any unknown/undefined profile → the solo set (fail-safe: never leak the
 *   team-only surfaces).
 */
export function destinationsForProfile(
  profile: DeploymentProfile,
): readonly ShellDestination[] {
  // Only an explicit `team` profile unlocks the team surfaces; everything
  // else (incl. an unknown/undefined value) falls back to the smaller solo
  // set so team destinations can never leak.
  const order = profile === "team" ? TEAM_ORDER : SOLO_ORDER;
  return order.map((slug) => toDestination(slug, true));
}

/**
 * The destination the shell lands on for a profile. Both solo and team open
 * on the Run cockpit — the flagship front door, not an archive list.
 */
export function defaultDestinationForProfile(
  _profile: DeploymentProfile,
): ShellDestinationSlug {
  return "run";
}
