// Re-export shim for the parallel-batch subagent fleet card.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.5). It is a pure
// presentational shell (head counts + footer + a `children` slot the host
// fills with `FleetSubagentRow`s), so this is a plain re-export; existing
// import sites keep resolving `SubagentFleetCard` from here.

export {
  SubagentFleetCard,
  type SubagentFleetCardProps,
} from "@0x-copilot/chat-surface";
