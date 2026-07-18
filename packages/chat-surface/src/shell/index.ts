export { AppRail, APP_RAIL_WIDTH, type AppRailProps } from "./AppRail";
export {
  ContextPanel,
  CONTEXT_PANEL_WIDTH,
  type ContextPanelProps,
  type ContextPanelPrimaryAction,
  type ContextPanelSearch,
} from "./ContextPanel";
export { Topbar, TOPBAR_HEIGHT, type TopbarProps } from "./Topbar";
export {
  RightRail,
  RIGHT_RAIL_WIDTH,
  type RightRailProps,
  type RightRailTabId,
} from "./RightRail";
export {
  ActivityTabContent,
  type ActivityTabContentProps,
} from "./ActivityTabContent";
export {
  ApprovalsTabContent,
  type ApprovalsTabContentProps,
  type ApprovalsFilter,
} from "./ApprovalsTabContent";
export { ChatShell, type ChatShellProps } from "./ChatShell";
export {
  DEFAULT_SHELL_DESTINATION,
  SHELL_DESTINATIONS,
  defaultDestinationForProfile,
  destinationsForProfile,
  type ShellDestination,
  type ShellDestinationSlug,
} from "./destinations";
// === Phase 0.5 shared primitives ===
export {
  PageHeader,
  type PageHeaderPrimaryAction,
  type PageHeaderProps,
} from "./PageHeader";
export {
  FilterTabs,
  type FilterTabOption,
  type FilterTabsProps,
} from "./FilterTabs";
export {
  StatusPill,
  type StatusPillProps,
  type StatusTone,
} from "./StatusPill";
export {
  EmptyState,
  type EmptyStateAction,
  type EmptyStateProps,
} from "./EmptyState";
export { CardGrid, type CardGridProps } from "./CardGrid";
export { DocList } from "./DocList";
export {
  ActivityList,
  type ActivityListProps,
  type ActivityRow,
} from "./ActivityList";
// === end Phase 0.5 ===

// === Placeholder for destinations not yet built in the redesign ===
export {
  DestinationPlaceholder,
  type DestinationPlaceholderBridge,
  type DestinationPlaceholderProps,
} from "./DestinationPlaceholder";

// === Phase 6 — global ⌘K command palette ===
export { CommandPalette, type CommandPaletteProps } from "./CommandPalette";
export {
  CommandPaletteTrigger,
  type CommandPaletteTriggerProps,
} from "./CommandPaletteTrigger";
export { PaletteHitRow, type PaletteHitRowProps } from "./PaletteHitRow";
export {
  useCommandPaletteHotkey,
  type UseCommandPaletteHotkeyOptions,
} from "./useCommandPaletteHotkey";
// === end Phase 6 command palette ===

// === Phase 6 — shell keyboard shortcuts (SSOT table + hook) ===
export {
  SHELL_SHORTCUTS,
  matchesChord,
  normalizeShortcutKey,
  type ShellShortcut,
  type ShellShortcutCallbacks,
  type ShellShortcutMap,
  type ShortcutChord,
  type ShortcutIntent,
  type ShortcutKeyEvent,
  type ShortcutScope,
  type UseShellShortcutsOptions,
} from "./shortcuts";
export { useShellShortcuts } from "./useShellShortcuts";
// === end Phase 6 shortcuts ===
