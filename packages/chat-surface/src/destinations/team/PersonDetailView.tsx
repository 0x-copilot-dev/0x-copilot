// PersonDetailView — `/team/<id>` detail page.
//
// Source: team-memory-cmdk-prd.md §7.1. Tabs:
//   Overview / Agents / Projects / Activity (admin) / Settings (admin)
//
// Pure presentation. Receives the `PersonDetailResponse` shape from the
// host. Admin-only tabs (Activity, Settings) render only when `isAdmin`.
// The Activity tab renders `recent_activity` via <ActivityList> only when
// non-empty — the sub-PRD §3.1 contract says non-admin callers get an
// empty array, so the tab UI also collapses for an empty admin view.
//
// Cross-destination links: agents / projects render as <ItemLink>
// (cross-audit §3.3) so the destination shell never reaches for the
// router directly.

import { useState, type CSSProperties, type ReactElement } from "react";

import type { PersonDetailResponse, TeamRole } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { itemKindNoun } from "../../refs/itemKindNoun";
import { ActivityList, type ActivityRow } from "../../shell/ActivityList";
import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";
import { formatRelativeTime } from "../../util/time";

export type PersonDetailTabId =
  | "overview"
  | "agents"
  | "projects"
  | "activity"
  | "settings";

const ROLE_LABEL: Readonly<Record<TeamRole, string>> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

const PRESENCE_LABEL: Readonly<Record<string, string>> = {
  active: "Active",
  away: "Away",
  in_meeting: "In meeting",
  offline: "Offline",
};

export interface PersonDetailViewProps {
  /** Detail payload; `null` while loading. */
  readonly detail: PersonDetailResponse | null;
  /** Admin gate — drives the Activity + Settings tabs. */
  readonly isAdmin: boolean;
  /** Frozen reference time for relative-time formatting; defaults to now. */
  readonly now?: number;

  readonly initialTab?: PersonDetailTabId;
  readonly activeTab?: PersonDetailTabId;
  readonly onTabChange?: (tab: PersonDetailTabId) => void;

  /** Admin-only — change member's role. */
  readonly onChangeRole?: (next: TeamRole) => void;
  /** Admin-only — open the OffboardingWizard. */
  readonly onOpenOffboarding?: () => void;
  /** Optional close — host may use to dismiss the detail panel. */
  readonly onClose?: () => void;
}

const TAB_DEFS: ReadonlyArray<{
  readonly id: PersonDetailTabId;
  readonly label: string;
  readonly adminOnly: boolean;
}> = [
  { id: "overview", label: "Overview", adminOnly: false },
  { id: "agents", label: "Agents", adminOnly: false },
  { id: "projects", label: "Projects", adminOnly: false },
  { id: "activity", label: "Activity", adminOnly: true },
  { id: "settings", label: "Settings", adminOnly: true },
];

