// SHELL_COMMANDS — the static ⌘K command-launcher layer (PRD-D).
//
// The current palette is a rich BACKEND search surface (PaletteSearchPort → a
// live index). That stays. What the v3 design also has — and the build lacked —
// is an instant command launcher: on an empty query, ⌘K lists a fixed set of
// nav/action commands so it works as a keyboard launcher without typing. These
// commands are the empty-query default and, once typing, are filtered and shown
// ABOVE the live search hits (complementary, not either/or).
//
// This module is the single source of truth for those commands. It is pure
// shell data (labels/icons/keywords/intent) — substrate-agnostic, no ports. The
// host maps each `intent` to real navigation via CommandPalette's `onCommand`.

import type { IconName } from "../icons/paths";
import type { ShellDestinationSlug } from "./destinations";

/**
 * What a command does when activated. Intentionally tiny: every v3 command is
 * either "go to a rail destination" or "open a Settings section". The host
 * interprets it (web router / desktop router), so the shell stays port-clean.
 */
/**
 * Direct-launch actions — commands that DO a thing rather than navigate to a
 * page. The host maps each to its own action seam (desktop: the add-key /
 * download-model / connect-tool / new-chat flows; web: its equivalents). Kept a
 * closed union so a new action is a compile-time decision on every host.
 */
export type ShellCommandAction =
  | "new-chat"
  | "add-provider-key"
  | "download-local-model"
  | "connect-tool";

export type ShellCommandIntent =
  | { readonly type: "navigate"; readonly slug: ShellDestinationSlug }
  | { readonly type: "settings"; readonly section: string }
  | { readonly type: "action"; readonly action: ShellCommandAction };

export interface ShellCommand {
  readonly id: string;
  readonly label: string;
  /** Short mono keyword shown right-aligned on the row (design `.cmdk__row .k`). */
  readonly keyword: string;
  readonly icon: IconName;
  readonly intent: ShellCommandIntent;
}

// The 13 design commands (copilot-app.jsx Palette), in the design's order.
export const SHELL_COMMANDS: readonly ShellCommand[] = [
  {
    id: "cmd-run",
    label: "Go to Run",
    keyword: "workspace",
    icon: "run",
    intent: { type: "navigate", slug: "run" },
  },
  {
    id: "cmd-chats",
    label: "Go to Chats",
    keyword: "chats",
    icon: "chats",
    intent: { type: "navigate", slug: "chats" },
  },
  {
    id: "cmd-projects",
    label: "Go to Projects",
    keyword: "projects",
    icon: "folder",
    intent: { type: "navigate", slug: "projects" },
  },
  {
    id: "cmd-new-chat",
    label: "New chat",
    keyword: "new run",
    icon: "plus",
    // Direct-launch: start a fresh run, not just navigate to the Run cockpit.
    intent: { type: "action", action: "new-chat" },
  },
  {
    id: "cmd-activity",
    label: "Go to Activity",
    keyword: "activity",
    icon: "activity",
    intent: { type: "navigate", slug: "activity" },
  },
  {
    id: "cmd-tools",
    label: "Go to Tools",
    keyword: "connectors",
    icon: "plug",
    intent: { type: "navigate", slug: "connectors" },
  },
  {
    id: "cmd-skills",
    label: "Go to Skills",
    keyword: "skills",
    icon: "skill",
    intent: { type: "navigate", slug: "tools" },
  },
  {
    id: "cmd-add-key",
    label: "Add a provider key",
    keyword: "BYOK",
    icon: "key",
    intent: { type: "action", action: "add-provider-key" },
  },
  {
    id: "cmd-local-model",
    label: "Download a local model",
    keyword: "local",
    icon: "chip",
    intent: { type: "action", action: "download-local-model" },
  },
  {
    id: "cmd-connect-tool",
    label: "Connect a tool",
    keyword: "connect",
    icon: "plug",
    intent: { type: "action", action: "connect-tool" },
  },
  {
    id: "cmd-behavior",
    label: "Model & behavior",
    keyword: "policy",
    icon: "sliders",
    intent: { type: "settings", section: "model-behavior" },
  },
  {
    id: "cmd-appearance",
    label: "Appearance",
    keyword: "theme",
    icon: "sun",
    intent: { type: "settings", section: "appearance" },
  },
  {
    id: "cmd-settings",
    label: "Open Settings",
    keyword: "settings",
    icon: "gear",
    intent: { type: "settings", section: "profile" },
  },
];

/** Filter commands by a query against label + keyword (case-insensitive). */
export function filterShellCommands(
  query: string,
  commands: readonly ShellCommand[] = SHELL_COMMANDS,
): readonly ShellCommand[] {
  const q = query.trim().toLowerCase();
  if (q.length === 0) return commands;
  return commands.filter((c) =>
    (c.label + " " + c.keyword).toLowerCase().includes(q),
  );
}
