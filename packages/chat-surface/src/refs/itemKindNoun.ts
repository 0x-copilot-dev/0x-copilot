// itemKindNoun — the display noun for an `ItemRef` whose CALLER holds only an
// id (PRD-04 Seam A, Non-goals).
//
// After Seam A `<ItemLink label={…}>` is required: the caller states what the
// link renders. Most call sites already hold the entity's real name (a chat
// subject, a file name, a project name) and pass THAT. A handful genuinely hold
// only `{ kind, id }` — a caller ref in an audit row, a run-history row, a
// cross-ref chip. Those sites pass `itemKindNoun(ref.kind)`: a VISIBLE, LOCAL
// fallback noun. A visible local noun beats the old invisible GLOBAL lie — the
// registry constant that shadowed real data even where the caller had it.
//
// This is deliberately a display fallback, not a resolver: it produces no
// route, participates in no registry, and cannot shadow a real name (the sites
// that have one never call it). Denormalizing those id-only wire rows to carry
// a real name is future work (PRD-04 Non-goals), and becomes purely additive
// now that `label` is caller-owned.

import type { ItemKind } from "@0x-copilot/api-types";

const NOUNS: Readonly<Record<ItemKind, string>> = {
  chat: "Chat",
  run: "Run",
  subagent: "Subagent",
  tool_result: "Tool result",
  todo: "Todo",
  inbox_item: "Inbox item",
  project: "Project",
  library_file: "File",
  library_page: "Page",
  library_dataset: "Dataset",
  agent: "Agent",
  tool: "Tool",
  skill: "Skill",
  connector: "Connector",
  person: "Person",
  memory: "Memory",
  routine: "Routine",
  approval: "Approval",
  meeting_external: "Meeting",
};

/** The display noun for an `ItemKind` at a call site that holds only an id. */
export function itemKindNoun(kind: ItemKind): string {
  return NOUNS[kind];
}
