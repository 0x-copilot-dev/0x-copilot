// ProjectDetailView — P6-B2
//
// Pure presentation of a single project's detail page. Mutations
// (member add/remove/role change, ownership transfer, tab-row clicks)
// are surfaced via callbacks; the host owns transport, fetch, and
// router. The view never calls transport or router directly — that
// keeps it substrate-agnostic and reusable from both the web app and
// the desktop substrate (per chat-surface SP-1 invariants).
//
// Tab model (Projects sub-PRD §3 + cross-audit §1.3):
//   Chats / Todos / Inbox / Library / Routines / Members / Activity
// The five cross-destination tabs (Chats..Routines) render whatever
// the host injects via `renderCrossDestinationTab` — the host is the
// only thing that knows how to issue the `filter[project_id]=<id>`
// list call for the relevant destination. We pass tab id + project
// id and let the host return the list view.
//
// Members + Activity tabs are owned here (split into their own files
// for clarity).

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { ProjectActivityTab, type ProjectActivity } from "./ProjectActivityTab";
import {
  ProjectMembersTab,
  type ProjectMember,
  type ProjectMemberRole,
} from "./ProjectMembersTab";

import type { ProjectId } from "./ProjectsDestination";

// Tokens (match ProjectsDestination.tsx)
const APP_BACKGROUND = "var(--color-bg)";
const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const ACCENT = "var(--color-accent)";

// ── Public types ─────────────────────────────────────────────────────

/** Status for a project (from destinations-master-prd §5.4). The pill
 *  in the header is the only consumer today; values stay loose so the
 *  host can keep the source of truth. */
export type ProjectStatus = "active" | "archived" | "paused";

export interface ProjectDetail {
  readonly id: ProjectId;
  readonly name: string;
  /** Optional emoji used as the project icon (chat1.md model). */
  readonly iconEmoji?: string;
  /** Optional color hue (0..359) used for the icon tile background. */
  readonly colorHue?: number;
  readonly status: ProjectStatus;
  readonly ownerUserId: string;
  readonly ownerName: string;
  readonly memberCount: number;
}

/** The seven tabs in the project detail view. The first five proxy to
 *  other destinations (host owns the actual fetch). */
export type ProjectDetailTabId =
  | "chats"
  | "todos"
  | "inbox"
  | "library"
  | "routines"
  | "members"
  | "activity";

export interface ProjectDetailViewProps {
  readonly project: ProjectDetail;

  /** Members tab data. Pass `null` while loading. */
  readonly members: ReadonlyArray<ProjectMember> | null;

  /** Activity tab data. Pass `null` while loading. */
  readonly activity: ReadonlyArray<ProjectActivity> | null;

  /** Whether the current viewer can mutate the project (owner / admin).
   *  Drives visibility of member-management + transfer-ownership UI. */
  readonly canManage: boolean;

  /** Optional initial tab. Default: "chats". */
  readonly initialTab?: ProjectDetailTabId;

  /** Controlled tab. If provided, takes precedence over internal state.
   *  Pair with `onTabChange` to drive the view from a parent. */
  readonly activeTab?: ProjectDetailTabId;
  readonly onTabChange?: (tab: ProjectDetailTabId) => void;

  /** Render slot for the five cross-destination tabs. The host knows
   *  how to fetch the list (`filter[project_id]=<id>`) and returns a
   *  ReactNode to embed. Called only when one of those tabs is active. */
  readonly renderCrossDestinationTab: (
    tab: "chats" | "todos" | "inbox" | "library" | "routines",
    projectId: ProjectId,
  ) => ReactNode;

  // Members tab callbacks (forwarded)
  readonly onAddMember?: (
    userIdentifier: string,
    role: ProjectMemberRole,
  ) => Promise<void>;
  readonly onRemoveMember?: (userId: string) => Promise<void>;
  readonly onChangeMemberRole?: (
    userId: string,
    role: ProjectMemberRole,
  ) => Promise<void>;

