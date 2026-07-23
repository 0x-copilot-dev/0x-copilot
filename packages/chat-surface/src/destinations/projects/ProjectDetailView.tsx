// ProjectDetailView — P6-B2
//
// Pure presentation of a single project's detail page. Mutations
// (member add/remove/role change, ownership transfer, tab-row clicks)
// are surfaced via callbacks; the host owns transport, fetch, and
// router. The view never calls transport or router directly — that
// keeps it substrate-agnostic and reusable from both the web app and
// the desktop substrate (per chat-surface SP-1 invariants).
//
// Tab model (Projects sub-PRD §3 + cross-audit §1.3, extended by
// Phase 4 FR-4.11):
//   Chats / Files / Todos / Inbox / Library / Routines / Members / Activity
// The five cross-destination tabs (Chats, Todos, Inbox, Library,
// Routines) render whatever the host injects via
// `renderCrossDestinationTab` — the host is the only thing that knows
// how to issue the `filter[project_id]=<id>` list call for the relevant
// destination. We pass tab id + project id and let the host return the
// list view. Chat rows opened from that slot navigate to Run
// (`ItemLink kind="run"`) — the host wires that.
//
// Files (Phase 4 FR-4.11/4.12), Members, and Activity tabs are owned
// here. The Files tab takes a projected `SectionResult<ProjectFileRow[]>`
// and renders the shared 4-state machine; each file row opens its
// artifact via `<ItemLink kind="library_file">`. When no files source is
// wired (the project files endpoint does not exist yet — PRD §11), the
// tab degrades to a "coming soon" empty state, never an error.

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

import { EmptyState } from "../../shell/EmptyState";
import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";
import { SectionHeader } from "../_shared";

import type {
  LibraryFileId,
  ProjectId,
  SectionResult,
} from "@0x-copilot/api-types";

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
  /** Optional short description shown under the name (v3 solo header). */
  readonly description?: string;
  readonly status: ProjectStatus;
  readonly ownerUserId: string;
  readonly ownerName: string;
  readonly memberCount: number;
  /** Optional chat count for the solo "Chats · N" section header. */
  readonly chatCount?: number;
  /** Optional file count for the solo "Files · M" section header. */
  readonly fileCount?: number;
}

/**
 * Surface profile (FR-G.5). The default solo profile renders the v3
 * tab-less detail — a colour-tile header over `.sect-h` "Chats · N" /
 * "Files · M" sections. The team profile keeps the full eight-tab model
 * (Chats / Files / Todos / Inbox / Library / Routines / Members /
 * Activity), gated rather than deleted, for the multi-user ACL product.
 */
export type ProjectDetailProfile = "solo" | "team";

/** The eight tabs in the project detail view. The five cross-destination
 *  tabs (chats/todos/inbox/library/routines) proxy to other destinations
 *  (host owns the actual fetch). `files`, `members`, and `activity` are
 *  owned by this view. */
export type ProjectDetailTabId =
  | "chats"
  | "files"
  | "todos"
  | "inbox"
  | "library"
  | "routines"
  | "members"
  | "activity";

/**
 * A single file/artifact attached to a project. Minimal presentational
 * row: the detail view renders an `<ItemLink kind="library_file">` from
 * `id`, so opening a file navigates to its artifact route.
 *
 * NON-branded on purpose — there is no `ProjectFileRow` wire contract in
 * `@0x-copilot/api-types` yet (the `/v1/projects/{id}/files` endpoint does
 * not exist — PRD §11). `id` is cast to the existing `LibraryFileId` brand
 * at the `<ItemLink>` boundary, so no `__brand` type is re-declared here.
 *
 * TODO(api-types): promote to a shared `ProjectFileRow` contract in
 * `@0x-copilot/api-types` once the project files endpoint lands.
 */
export interface ProjectFileRow {
  /** Opaque file/artifact id (the `<ItemLink kind="library_file">` target). */
  readonly id: string;
  /** File name shown in the row. */
  readonly name: string;
  /** Optional short kind/descriptor ("PDF", "Doc", "Dataset"). Display-only. */
  readonly fileKind?: string;
  /** Optional ISO timestamp; relative-time formatted at render. */
  readonly updatedAt?: string;
  /** Optional human-readable size ("1.2 MB"). Display-only. */
  readonly sizeLabel?: string;
}

