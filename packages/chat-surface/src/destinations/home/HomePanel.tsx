// Home — right-side context panel (Phase 9 rewrite).
//
// Sub-PRD §3.2. Phase 9 drops StarredProjectsSection (replaced by
// InFlightStrip in the main column). HomePanel is now a single section:
// QuickActions. If the product later wants to drop HomePanel entirely
// (collapse Quick Actions into the main TriageStrip area), that is a
// one-file change.
//
// Pure-presentation. No transport, no router, no fetch — the data
// binder (P9-C) hands the `homeResponse` payload down; this component
// reads only `quick_actions`.

import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import type { HomePayload, QuickActionTarget } from "@0x-copilot/api-types";

import { ContextPanel } from "../../shell/ContextPanel";

import { HomeQuickActionsSection } from "./sections";

export interface HomePanelProps {
  /**
   * Same payload the `<HomeDestination>` consumes. The panel reads only
   * `quick_actions`; when `null` (loading), the panel renders a quiet
   * skeleton.
   */
  readonly homeResponse?: HomePayload | null;

  /**
   * Host-supplied router shim for quick-action click-through. The
   * `QuickActionTarget` discriminator (`chat_new` / `todo_new` /
   * `routine_new` / `tools_onboard` / `team_invite`) tells the host
   * which creation flow to open.
   */
  readonly onQuickActionSelect?: (target: QuickActionTarget) => void;
}

export function HomePanel(props: HomePanelProps = {}): ReactElement {
  const { homeResponse = null, onQuickActionSelect } = props;

  // === Loading state ====================================================
  if (homeResponse === null) {
    return (
      <ContextPanel title="Home" subtitle="Loading…" destination="home">
        <div data-testid="home-panel-loading" aria-hidden="true">
          <PanelSectionSkeleton title="Quick start" rows={4} />
        </div>
      </ContextPanel>
    );
  }

  // === Ready state ======================================================
  return (
    <ContextPanel title="Home" destination="home">
      <div data-testid="home-panel">
        <PanelSectionWrapper
          testId="home-panel-section-quick-actions"
          title="Quick start"
        >
          <HomeQuickActionsSection
            actions={homeResponse.quick_actions}
            onSelect={onQuickActionSelect}
          />
        </PanelSectionWrapper>
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// Section wrapper — small visual frame inside the panel
// ===========================================================================

interface PanelSectionWrapperProps {
  readonly testId: string;
  readonly title: string;
  readonly children: ReactNode;
}

function PanelSectionWrapper({
  testId,
  title,
  children,
}: PanelSectionWrapperProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    padding: "12px 14px",
    borderBottom: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted)",
    margin: 0,
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  return (
    <section style={wrapperStyle} data-testid={testId}>
      <h3 style={titleStyle}>{title}</h3>
      {children}
    </section>
  );
}

// ===========================================================================
// Panel skeleton
// ===========================================================================

interface PanelSectionSkeletonProps {
  readonly title: string;
  readonly rows: number;
}

function PanelSectionSkeleton({
  title,
  rows,
}: PanelSectionSkeletonProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    padding: "12px 14px",
    borderBottom: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted)",
    margin: 0,
    textTransform: "uppercase",
    letterSpacing: 0.4,
    opacity: 0.6,
  };
  const rowStyle: CSSProperties = {
    height: 18,
    borderRadius: 4,
    backgroundColor: "var(--color-surface-muted)",
    opacity: 0.5,
  };
  return (
    <div style={wrapperStyle}>
      <div style={titleStyle}>{title}</div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={rowStyle} data-testid="home-panel-skeleton-row" />
      ))}
    </div>
  );
}