  // Transfer ownership: optional render slot for the dialog.
  // The host typically supplies <TransferOwnershipDialog/> wired to
  // its own transport. We surface the trigger here; the dialog opens
  // when the trigger callback fires.
  readonly onRequestTransferOwnership?: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────────

const TAB_DEFS: ReadonlyArray<{
  readonly id: ProjectDetailTabId;
  readonly label: string;
}> = [
  { id: "chats", label: "Chats" },
  { id: "todos", label: "Todos" },
  { id: "inbox", label: "Inbox" },
  { id: "library", label: "Library" },
  { id: "routines", label: "Routines" },
  { id: "members", label: "Members" },
  { id: "activity", label: "Activity" },
];

function statusToneFor(status: ProjectStatus): "running" | "ready" | "idle" {
  // StatusPill from design-system carries three tones. Map project
  // status → tone for visual cue only (no semantic claim beyond
  // active = "running", archived = "idle", paused = "ready").
  switch (status) {
    case "active":
      return "running";
    case "paused":
      return "ready";
    case "archived":
      return "idle";
  }
}

function statusLabelFor(status: ProjectStatus): string {
  switch (status) {
    case "active":
      return "Active";
    case "paused":
      return "Paused";
    case "archived":
      return "Archived";
  }
}

function ProjectIconTile({
  emoji,
  colorHue,
  name,
}: {
  emoji?: string;
  colorHue?: number;
  name: string;
}): ReactElement {
  const bg =
    colorHue !== undefined
      ? `hsl(${colorHue} 60% 28% / 0.45)`
      : "var(--color-border-strong)";
  const border =
    colorHue !== undefined
      ? `hsl(${colorHue} 60% 50% / 0.55)`
      : "var(--color-border)";
  const style: CSSProperties = {
    width: 44,
    height: 44,
    borderRadius: 10,
    backgroundColor: bg,
    border: `1px solid ${border}`,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 22,
    flexShrink: 0,
  };
  return (
    <div
      style={style}
      role="img"
      aria-label={`${name} icon`}
      data-testid="project-detail-icon"
      data-color-hue={colorHue ?? ""}
    >
      {emoji !== undefined && emoji.length > 0 ? emoji : "📁"}
    </div>
  );
}

// ── Header ───────────────────────────────────────────────────────────

interface HeaderProps {
  readonly project: ProjectDetail;
  readonly canManage: boolean;
  readonly onRequestTransferOwnership?: () => void;
}

function ProjectDetailHeader({
  project,
  canManage,
  onRequestTransferOwnership,
}: HeaderProps): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 14,
    paddingBottom: 16,
    borderBottom: `1px solid ${PANEL_BORDER}`,
  };
  const titleBlock: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    flex: 1,
    minWidth: 0,
  };
  const nameStyle: CSSProperties = {
    fontSize: 20,
    fontWeight: 600,
    color: TEXT_PRIMARY,
    display: "flex",
    alignItems: "center",
    gap: 10,
    flexWrap: "wrap",
  };
  const metaStyle: CSSProperties = {
    fontSize: 13,
    color: TEXT_SECONDARY,
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const pillStyle: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 999,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_SECONDARY,
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
  };
  const transferBtn: CSSProperties = {
    height: 32,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };

  // Status pill via inline element to keep the file standalone — the
  // design-system StatusPill carries three tones and we map them above.
  // We render an inline pill here that mirrors the DS look so the file
  // has no hard dependency on CSS class definitions during unit tests.
  const statusBg =
    project.status === "active"
      ? "rgba(34,197,94,0.12)"
      : project.status === "paused"
        ? "rgba(245,158,11,0.12)"
        : "rgba(148,163,184,0.12)";
  const statusFg =
    project.status === "active"
      ? "rgb(74,222,128)"
      : project.status === "paused"
        ? "rgb(251,191,36)"
        : "rgb(148,163,184)";
  const statusPill: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    padding: "2px 10px",
    borderRadius: 999,
    backgroundColor: statusBg,
    color: statusFg,
    border: `1px solid ${statusFg}`,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };

  return (
    <header
      style={wrapper}
      data-testid="project-detail-header"
      data-project-id={project.id}
    >
      <ProjectIconTile
        emoji={project.iconEmoji}
        colorHue={project.colorHue}
        name={project.name}
      />
      <div style={titleBlock}>
        <div style={nameStyle}>
          <span
            data-testid="project-detail-name"
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {project.name}
          </span>
          <span
            style={statusPill}
            data-testid="project-detail-status"
            data-status={project.status}
            aria-label={`Status: ${statusLabelFor(project.status)}`}
            data-tone={statusToneFor(project.status)}
          >
            <span
              aria-hidden="true"
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                backgroundColor: statusFg,
              }}
            />
            {statusLabelFor(project.status)}
          </span>
        </div>
        <div style={metaStyle}>
          <span
            style={pillStyle}
            data-testid="project-detail-owner"
            data-owner-user-id={project.ownerUserId}
          >
            Owner: {project.ownerName}
          </span>
          <span style={pillStyle} data-testid="project-detail-member-count">
            {project.memberCount} member
            {project.memberCount === 1 ? "" : "s"}
          </span>
        </div>
      </div>
      {canManage && onRequestTransferOwnership !== undefined ? (
        <button
          type="button"
          style={transferBtn}
          onClick={onRequestTransferOwnership}
          data-testid="project-detail-transfer-trigger"
          aria-label="Transfer ownership"
        >
          Transfer ownership
        </button>
      ) : null}
    </header>
  );
}

