// Settings section identity — the `SettingsSection` routing union.
//
// This is the app's URL contract for `#/settings/<section>`, imported by
// `App.tsx` / `routes.ts` / `HashRouter.ts`. It intentionally remains a
// superset of the chat-surface `SettingsSectionSlug` nav: `connectors` and
// `skills` stay ROUTABLE (legacy deep-links redirect to the Tools / Skills
// rail destinations — PR-E.3), and the SSOT nav's `models` / `app-lock` slugs
// parse so a nav click reflects to the URL.
//
// The legacy nav descriptor (`railSections` / `RailIcon` / `RailEntry`) died
// with the legacy `SettingsScreen` (PR-E.3); the SSOT nav lives in
// `@0x-copilot/chat-surface` `settingsNav.ts`.

export type SettingsSection =
  // PR 8.1 — ACCOUNT group (per-user identity + appearance + shortcuts +
  // personal API keys).
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
  // PRD-E convergence — SSOT nav slugs (model curation / desktop app-lock).
  | "models"
  | "app-lock"
  // Round 2 — local Ollama models (desktop / self-host only).
  | "local-models"
  // PR-E.3 — routable for legacy deep-links only; both redirect to their rail
  // destinations (connectors → Tools, skills → Skills) in the App dispatch.
  | "connectors"
  | "skills"
  | "privacy-data"
  // PR 8.1 — NOTIFICATIONS.
  | "notifications";
