// PR 8.0.1 — pluralize helper for workspace pane tab labels.
//
// One-line rule: if the count is 1, render the singular form; otherwise
// render the plural. Centralised here so every consumer that pairs a
// label with a count (Sources / Agents / Approvals / Skills / Members /
// Invitations) follows the same grammar.

/** Singular when count === 1, plural otherwise. */
export function pluralize(
  singular: string,
  plural: string,
  count: number,
): string {
  return count === 1 ? singular : plural;
}

/**
 * Convenience for label objects so callers don't repeat the pair
 * inline at every call site.
 */
export interface LabelForms {
  readonly singular: string;
  readonly plural: string;
}

export function tabLabel(forms: LabelForms, count: number): string {
  return pluralize(forms.singular, forms.plural, count);
}

export const TAB_LABELS = {
  sources: { singular: "Source", plural: "Sources" },
  agents: { singular: "Agent", plural: "Agents" },
  approval: { singular: "Approval", plural: "Approvals" },
  skill: { singular: "Skill", plural: "Skills" },
} as const satisfies Record<string, LabelForms>;
