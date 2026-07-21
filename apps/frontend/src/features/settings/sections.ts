import type { SettingsSection } from "./settingsSections";

// The complete list of valid settings-section slugs. PR 4.1 / 4.2 / 4.3
// each grew the union independently, so the list lives here (not in
// App.tsx) to avoid merge collisions in the route table.
export const SETTINGS_SECTIONS = [
  // PR 8.1 — ACCOUNT group (was "You")
  "profile",
  "appearance",
  "shortcuts",
  "api-keys",
  // PR 8.1 — WORKSPACE group
  "workspace",
  "members",
  "billing",
  "audit-log",
  // PR 8.1 — AI & DATA group
  "model-and-behavior",
  // BYOK — per-user model provider keys (OpenAI / Anthropic / Google / OpenRouter).
  "provider-keys",
  // PRD-E convergence — SSOT-only nav slugs reachable via the web
  // `SettingsBinder` (chat-surface `SettingsSurface`). Web has no body yet
  // (surface placeholder) but they must parse so nav clicks reflect to the URL.
  "models",
  "app-lock",
  // Round 2 — local (Ollama) models; only rendered on desktop/self-host.
  "local-models",
  "connectors",
  "skills",
  "privacy-data",
  // PR 8.1 — NOTIFICATIONS group
  "notifications",
] as const satisfies readonly SettingsSection[];

/** Slug rendered when `/settings` carries no hash. */
export const DEFAULT_SETTINGS_SECTION: SettingsSection = "profile";
