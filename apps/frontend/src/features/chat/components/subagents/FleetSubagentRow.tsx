// Re-export shim for the compact parallel-fleet row.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.5). Its elapsed
// timer was rewritten to the bare `setInterval` global (FR-1.30) so the row
// is substrate-portable; existing import sites keep resolving
// `FleetSubagentRow` from here.

export {
  FleetSubagentRow,
  type FleetSubagentRowProps,
} from "@0x-copilot/chat-surface";
