// Re-export shim for the workspace pane tab-label pluralize helper.
//
// The helper now lives in @0x-copilot/chat-surface (PR-1.7) with the pane it
// serves. It is a pure string helper, so this is a pure re-export; existing
// import sites keep resolving `pluralize` / `tabLabel` / `TAB_LABELS` here.

export {
  pluralize,
  tabLabel,
  TAB_LABELS,
  type LabelForms,
} from "@0x-copilot/chat-surface";