/** Files-tab data. Uniform `SectionResult` wrapper so the tab renders the
 *  same 4-state machine as the other list surfaces (FR-4.2). `null` =
 *  loading; the prop being omitted entirely = no source wired ("coming
 *  soon"). */
export type ProjectFilesResult = SectionResult<ReadonlyArray<ProjectFileRow>>;

export interface ProjectDetailViewProps {
  readonly project: ProjectDetail;

  /**
   * Surface profile (FR-G.5). Defaults to `"solo"` — the v3 tab-less
   * detail with `.sect-h` Chats / Files sections. Pass `"team"` to keep
   * the eight-tab model for the multi-user ACL product.
   */
  readonly profile?: ProjectDetailProfile;

  /** Members tab data. Pass `null` while loading. */
  readonly members: ReadonlyArray<ProjectMember> | null;

  /** Activity tab data. Pass `null` while loading. */
  readonly activity: ReadonlyArray<ProjectActivity> | null;

  /**
   * Files tab data (FR-4.11/4.12).
   *   - omitted / `undefined` → no source wired: "coming soon" empty state
   *     (the project files endpoint does not exist yet — PRD §11). Never
   *     an error.
   *   - `null` → loading skeleton.
   *   - `SectionResult` → the 4-state machine (error+Retry / unavailable /
   *     empty / ready). Each ready row opens its artifact via
   *     `<ItemLink kind="library_file">`.
   */
  readonly files?: ProjectFilesResult | null;

  /** Retry callback when `files.status === "error"`. */
  readonly onRetryFiles?: () => void;