export function PersonDetailView({
  detail,
  isAdmin,
  now,
  initialTab = "overview",
  activeTab,
  onTabChange,
  onChangeRole,
  onOpenOffboarding,
  onClose,
}: PersonDetailViewProps): ReactElement {
  const [internalTab, setInternalTab] = useState<PersonDetailTabId>(initialTab);
  const current = activeTab ?? internalTab;

  const handleTabSelect = (next: PersonDetailTabId): void => {
    if (onTabChange !== undefined) onTabChange(next);
    else setInternalTab(next);
  };

  if (detail === null) {
    return (
      <section
        data-testid="person-detail-skeleton"
        aria-busy="true"
        style={containerStyle}
      >
        <div style={skeletonRowStyle} />
        <div style={skeletonRowStyle} />
        <div style={skeletonRowStyle} />
      </section>
    );
  }

  const { person, agents, projects, recent_activity } = detail;

  const visibleTabs = TAB_DEFS.filter((t) => isAdmin || !t.adminOnly);

  return (
    <section
      style={containerStyle}
      data-testid="person-detail-view"
      data-person-id={person.id}
      aria-label={`Person detail — ${person.display_name}`}
    >
      <PageHeader
        title={person.display_name}
        subtitle={`${ROLE_LABEL[person.role]} · ${
          PRESENCE_LABEL[person.presence] ?? person.presence
        } · ${person.email}`}
        primaryAction={
          onClose !== undefined
            ? { label: "Close", onClick: onClose }
            : undefined
        }
      />

      <div
        role="tablist"
        aria-label="Person detail"
        style={tablistStyle}
        data-testid="person-detail-tablist"
      >
        {visibleTabs.map((t) => {
          const active = t.id === current;
          return (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={active}
              aria-controls={`person-detail-panel-${t.id}`}
              id={`person-detail-tab-${t.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => handleTabSelect(t.id)}
              style={tabStyle(active)}
              data-testid={`person-detail-tab-${t.id}`}
              data-active={active}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`person-detail-panel-${current}`}
        aria-labelledby={`person-detail-tab-${current}`}
        style={panelStyle}
        data-testid={`person-detail-tabpanel-${current}`}
      >
        {current === "overview" ? (
          <OverviewTab detail={detail} now={now} />
        ) : null}
        {current === "agents" ? (
          <RefListTab
            refs={agents}
            emptyTitle="No agents"
            emptyBody={`${person.display_name} doesn't own any agents yet.`}
            testIdPrefix="person-detail-agents"
          />
        ) : null}
        {current === "projects" ? (
          <RefListTab
            refs={projects}
            emptyTitle="No projects"
            emptyBody={`${person.display_name} doesn't own any projects yet.`}
            testIdPrefix="person-detail-projects"
          />
        ) : null}
        {current === "activity" && isAdmin ? (
          <ActivityTab activity={recent_activity} now={now} />
        ) : null}
        {current === "settings" && isAdmin ? (
          <SettingsTab
            currentRole={person.role}
            onChangeRole={onChangeRole}
            onOpenOffboarding={onOpenOffboarding}
            disableSelfDemote={person.is_self}
          />
        ) : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

interface OverviewTabProps {
  readonly detail: PersonDetailResponse;
  readonly now?: number;
}

function OverviewTab({ detail, now }: OverviewTabProps): ReactElement {
  const { person } = detail;
  const lastSeen =
    person.last_seen_at !== null
      ? formatRelativeTime(person.last_seen_at, now)
      : "Never connected";
  const joined = formatRelativeTime(person.joined_at, now);
  return (
    <div data-testid="person-detail-overview" style={overviewGridStyle}>
      <Field label="Email" value={person.email} />
      <Field label="Role" value={ROLE_LABEL[person.role]} />
      <Field
        label="Presence"
        value={PRESENCE_LABEL[person.presence] ?? person.presence}
      />
      <Field label="Last seen" value={lastSeen} />
      <Field label="Joined" value={joined} />
      <Field label="Agents" value={String(person.agents_count)} />
      <Field label="Projects" value={String(person.projects_count)} />
    </div>
  );
}

interface FieldProps {
  readonly label: string;
  readonly value: string;
}

function Field({ label, value }: FieldProps): ReactElement {
  return (
    <div
      style={fieldStyle}
      data-testid={`person-detail-field-${label.toLowerCase()}`}
    >
      <div style={fieldLabelStyle}>{label}</div>
      <div style={fieldValueStyle}>{value}</div>
    </div>
  );
}

interface RefListTabProps {
  readonly refs: PersonDetailResponse["agents"];
  readonly emptyTitle: string;
  readonly emptyBody: string;
  readonly testIdPrefix: string;
}

function RefListTab({
  refs,
  emptyTitle,
  emptyBody,
  testIdPrefix,
}: RefListTabProps): ReactElement {
  if (refs.length === 0) {
    return (
      <div data-testid={`${testIdPrefix}-empty`}>
        <EmptyState title={emptyTitle} body={emptyBody} />
      </div>
    );
  }
  return (
    <ul style={refListStyle} data-testid={`${testIdPrefix}-list`}>
      {refs.map((ref) => (
        <li
          key={`${ref.kind}:${ref.id}`}
          style={refRowStyle}
          data-testid={`${testIdPrefix}-row`}
          data-item-kind={ref.kind}
        >
          <ItemLink ref={ref} label={itemKindNoun(ref.kind)} />
        </li>
      ))}
    </ul>
  );
}

interface ActivityTabProps {
  readonly activity: PersonDetailResponse["recent_activity"];
  readonly now?: number;
}

function ActivityTab({ activity, now }: ActivityTabProps): ReactElement {
  // Per sub-PRD §3.1 — non-admin callers receive an empty array; the
  // admin gate above means this component only renders when isAdmin.
  // We still empty-state for admin-with-no-activity so the tab is honest.
  if (activity.length === 0) {
    return (
      <div data-testid="person-detail-activity-empty">
        <EmptyState
          title="No recent activity"
          body="Nothing has happened on this person's account in the audit window."
        />
      </div>
    );
  }
  const rows: ReadonlyArray<ActivityRow> = activity.map((entry, idx) => ({
    key: `${entry.at}-${idx}`,
    ref: entry.target,
    timestamp: entry.at,
    context: entry.summary,
  }));
  return (
    <div data-testid="person-detail-activity-list">
      <ActivityList rows={rows} now={now} ariaLabel="Recent activity" />
    </div>
  );
}

interface SettingsTabProps {
  readonly currentRole: TeamRole;
  readonly onChangeRole?: (next: TeamRole) => void;
  readonly onOpenOffboarding?: () => void;
  /** Self-demote guard hint — disables the role select when viewing self
   *  (server enforces the invariant; this is UI courtesy). */
  readonly disableSelfDemote: boolean;
}

function SettingsTab({
  currentRole,
  onChangeRole,
  onOpenOffboarding,
  disableSelfDemote,
}: SettingsTabProps): ReactElement {
  const roles: ReadonlyArray<TeamRole> = ["owner", "admin", "member", "guest"];
  return (
    <div data-testid="person-detail-settings" style={settingsBlockStyle}>
      <div style={fieldStyle}>
        <div style={fieldLabelStyle}>Role</div>
        <select
          value={currentRole}
          onChange={(e) => onChangeRole?.(e.target.value as TeamRole)}
          disabled={onChangeRole === undefined || disableSelfDemote}
          aria-disabled={onChangeRole === undefined || disableSelfDemote}
          aria-label="Member role"
          style={selectStyle}
          data-testid="person-detail-role-select"
        >
          {roles.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
        {disableSelfDemote ? (
          <div style={hintStyle}>
            You can't change your own role. Ask another admin.
          </div>
        ) : null}
      </div>
      <div style={fieldStyle}>
        <div style={fieldLabelStyle}>Offboarding</div>
        <button
          type="button"
          onClick={onOpenOffboarding}
          disabled={onOpenOffboarding === undefined}
          style={dangerButtonStyle}
          data-testid="person-detail-offboard-trigger"
        >
          Offboard this teammate…
        </button>
        <div style={hintStyle}>
          Opens the controlled-handoff wizard. Choose a new owner for each asset
          the teammate owns. Atlas never force-transfers.
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  width: "100%",
  height: "100%",
  minHeight: 0,
  boxSizing: "border-box",
};

const tablistStyle: CSSProperties = {
  display: "flex",
  gap: 4,
  borderBottom: "1px solid var(--color-border, #232325)",
};

function tabStyle(active: boolean): CSSProperties {
  return {
    height: 36,
    padding: "0 14px",
    borderRadius: 0,
    border: "none",
    background: "transparent",
    color: active
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: active ? 600 : 500,
    cursor: "pointer",
    borderBottom: active
      ? "2px solid var(--color-accent, #d97757)"
      : "2px solid transparent",
    marginBottom: -1,
  };
}

const panelStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
  padding: "8px 0",
};

const overviewGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
  gap: 12,
};

const fieldStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const fieldLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const fieldValueStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
};

const refListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const refRowStyle: CSSProperties = {
  padding: "8px 10px",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-bg-elevated, #18181b)",
};

const settingsBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const selectStyle: CSSProperties = {
  height: 32,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  width: 200,
};

const dangerButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-danger, #d97777)",
  background: "transparent",
  color: "var(--color-danger, #d97777)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
  width: "fit-content",
};

const hintStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const skeletonRowStyle: CSSProperties = {
  height: 18,
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-surface-muted, #222224)",
  opacity: 0.5,
};
