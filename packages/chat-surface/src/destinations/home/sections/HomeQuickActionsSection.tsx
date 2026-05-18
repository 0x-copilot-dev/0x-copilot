// <HomeQuickActionsSection> — the only section in <HomePanel> after the
// Phase 9 redesign. Sub-PRD §3.2.
//
// Quick actions are server-driven (admin role / plan tier / tenant
// overrides), discriminated by `QuickActionTarget.kind`. The host owns
// the click → route resolution; pure-presentation passes through.
//
// Empty state shows the "no quick actions yet" copy via <EmptyState>
// (SP-1 primitive). No bespoke "Connect a tool" CTA — that is a
// concrete quick-action kind delivered by the server.

import type { CSSProperties, ReactElement } from "react";

import type {
  QuickAction,
  QuickActionTarget,
} from "@enterprise-search/api-types";

import { EmptyState } from "../../../shell/EmptyState";

export interface HomeQuickActionsSectionProps {
  readonly actions: ReadonlyArray<QuickAction>;
  /**
   * Host-supplied router shim. Given a `QuickActionTarget`, the host
   * navigates / opens the corresponding flow.
   */
  readonly onSelect?: (target: QuickActionTarget) => void;
}

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const buttonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  padding: "8px 10px",
  background: "transparent",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-sm, 6px)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-sm, 13px)",
  cursor: "pointer",
  textAlign: "left",
  font: "inherit",
};

export function HomeQuickActionsSection({
  actions,
  onSelect,
}: HomeQuickActionsSectionProps): ReactElement {
  if (actions.length === 0) {
    return (
      <EmptyState
        title="No quick actions yet"
        body="An admin can configure quick actions for this workspace."
      />
    );
  }
  return (
    <ul
      style={listStyle}
      data-testid="home-quick-actions"
      aria-label="Quick actions"
    >
      {actions.map((action) => (
        <li key={action.id}>
          <button
            type="button"
            style={buttonStyle}
            data-testid="home-quick-action"
            data-quick-action-id={action.id}
            data-target-kind={action.target.kind}
            onClick={() => onSelect?.(action.target)}
          >
            {action.label}
          </button>
        </li>
      ))}
    </ul>
  );
}
