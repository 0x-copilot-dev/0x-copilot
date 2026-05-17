// Order matches the 12 top-level destinations in project_atlas_product_model
// (P5 adds Routines as the 12th — scheduled / triggered runs).
// AppRail / Topbar / ChatShell all read from this constant — single source
// of truth for the slug ↔ label mapping.

export type ShellDestinationSlug =
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
  | "routines";

export interface ShellDestination {
  readonly slug: ShellDestinationSlug;
  readonly label: string;
}

export const SHELL_DESTINATIONS: readonly ShellDestination[] = [
  { slug: "home", label: "Home" },
  { slug: "chats", label: "Chats" },
  { slug: "agents", label: "Agents" },
  { slug: "library", label: "Library" },
  { slug: "inbox", label: "Inbox" },
  { slug: "tools", label: "Tools" },
  { slug: "projects", label: "Projects" },
  { slug: "todos", label: "Todos" },
  { slug: "connectors", label: "Connectors" },
  { slug: "team", label: "Team" },
  { slug: "memory", label: "Memory" },
  { slug: "routines", label: "Routines" },
];

export const DEFAULT_SHELL_DESTINATION: ShellDestinationSlug = "home";
