// Tools destination — UI-side view-model helpers (P10-B1).
//
// Source: tools-prd §1.6 (status semantics) + §4.12 (filter / sort
// allowlist) + §7.2 (destination components).
//
// SCOPE: presentation-only. No transport, no router, no service calls.
// The destination consumes the canonical `Tool` wire shape directly from
// `@0x-copilot/api-types`; this file carries:
//
//   - the catalog filter axis slugs + labels (the master filter axis
//     "My / Installed / Available / Custom / By kind" — tools-prd §7.2)
//   - the sort allowlist (tools-prd §4.12)
//   - the kind / scope / status display labels (single source so the
//     destination + panel + card share one vocabulary)
//   - the `statusTone(...)` mapper (StatusTone ← ToolStatus)
//   - pure `filterTools(...)` and `searchTools(...)` helpers
//
// Re-exports `Tool`, `ToolKind`, `ToolScope`, `ToolStatus` from api-types
// so consumers (the card, the panel, the destination, tests, and the
// future data-binder phase) have one import path.

import type {
  Tool,
  ToolKind,
  ToolScope,
  ToolStatus,
} from "@0x-copilot/api-types";

import type { StatusTone } from "../../shell/StatusPill";

// Re-export wire types so the rest of the surface has one import.
export type { Tool, ToolKind, ToolScope, ToolStatus };

// ===========================================================================
// Catalog filter axis (tools-prd §7.2)
// ===========================================================================
//
// The destination header tablist mirrors the App-Store / agents gallery
// shape: "My / Installed / Available / Custom / By kind". "By kind"
// is the secondary axis — when selected, the panel surfaces the kind
// chips and the destination filters by the active kind.

export type ToolsFilterSlug =
  | "my"
  | "installed"
  | "available"
  | "custom"
  | "by_kind";

export const TOOLS_FILTER_ORDER: ReadonlyArray<ToolsFilterSlug> = [
  "my",
  "installed",
  "available",
  "custom",
  "by_kind",
];

export const TOOLS_FILTER_LABELS: Readonly<Record<ToolsFilterSlug, string>> = {
  my: "My",
  installed: "Installed",
  available: "Available",
  custom: "Custom",
  by_kind: "By kind",
};

// ===========================================================================
// Sort allowlist (tools-prd §4.12)
// ===========================================================================

export type ToolsSortSlug =
  | "name_asc"
  | "calls_30d_desc"
  | "last_used_desc"
  | "created_at_desc";

export const TOOLS_SORT_ORDER: ReadonlyArray<ToolsSortSlug> = [
  "name_asc",
  "calls_30d_desc",
  "last_used_desc",
  "created_at_desc",
];

export const TOOLS_SORT_LABELS: Readonly<Record<ToolsSortSlug, string>> = {
  name_asc: "Name (A-Z)",
  calls_30d_desc: "Most used (30d)",
  last_used_desc: "Recently used",
  created_at_desc: "Newest",
};

// ===========================================================================
// Kind / scope / status display vocabulary
// ===========================================================================
//
// Single source for chip labels — every consumer (card, panel,
// destination) reads from here.

export const TOOLS_KIND_ORDER: ReadonlyArray<ToolKind> = [
  "mcp",
  "openapi",
  "builtin",
  "code",
  "skill",
];

export const TOOLS_KIND_LABELS: Readonly<Record<ToolKind, string>> = {
  mcp: "MCP",
  openapi: "API",
  builtin: "Built-in",
  code: "Code",
  skill: "Skill",
};

export const TOOLS_SCOPE_ORDER: ReadonlyArray<ToolScope> = [
  "read",
  "write",
  "both",
];

export const TOOLS_SCOPE_LABELS: Readonly<Record<ToolScope, string>> = {
  read: "Read",
  write: "Write",
  both: "Read+Write",
};

export const TOOLS_STATUS_LABELS: Readonly<Record<ToolStatus, string>> = {
  enabled: "Enabled",
  disabled: "Disabled",
  error: "Error",
  pending_review: "Pending review",
};

// ===========================================================================
// Status-tone mapping (tools-prd §1.6 → SP-1 StatusPill tone)
// ===========================================================================
//
// Mapping is intentional — `enabled` reads "ok" (success), `error`
// is loud (danger), `pending_review` and `disabled` are softer because
// they're holding-pattern states. SINGLE definition; never inlined.

export function statusTone(status: ToolStatus): StatusTone {
  switch (status) {
    case "enabled":
      return "ok";
    case "error":
      return "error";
    case "pending_review":
      return "warning";
    case "disabled":
      return "muted";
  }
}

