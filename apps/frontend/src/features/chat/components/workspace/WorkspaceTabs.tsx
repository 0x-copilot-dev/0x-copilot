// Re-export shim for the Workspace pane tablist.
//
// The WAI-ARIA tablist now lives in @0x-copilot/chat-surface (PR-1.7) with the
// pane it serves. It is a pure presentational primitive (roving tabindex,
// arrow/Home/End keyboard nav), so this is a pure re-export; existing import
// sites keep resolving `WorkspaceTabs` / `workspaceTabPanelId` here.

export {
  WorkspaceTabs,
  workspaceTabPanelId,
  type WorkspaceTabsItem,
  type WorkspaceTabsProps,
} from "@0x-copilot/chat-surface";