// ── Tabs bar ─────────────────────────────────────────────────────────

interface TabsBarProps {
  readonly active: ProjectDetailTabId;
  readonly onSelect: (id: ProjectDetailTabId) => void;
}

function TabsBar({ active, onSelect }: TabsBarProps): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    gap: 4,
    borderBottom: `1px solid ${PANEL_BORDER}`,
    overflowX: "auto",
  };
  const tabBase: CSSProperties = {
    height: 36,
    padding: "0 14px",
    borderRadius: "8px 8px 0 0",
    border: "none",
    background: "transparent",
    color: TEXT_SECONDARY,
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    borderBottom: "2px solid transparent",
  };
  return (
    <div
      role="tablist"
      aria-label="Project sections"
      style={wrapper}
      data-testid="project-detail-tabs"
    >
      {TAB_DEFS.map(({ id, label }) => {
        const isActive = id === active;
        const style: CSSProperties = {
          ...tabBase,
          color: isActive ? TEXT_PRIMARY : TEXT_SECONDARY,
          borderBottomColor: isActive ? ACCENT : "transparent",
          fontWeight: isActive ? 600 : 500,
        };
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={`project-tab-panel-${id}`}
            id={`project-tab-${id}`}
            data-testid={`project-detail-tab-${id}`}
            data-tab-active={isActive ? "true" : "false"}
            style={style}
            onClick={() => onSelect(id)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────

export function ProjectDetailView(props: ProjectDetailViewProps): ReactElement {
  const {
    project,
    members,
    activity,
    canManage,
    initialTab,
    activeTab: controlledTab,
    onTabChange,
    renderCrossDestinationTab,
    onAddMember,
    onRemoveMember,
    onChangeMemberRole,
    onRequestTransferOwnership,
  } = props;

  const [uncontrolledTab, setUncontrolledTab] = useState<ProjectDetailTabId>(
    initialTab ?? "chats",
  );
  const active = controlledTab ?? uncontrolledTab;

  const handleSelectTab = (next: ProjectDetailTabId): void => {
    if (controlledTab === undefined) setUncontrolledTab(next);
    onTabChange?.(next);
  };

  const root: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: APP_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const container: CSSProperties = {
    width: "100%",
    maxWidth: 1000,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const panelStyle: CSSProperties = {
    paddingTop: 16,
    minHeight: 200,
  };

  const tabPanel = useMemo<ReactElement>(() => {
    if (
      active === "chats" ||
      active === "todos" ||
      active === "inbox" ||
      active === "library" ||
      active === "routines"
    ) {
      return (
        <div
          role="tabpanel"
          id={`project-tab-panel-${active}`}
          aria-labelledby={`project-tab-${active}`}
          style={panelStyle}
          data-testid={`project-detail-panel-${active}`}
        >
          {renderCrossDestinationTab(active, project.id)}
        </div>
      );
    }
    if (active === "members") {
      return (
        <div
          role="tabpanel"
          id="project-tab-panel-members"
          aria-labelledby="project-tab-members"
          style={panelStyle}
          data-testid="project-detail-panel-members"
        >
          <ProjectMembersTab
            members={members}
            canManage={canManage}
            ownerUserId={project.ownerUserId}
            onAddMember={onAddMember}
            onRemoveMember={onRemoveMember}
            onChangeMemberRole={onChangeMemberRole}
          />
        </div>
      );
    }
    // activity
    return (
      <div
        role="tabpanel"
        id="project-tab-panel-activity"
        aria-labelledby="project-tab-activity"
        style={panelStyle}
        data-testid="project-detail-panel-activity"
      >
        <ProjectActivityTab activity={activity} />
      </div>
    );
  }, [
    active,
    activity,
    canManage,
    members,
    onAddMember,
    onChangeMemberRole,
    onRemoveMember,
    panelStyle,
    project.id,
    project.ownerUserId,
    renderCrossDestinationTab,
  ]);

  return (
    <section
      aria-label={`Project ${project.name}`}
      data-testid="project-detail-view"
      data-active-tab={active}
      style={root}
    >
      <div style={container}>
        <ProjectDetailHeader
          project={project}
          canManage={canManage}
          onRequestTransferOwnership={onRequestTransferOwnership}
        />
        <TabsBar active={active} onSelect={handleSelectTab} />
        {tabPanel}
      </div>
    </section>
  );
}

export type { ProjectMember, ProjectMemberRole } from "./ProjectMembersTab";
export type { ProjectActivity } from "./ProjectActivityTab";