  /** Reference instant — test seam for relative-time formatting on file
   *  rows. Defaults to `Date.now()`. */
  readonly now?: number;

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
  { id: "files", label: "Files" },
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
  colorHue,
  name,
}: {
  colorHue?: number;
  name: string;
}): ReactElement {
  // v3 design (FR-G.5): the header tile is the project colour + the name's
  // first letter — NOT the emoji — so the detail matches the `.grid3` card
  // tile on the list.
  const initial = (name.trim()[0] ?? "?").toUpperCase();
  const bg =
    colorHue !== undefined
      ? `hsl(${colorHue} 60% 28% / 0.45)`
      : "var(--color-border-strong)";
  const border =
    colorHue !== undefined
      ? `hsl(${colorHue} 60% 50% / 0.55)`
      : "var(--color-border)";
  const fg =
    colorHue !== undefined ? `hsl(${colorHue} 70% 82%)` : "var(--color-text)";
  const style: CSSProperties = {
    width: 44,
    height: 44,
    borderRadius: 10,
    backgroundColor: bg,
    border: `1px solid ${border}`,
    color: fg,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "var(--font-size-xl)",
    fontWeight: 700,
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
      {initial}
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
    fontSize: "var(--font-size-xl)",
    fontWeight: 600,
    color: TEXT_PRIMARY,
    display: "flex",
    alignItems: "center",
    gap: 10,
    flexWrap: "wrap",
  };
  const metaStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    color: TEXT_SECONDARY,
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const pillStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
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
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    cursor: "pointer",
  };

  // Status pill via inline element to keep the file standalone. Colours
  // come from design-system status tokens (PRD-B) — never hard-coded rgb;
  // `statusToneFor` maps the project status to the tone attribute the
  // tests assert on (active→running, paused→ready, archived→idle).
  const statusBg =
    project.status === "active"
      ? "var(--color-success-bg)"
      : project.status === "paused"
        ? "var(--color-warning-bg)"
        : "var(--color-surface-muted)";
  const statusFg =
    project.status === "active"
      ? "var(--color-success)"
      : project.status === "paused"
        ? "var(--color-warning)"
        : "var(--color-text-subtle)";
  const statusPill: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
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
      <ProjectIconTile colorHue={project.colorHue} name={project.name} />
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
        {project.description !== undefined && project.description.length > 0 ? (
          <p
            data-testid="project-detail-description"
            style={{
              margin: 0,
              fontSize: "var(--font-size-sm)",
              color: TEXT_SECONDARY,
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {project.description}
          </p>
        ) : null}
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
    fontSize: "var(--font-size-sm)",
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

// ── Files tab ────────────────────────────────────────────────────────
//
// Owned here (like Members / Activity). Renders the shared 4-state
// machine over a `SectionResult<ProjectFileRow[]>`; each ready row opens
// its artifact via `<ItemLink kind="library_file">` (FR-4.12). When no
// source is wired (`files === undefined`) it degrades to a "coming soon"
// empty state — never an error (FR-4.11, PRD §11 files gap).
//
// No member/role chips are rendered here: `viewer_role` gating lives on
// the project card (ProjectsDestination), and the files section is
// deliberately chip-free so nothing member-scoped leaks under the solo
// profile (FR-4.13).

interface ProjectFilesTabProps {
  readonly files?: ProjectFilesResult | null;
  readonly onRetry?: () => void;
  readonly now?: number;
}

function ProjectFilesTab({
  files,
  onRetry,
  now,
}: ProjectFilesTabProps): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const list: CSSProperties = {
    listStyle: "none",
    padding: 0,
    margin: 0,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const skeletonRow: CSSProperties = {
    height: 52,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    opacity: 0.6,
  };

  // No source wired → "coming soon" (endpoint absent). Never an error.
  if (files === undefined) {
    return (
      <section
        data-testid="project-files-tab"
        data-state="unavailable"
        style={wrapper}
      >
        <EmptyState
          title="Project files coming soon"
          body="This workspace doesn't expose a project files list yet. Chats and activity for the project are available above."
        />
      </section>
    );
  }

  // Loading skeleton.
  if (files === null) {
    return (
      <section
        data-testid="project-files-tab"
        data-state="loading"
        style={wrapper}
      >
        <ul style={list} aria-busy="true">
          {Array.from({ length: 4 }).map((_, i) => (
            <li
              key={i}
              style={skeletonRow}
              data-testid="project-files-skeleton"
              aria-hidden="true"
            />
          ))}
        </ul>
      </section>
    );
  }

  // Error → EmptyState + Retry.
  if (files.status === "error") {
    return (
      <section
        data-testid="project-files-tab"
        data-state="error"
        style={wrapper}
      >
        <EmptyState
          title="Could not load files"
          body={files.error ?? "Network error — try again."}
          action={
            onRetry !== undefined
              ? { label: "Retry", onClick: onRetry }
              : undefined
          }
        />
      </section>
    );
  }

  // Unavailable → distinct "not enabled" empty state.
  if (files.status === "unavailable") {
    return (
      <section
        data-testid="project-files-tab"
        data-state="unavailable"
        style={wrapper}
      >
        <EmptyState
          title="Project files unavailable"
          body={files.error ?? "File listing is not enabled for this project."}
        />
      </section>
    );
  }

  const rows = files.data ?? [];

  // Ready + empty → per-view empty copy.
  if (rows.length === 0) {
    return (
      <section
        data-testid="project-files-tab"
        data-state="empty"
        style={wrapper}
      >
        <EmptyState
          title="No files yet"
          body="Files attached to this project will appear here."
        />
      </section>
    );
  }

  // Ready + rows.
  return (
    <section data-testid="project-files-tab" data-state="ready" style={wrapper}>
      <ul style={list} data-testid="project-files-list">
        {rows.map((row) => (
          <ProjectFileRowView key={row.id} row={row} now={now} />
        ))}
      </ul>
    </section>
  );
}

function ProjectFileRowView({
  row,
  now,
}: {
  row: ProjectFileRow;
  now?: number;
}): ReactElement {
  const li: CSSProperties = {
    padding: "10px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 10,
    backgroundColor: PANEL_BACKGROUND,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  };
  const leftCol: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minWidth: 0,
    flex: 1,
  };
  const nameStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    fontWeight: 500,
    color: TEXT_PRIMARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    color: TEXT_SECONDARY,
    display: "flex",
    alignItems: "center",
    gap: 8,
  };
  const rightCol: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    flexShrink: 0,
  };
  const tsStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    color: "var(--color-text-subtle)",
  };

  // Cast the plain-string id to the existing `LibraryFileId` brand at the
  // <ItemLink> boundary — the artifact/file resolver is registered under
  // kind `"library_file"` (packages/chat-surface/src/destinations/library).
  // No new brand is declared here. The file name is shown as the primary
  // text (the row's display hint); the <ItemLink> is the sanctioned
  // navigational affordance that opens the artifact route (FR-4.12) —
  // direct `router.navigate` from a row is forbidden (cross-audit §1.1).
  const fileRef = {
    kind: "library_file" as const,
    id: row.id as LibraryFileId,
  };

  return (
    <li
      style={li}
      data-testid="project-file-row"
      data-file-id={row.id}
      data-ref-kind={fileRef.kind}
      data-ref-id={row.id}
    >
      <div style={leftCol}>
        <span
          style={nameStyle}
          data-testid="project-file-row-name"
          title={row.name}
        >
          {row.name}
        </span>
        {row.fileKind !== undefined || row.sizeLabel !== undefined ? (
          <span style={subStyle} data-testid="project-file-row-sub">
            {row.fileKind !== undefined ? (
              <span data-testid="project-file-row-kind">{row.fileKind}</span>
            ) : null}
            {row.sizeLabel !== undefined ? (
              <span data-testid="project-file-row-size">{row.sizeLabel}</span>
            ) : null}
          </span>
        ) : null}
      </div>
      <div style={rightCol}>
        {row.updatedAt !== undefined ? (
          <time
            style={tsStyle}
            dateTime={row.updatedAt}
            data-testid="project-file-row-time"
          >
            {formatRelativeTime(row.updatedAt, now)}
          </time>
        ) : null}
        <span data-testid="project-file-row-open">
          <ItemLink ref={fileRef} label={row.name} />
        </span>
      </div>
    </li>
  );
}

