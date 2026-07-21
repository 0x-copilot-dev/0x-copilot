// Settings section identity + the legacy nav-rail config.
//
// Extracted from `SettingsScreen.tsx` (which had grown to ~1400 lines and
// owned the routing type, the nav config, the chrome, AND two full sub-features).
// This module is the data/types layer: the `SettingsSection` routing union (the
// app's URL contract — imported by `App.tsx` / `routes.ts` / `HashRouter.ts`, and
// re-exported from `SettingsScreen` for back-compat) plus the `railSections`
// descriptor the legacy nav renders.

export type SettingsSection =
  // PR 8.1 — ACCOUNT group (per-user identity + appearance + shortcuts +
  // personal API keys). Lands first so users find their own settings.
  | "profile"
  | "appearance"
  | "shortcuts"
  | "api-keys"
  // PR 8.1 — WORKSPACE group (admin / shared surfaces).
  | "workspace"
  | "members"
  | "billing"
  | "audit-log"
  // PR 8.1 — AI & DATA group (agent behavior + sources).
  | "model-and-behavior"
  // BYOK — per-user model provider keys. Distinct from "api-keys"
  // (Account group), which are Atlas bearer tokens.
  | "provider-keys"
  // PRD-E convergence — SSOT nav slugs the legacy screen never had a route
  // for. The web `SettingsBinder` mounts the chat-surface `SettingsSurface`,
  // whose nav can navigate to the model-curation ("models") and desktop
  // "app-lock" sections; both must parse as valid route sections so a nav
  // click reflects to the URL. Web has no body for either yet (the surface
  // shows its placeholder), but the slugs are routable.
  | "models"
  | "app-lock"
  // Round 2 — local Ollama models (desktop / self-host only; gated by a
  // server status probe).
  | "local-models"
  | "connectors"
  | "skills"
  | "privacy-data"
  // PR 8.1 — NOTIFICATIONS (single section, kept as its own group to
  // match the design bundle's IA).
  | "notifications";

// PR 8.1 — `RailEntry` carries the icon glyph + an optional badge so the
// rail rows visually match the Atlas design (icon + label + count /
// "Admin" tag). Group entries are heading rows the user can't click.
export type RailIcon =
  | "user"
  | "sun"
  | "command"
  | "key"
  | "building"
  | "users"
  | "card"
  | "doc"
  | "spark"
  | "link"
  | "book"
  | "shield"
  | "bell";

export type RailEntry =
  | { kind: "group"; label: string }
  | {
      kind: "section";
      id: SettingsSection;
      label: string;
      icon: RailIcon;
      /**
       * Optional badge override. When omitted the rail computes a sensible
       * default at render time (member count, connector count, etc.).
       */
      badge?: string;
      /**
       * Slug for the data-driven badge resolver. `null` means no badge,
       * a string keys into the runtime count map below.
       */
      countKey?: "members" | "connectors" | "skills" | null;
      /** Show the static "Admin" pill — purely cosmetic; backend still gates. */
      adminPill?: boolean;
    };

export const railSections: ReadonlyArray<RailEntry> = [
  { kind: "group", label: "Account" },
  { kind: "section", id: "profile", label: "Profile", icon: "user" },
  { kind: "section", id: "appearance", label: "Appearance", icon: "sun" },
  { kind: "section", id: "shortcuts", label: "Shortcuts", icon: "command" },
  { kind: "section", id: "api-keys", label: "API keys", icon: "key" },
  { kind: "group", label: "Workspace" },
  {
    kind: "section",
    id: "workspace",
    label: "Workspace",
    icon: "building",
    adminPill: true,
  },
  {
    kind: "section",
    id: "members",
    label: "Members & roles",
    icon: "users",
    countKey: "members",
  },
  {
    kind: "section",
    id: "billing",
    label: "Billing & usage",
    icon: "card",
  },
  {
    kind: "section",
    id: "audit-log",
    label: "Audit log",
    icon: "doc",
    adminPill: true,
  },
  { kind: "group", label: "AI & data" },
  {
    kind: "section",
    id: "model-and-behavior",
    label: "Model & behavior",
    icon: "spark",
  },
  {
    kind: "section",
    id: "provider-keys",
    label: "Provider keys",
    icon: "key",
  },
  {
    kind: "section",
    id: "local-models",
    label: "Local models",
    icon: "spark",
  },
  {
    kind: "section",
    id: "connectors",
    label: "Connectors",
    icon: "link",
    countKey: "connectors",
  },
  {
    kind: "section",
    id: "skills",
    label: "Skills",
    icon: "book",
    countKey: "skills",
  },
  {
    kind: "section",
    id: "privacy-data",
    label: "Privacy & data",
    icon: "shield",
  },
  { kind: "group", label: "Notifications" },
  {
    kind: "section",
    id: "notifications",
    label: "Notifications",
    icon: "bell",
  },
];