// ===========================================================================
// "Installed" semantics
// ===========================================================================
//
// The wire shape doesn't carry an explicit `installed` flag — a tool
// is callable iff `status === "enabled"`. The filter axis collapses
// "Installed" to that predicate. This keeps the wire single-source and
// matches tools-prd §1.6 ("`enabled` — installed, scope reviewed,
// callable by every grant").

export function isInstalled(tool: Tool): boolean {
  return tool.status === "enabled";
}

// ===========================================================================
// Filter + sort + search (pure)
// ===========================================================================

export interface ToolsFilterContext {
  /** Active filter axis (drives the master view). */
  readonly filter: ToolsFilterSlug;
  /** When `filter === "by_kind"`, restricts to a single kind. Null = all kinds. */
  readonly kindFilter: ToolKind | null;
  /** Current viewer's user id; used by "Custom" / "My" semantics. */
  readonly currentUserId: string | null;
}

/**
 * Pure filter — given a candidate set + context, return the tools that
 * should be rendered. Lifted out of the destination so tests can pin the
 * matrix without rendering.
 *
 * Semantics:
 *   - my       → tools the current user owns AND installed (status=enabled)
 *   - installed→ everything with status=enabled
 *   - available→ status=disabled OR pending_review (not callable yet)
 *   - custom   → kind=code OR kind=skill (user-authored)
 *   - by_kind  → all tools matching `kindFilter` (null = no narrowing)
 */
export function filterTools(
  tools: ReadonlyArray<Tool>,
  ctx: ToolsFilterContext,
): ReadonlyArray<Tool> {
  switch (ctx.filter) {
    case "my": {
      const uid = ctx.currentUserId;
      if (uid === null) return tools.filter(isInstalled);
      return tools.filter((t) => isInstalled(t) && t.owner_user_id === uid);
    }
    case "installed":
      return tools.filter(isInstalled);
    case "available":
      return tools.filter((t) => !isInstalled(t));
    case "custom":
      return tools.filter((t) => t.kind === "code" || t.kind === "skill");
    case "by_kind":
      if (ctx.kindFilter === null) return tools;
      return tools.filter((t) => t.kind === ctx.kindFilter);
  }
}

/**
 * Pure search — case-insensitive over name + description + tags. Lifted
 * for the same reason as `filterTools`.
 */
export function searchTools(
  tools: ReadonlyArray<Tool>,
  query: string,
): ReadonlyArray<Tool> {
  const needle = query.trim().toLowerCase();
  if (needle === "") return tools;
  return tools.filter((t) => {
    const hay = `${t.name} ${t.description} ${t.tags.join(" ")}`.toLowerCase();
    return hay.includes(needle);
  });
}

/**
 * Pure sort — stable order across the four allowlisted axes
 * (tools-prd §4.12). Returns a NEW array; never mutates the input.
 */
export function sortTools(
  tools: ReadonlyArray<Tool>,
  sort: ToolsSortSlug,
): ReadonlyArray<Tool> {
  const copy = tools.slice();
  switch (sort) {
    case "name_asc":
      return copy.sort((a, b) =>
        a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
      );
    case "calls_30d_desc":
      return copy.sort((a, b) => b.usage.calls_30d - a.usage.calls_30d);
    case "last_used_desc":
      return copy.sort((a, b) => {
        const aT =
          a.usage.last_used_at === null ? 0 : Date.parse(a.usage.last_used_at);
        const bT =
          b.usage.last_used_at === null ? 0 : Date.parse(b.usage.last_used_at);
        return bT - aT;
      });
    case "created_at_desc":
      return copy.sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      );
  }
}

// ===========================================================================
// Kind tiles (empty-state)
// ===========================================================================
//
// tools-prd §7.4: when the catalog is empty, the EmptyState surfaces
// four onboarding tiles, each a deep-link to the wizard at
// `/tools/onboard/<kind>`. Tiles include `mcp`, `openapi`, `code`, and
// `skill` — `builtin` is omitted because users can't onboard a built-in
// (Atlas ships those).

export interface KindOnboardTile {
  readonly kind: Exclude<ToolKind, "builtin">;
  readonly label: string;
  readonly description: string;
  readonly icon: string;
}

export const ONBOARD_KIND_TILES: ReadonlyArray<KindOnboardTile> = [
  {
    kind: "mcp",
    label: "MCP server",
    description: "Install an MCP server and expose its tools.",
    icon: "MCP",
  },
  {
    kind: "openapi",
    label: "OpenAPI",
    description: "Onboard a REST API via its OpenAPI document.",
    icon: "API",
  },
  {
    kind: "code",
    label: "Code routine",
    description: "Write a deterministic function that runs in a sandbox.",
    icon: "</>",
  },
  {
    kind: "skill",
    label: "Skill",
    description: "Promote a Library skill page into a callable tool.",
    icon: "SK",
  },
];
