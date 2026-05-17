// Home — right-side context panel (P2-B1).
//
// Per sub-PRD §3.2, Home's panel is sparse: starred projects + quick
// actions. The two section bodies are owned by P2-B3 (sections B);
// this file is the panel *shell* — it composes ContextPanel from
// `../../shell` and routes data through to the section components.
//
// `<HomePanel>` keeps the destination panel substrate-agnostic: no
// fetching, no router calls. The host supplies `homeResponse` (same
// payload the destination consumes) and the panel reads
// `starred_projects` + `quick_actions` from it.
//
// `_home-stub.ts` carries the wire types until P2-A1's api-types merge.

import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { ContextPanel } from "../../shell/ContextPanel";
import { EmptyState } from "../../shell/EmptyState";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type {
  HomePayload,
  QuickAction,
  StarredProjectSummary,
} from "./_home-stub";

// ===========================================================================
// Public props
// ===========================================================================

export interface HomePanelProps {
  /**
   * Same payload the `<HomeDestination>` consumes. The panel reads only
   * `starred_projects` + `quick_actions`; when `null` (loading), the
   * panel renders a quiet skeleton.
   */
  readonly homeResponse?: HomePayload | null;

  /**
   * Optional retry callback for the starred-projects section when its
   * `SectionResult.status === "error"`. P2-C wires this to
   * `GET /v1/home?refresh_section=starred_projects`.
   */
  readonly onRetryStarredProjects?: () => void;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function HomePanel(props: HomePanelProps = {}): ReactElement {
  const { homeResponse = null, onRetryStarredProjects } = props;

  // === Loading state ====================================================
  if (homeResponse === null) {
    return (
      <ContextPanel title="Home" subtitle="Loading…" destination="home">
        <div data-testid="home-panel-loading" aria-hidden="true">
          <PanelSectionSkeleton title="Starred projects" rows={3} />
          <PanelSectionSkeleton title="Quick start" rows={4} />
        </div>
      </ContextPanel>
    );
  }

  // === Ready state ======================================================
  return (
    <ContextPanel title="Home" destination="home">
      <div data-testid="home-panel">
        {/* §3.2.1 Starred projects */}
        <PanelSectionWrapper
          testId="home-panel-section-starred-projects"
          title="Starred projects"
        >
          <StarredProjectsBody
            result={homeResponse.starred_projects}
            onRetry={onRetryStarredProjects}
          />
        </PanelSectionWrapper>

        {/* §3.2.2 Quick start */}
        <PanelSectionWrapper
          testId="home-panel-section-quick-actions"
          title="Quick start"
        >
          <QuickActionsBody actions={homeResponse.quick_actions} />
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
    borderBottom: "1px solid var(--color-border, #232325)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted, #b4b4b8)",
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
// §3.2.1 Starred projects body (P2-B3 sections-B replaces the OK render)
// ===========================================================================

interface StarredProjectsBodyProps {
  readonly result: SectionResult<ReadonlyArray<StarredProjectSummary>>;
  readonly onRetry?: () => void;
}

function StarredProjectsBody({
  result,
  onRetry,
}: StarredProjectsBodyProps): ReactElement {
  if (result.status === "error") {
    return (
      <EmptyState
        title="Couldn't load starred projects"
        body={result.error}
        action={
          onRetry !== undefined
            ? { label: "Retry section", onClick: onRetry }
            : undefined
        }
      />
    );
  }
  if (result.status === "unavailable") {
    return (
      <EmptyState
        title="Projects coming soon"
        body={
          result.error ??
          "Star a project to keep it here once the Projects destination is enabled."
        }
      />
    );
  }
  const projects = (result.data ?? []) as ReadonlyArray<StarredProjectSummary>;
  if (projects.length === 0) {
    return (
      <EmptyState
        title="No starred projects yet"
        body="Star a project to keep it here."
      />
    );
  }
  return (
    <div
      data-testid="home-panel-starred-projects-content"
      data-section-status="ok"
      data-section-count={projects.length}
    >
      {/* TODO(P2-B3): replace with <HomeStarredProjectsSection projects={projects} />. */}
      {`${projects.length} starred project${projects.length === 1 ? "" : "s"} — section component lands in P2-B3`}
    </div>
  );
}

// ===========================================================================
// §3.2.2 Quick actions body (P2-B3 sections-B replaces the OK render)
// ===========================================================================

interface QuickActionsBodyProps {
  readonly actions: ReadonlyArray<QuickAction>;
}

function QuickActionsBody({ actions }: QuickActionsBodyProps): ReactElement {
  if (actions.length === 0) {
    return (
      <EmptyState
        title="No quick actions yet"
        body="An admin can configure quick actions for this workspace."
      />
    );
  }
  return (
    <div
      data-testid="home-panel-quick-actions-content"
      data-section-count={actions.length}
    >
      {/* TODO(P2-B3): replace with <HomeQuickActionsSection actions={actions} />. */}
      {`${actions.length} quick action${actions.length === 1 ? "" : "s"} — section component lands in P2-B3`}
    </div>
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
    borderBottom: "1px solid var(--color-border, #232325)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted, #b4b4b8)",
    margin: 0,
    textTransform: "uppercase",
    letterSpacing: 0.4,
    opacity: 0.6,
  };
  const rowStyle: CSSProperties = {
    height: 18,
    borderRadius: 4,
    backgroundColor: "var(--color-surface-muted, #222224)",
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