// ── Main view ────────────────────────────────────────────────────────

export function ProjectDetailView(props: ProjectDetailViewProps): ReactElement {
  const {
    project,
    profile = "solo",
    members,
    activity,
    files,
    onRetryFiles,
    now,
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
    if (active === "files") {
      return (
        <div
          role="tabpanel"
          id="project-tab-panel-files"
          aria-labelledby="project-tab-files"
          style={panelStyle}
          data-testid="project-detail-panel-files"
        >
          <ProjectFilesTab files={files} onRetry={onRetryFiles} now={now} />
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
    files,
    members,
    now,
    onAddMember,
    onChangeMemberRole,
    onRemoveMember,
    onRetryFiles,
    panelStyle,
    project.id,
    project.ownerUserId,
    renderCrossDestinationTab,
  ]);

  // Solo profile (FR-G.5): tab-less detail — the colour-tile header over
  // `.sect-h` "Chats · N" / "Files · M" sections. Chats reuses the host's
  // cross-destination slot (it owns the `filter[project_id]` list); Files
  // reuses the same `ProjectFilesTab` 4-state machine (which degrades to a
  // "coming soon" empty state when no source is wired).
  const soloSections = (
    <div
      data-testid="project-detail-sections"
      style={{ display: "flex", flexDirection: "column", gap: 20 }}
    >
      <section
        aria-labelledby="project-section-chats"
        data-testid="project-detail-section-chats"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        <SectionHeader
          headingId="project-section-chats"
          count={project.chatCount}
        >
          Chats
        </SectionHeader>
        {renderCrossDestinationTab("chats", project.id)}
      </section>
      <section
        aria-labelledby="project-section-files"
        data-testid="project-detail-section-files"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        <SectionHeader
          headingId="project-section-files"
          count={project.fileCount}
        >
          Files
        </SectionHeader>
        <ProjectFilesTab files={files} onRetry={onRetryFiles} now={now} />
      </section>
    </div>
  );

  return (
    <section
      aria-label={`Project ${project.name}`}
      data-testid="project-detail-view"
      data-active-tab={active}
      data-profile={profile}
      style={root}
    >
      <div style={container}>
        <ProjectDetailHeader
          project={project}
          canManage={canManage}
          onRequestTransferOwnership={onRequestTransferOwnership}
        />
        {profile === "team" ? (
          <>
            <TabsBar active={active} onSelect={handleSelectTab} />
            {tabPanel}
          </>
        ) : (
          soloSections
        )}
      </div>
    </section>
  );
}

export { ProjectFilesTab };
export type { ProjectMember, ProjectMemberRole } from "./ProjectMembersTab";
export type { ProjectActivity } from "./ProjectActivityTab";
