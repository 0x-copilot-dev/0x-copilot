// <NotificationsPage /> — Settings → Notifications.
//
// Source: team-memory-cmdk-prd.md §7.4 (Settings pages) + §U-S1
// (per-user defaults) + §U-S2 (workspace defaults, admin gate).
//
// SP-1 primitives: PageHeader + FilterTabs (My defaults / Workspace
// defaults). Workspace tab is admin-gated via the `isAdmin` prop — when
// `false` the page renders a single panel ("My defaults") and does NOT
// render the tablist at all (no disabled tab leak, no admin tab in the
// AT tree).
//
// Submission semantics: `onSave` receives the PATCH body and is called
// only when there are dirty fields. Diff carries only changed fields
// (`destinations_enabled` is only included if any toggle differs from
// the initial value; same for `quiet_hours`).
//
// This page is pure presentation: NO transport, NO routing, NO fetch.
// The host (apps/frontend) wires the GET / PATCH against the facade.

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type ReactElement,
} from "react";

import type {
  NotificationDefaults,
  NotificationQuietHoursBlob,
  PerDestinationToggle,
  UpdateNotificationDefaultsRequest,
  UpdateWorkspaceNotificationDefaultsRequest,
  WorkspaceNotificationDefaults,
} from "@enterprise-search/api-types";

import { FilterTabs, type FilterTabOption } from "../shell/FilterTabs";
import { PageHeader } from "../shell/PageHeader";

import { QuietHoursEditor, validateQuietHoursWindow } from "./QuietHoursEditor";

// ---------------------------------------------------------------------------
// Public destination slug ordering (sub-PRD §7.4).
//
// We mirror the prompt-prescribed list here. New destinations append
// without breaking the wire (`PerDestinationToggle` is open-ended).
// ---------------------------------------------------------------------------

export interface DestinationRowDescriptor {
  readonly slug: string;
  readonly label: string;
}

export const NOTIFICATION_DESTINATION_ROWS: ReadonlyArray<DestinationRowDescriptor> =
  [
    { slug: "chats", label: "Chats" },
    { slug: "runs", label: "Runs" },
    { slug: "approvals", label: "Approvals" },
    { slug: "inbox", label: "Inbox" },
    { slug: "routines", label: "Routines" },
    { slug: "library", label: "Library" },
    { slug: "agents", label: "Agents" },
    { slug: "tools", label: "Tools" },
    { slug: "connectors", label: "Connectors" },
    { slug: "team", label: "Team" },
    { slug: "memory", label: "Memory" },
  ];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type NotificationsPageTabSlug = "my" | "workspace";

export interface NotificationsPageProps {
  readonly myDefaults: NotificationDefaults;
  readonly workspaceDefaults: WorkspaceNotificationDefaults | null;
  readonly isAdmin: boolean;
  readonly onSaveMy: (patch: UpdateNotificationDefaultsRequest) => void;
  readonly onSaveWorkspace?: (
    patch: UpdateWorkspaceNotificationDefaultsRequest,
  ) => void;
  /** Rows to render. Defaults to `NOTIFICATION_DESTINATION_ROWS`. */
  readonly destinationRows?: ReadonlyArray<DestinationRowDescriptor>;
  /** TZ options forwarded to the QuietHoursEditor. */
  readonly tzOptions?: ReadonlyArray<string>;
}

// ---------------------------------------------------------------------------
// Diff helpers
// ---------------------------------------------------------------------------

function destinationsDiff(
  initial: PerDestinationToggle,
  next: PerDestinationToggle,
  rows: ReadonlyArray<DestinationRowDescriptor>,
): PerDestinationToggle | undefined {
  const out: Record<string, boolean> = {};
  let changed = false;
  for (const row of rows) {
    const initVal = initial[row.slug] !== false; // missing → true (sub-PRD note)
    const nextVal = next[row.slug] !== false;
    if (initVal !== nextVal) {
      out[row.slug] = nextVal;
      changed = true;
    }
  }
  // Preserve unknown keys from `next` that differ from `initial`.
  for (const key of Object.keys(next)) {
    if (rows.some((r) => r.slug === key)) continue;
    if (next[key] !== initial[key]) {
      out[key] = next[key] as boolean;
      changed = true;
    }
  }
  return changed ? out : undefined;
}

function quietHoursDiff(
  initial: NotificationQuietHoursBlob,
  next: NotificationQuietHoursBlob,
): NotificationQuietHoursBlob | undefined {
  if (
    initial.enabled === next.enabled &&
    initial.from_local === next.from_local &&
    initial.to_local === next.to_local &&
    initial.tz === next.tz
  ) {
    return undefined;
  }
  return next;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const fieldsetStyle: CSSProperties = {
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const legendStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  padding: "0 6px",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const labelStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
};

const saveBarStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
};

const saveButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

// ---------------------------------------------------------------------------
// Per-tab body
// ---------------------------------------------------------------------------

interface NotificationsBodyProps {
  readonly idPrefix: string;
  readonly destinations: PerDestinationToggle;
  readonly setDestinations: (next: PerDestinationToggle) => void;
  readonly quietHours: NotificationQuietHoursBlob;
  readonly setQuietHours: (next: NotificationQuietHoursBlob) => void;
  readonly rows: ReadonlyArray<DestinationRowDescriptor>;
  readonly tzOptions?: ReadonlyArray<string>;
  readonly onSubmit: () => void;
  readonly disabled: boolean;
}

function NotificationsBody({
  idPrefix,
  destinations,
  setDestinations,
  quietHours,
  setQuietHours,
  rows,
  tzOptions,
  onSubmit,
  disabled,
}: NotificationsBodyProps): ReactElement {
  const handleToggle = useCallback(
    (slug: string, e: ChangeEvent<HTMLInputElement>) => {
      setDestinations({ ...destinations, [slug]: e.target.checked });
    },
    [destinations, setDestinations],
  );

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      onSubmit();
    },
    [onSubmit],
  );

  return (
    <form style={formStyle} onSubmit={handleSubmit} aria-labelledby={idPrefix}>
      <fieldset style={fieldsetStyle}>
        <legend style={legendStyle}>Notify me about</legend>
        {rows.map((row) => {
          const enabled = destinations[row.slug] !== false;
          const inputId = `${idPrefix}-toggle-${row.slug}`;
          return (
            <div key={row.slug} style={rowStyle}>
              <label htmlFor={inputId} style={labelStyle}>
                {row.label}
              </label>
              <input
                id={inputId}
                type="checkbox"
                checked={enabled}
                onChange={(e) => handleToggle(row.slug, e)}
                data-testid={`notify-toggle-${row.slug}`}
              />
            </div>
          );
        })}
      </fieldset>
      <QuietHoursEditor
        value={quietHours}
        onChange={setQuietHours}
        tzOptions={tzOptions}
      />
      <div style={saveBarStyle}>
        <button
          type="submit"
          style={{
            ...saveButtonStyle,
            opacity: disabled ? 0.6 : 1,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
          disabled={disabled}
          aria-disabled={disabled}
          data-testid="notifications-save"
        >
          Save changes
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function NotificationsPage({
  myDefaults,
  workspaceDefaults,
  isAdmin,
  onSaveMy,
  onSaveWorkspace,
  destinationRows,
  tzOptions,
}: NotificationsPageProps): ReactElement {
  const rows = destinationRows ?? NOTIFICATION_DESTINATION_ROWS;
  const reactId = useId();
  const idPrefix = `notifications-${reactId}`;

  // ---- My-defaults state ----
  const [myDest, setMyDest] = useState<PerDestinationToggle>(
    myDefaults.destinations_enabled,
  );
  const [myQuiet, setMyQuiet] = useState<NotificationQuietHoursBlob>(
    myDefaults.quiet_hours,
  );
  useEffect(() => {
    setMyDest(myDefaults.destinations_enabled);
    setMyQuiet(myDefaults.quiet_hours);
  }, [myDefaults]);

  // ---- Workspace-defaults state (lazily seeded) ----
  const [wsDest, setWsDest] = useState<PerDestinationToggle>(
    workspaceDefaults?.destinations_enabled ?? {},
  );
  const [wsQuiet, setWsQuiet] = useState<NotificationQuietHoursBlob>(
    workspaceDefaults?.quiet_hours ?? {
      enabled: false,
      from_local: "22:00",
      to_local: "07:00",
      tz: "UTC",
    },
  );
  useEffect(() => {
    if (workspaceDefaults !== null) {
      setWsDest(workspaceDefaults.destinations_enabled);
      setWsQuiet(workspaceDefaults.quiet_hours);
    }
  }, [workspaceDefaults]);

  // ---- Active tab ----
  const [activeTab, setActiveTab] = useState<NotificationsPageTabSlug>("my");

  // ---- Save callbacks (diff-only) ----
  const handleSaveMy = useCallback(() => {
    if (
      validateQuietHoursWindow(myQuiet.from_local, myQuiet.to_local) !== null
    ) {
      return;
    }
    const patch: UpdateNotificationDefaultsRequest = {};
    const ddiff = destinationsDiff(
      myDefaults.destinations_enabled,
      myDest,
      rows,
    );
    if (ddiff !== undefined) {
      (
        patch as { destinations_enabled?: PerDestinationToggle }
      ).destinations_enabled = ddiff;
    }
    const qdiff = quietHoursDiff(myDefaults.quiet_hours, myQuiet);
    if (qdiff !== undefined) {
      (patch as { quiet_hours?: NotificationQuietHoursBlob }).quiet_hours =
        qdiff;
    }
    if (
      patch.destinations_enabled === undefined &&
      patch.quiet_hours === undefined
    ) {
      return; // nothing dirty
    }
    onSaveMy(patch);
  }, [myDefaults, myDest, myQuiet, onSaveMy, rows]);

  const handleSaveWorkspace = useCallback(() => {
    if (workspaceDefaults === null || onSaveWorkspace === undefined) return;
    if (
      validateQuietHoursWindow(wsQuiet.from_local, wsQuiet.to_local) !== null
    ) {
      return;
    }
    const patch: UpdateWorkspaceNotificationDefaultsRequest = {};
    const ddiff = destinationsDiff(
      workspaceDefaults.destinations_enabled,
      wsDest,
      rows,
    );
    if (ddiff !== undefined) {
      (
        patch as { destinations_enabled?: PerDestinationToggle }
      ).destinations_enabled = ddiff;
    }
    const qdiff = quietHoursDiff(workspaceDefaults.quiet_hours, wsQuiet);
    if (qdiff !== undefined) {
      (patch as { quiet_hours?: NotificationQuietHoursBlob }).quiet_hours =
        qdiff;
    }
    if (
      patch.destinations_enabled === undefined &&
      patch.quiet_hours === undefined
    ) {
      return;
    }
    onSaveWorkspace(patch);
  }, [workspaceDefaults, wsDest, wsQuiet, onSaveWorkspace, rows]);

  // ---- Render ----
  const tabOptions = useMemo<
    ReadonlyArray<FilterTabOption<NotificationsPageTabSlug>>
  >(
    () => [
      { slug: "my", label: "My defaults" },
      { slug: "workspace", label: "Workspace defaults" },
    ],
    [],
  );

  const showTabs = isAdmin;
  const renderTab: NotificationsPageTabSlug =
    !isAdmin || activeTab === "my" ? "my" : "workspace";

  return (
    <div style={pageStyle} data-testid="notifications-page">
      <PageHeader
        title="Notifications"
        subtitle="Control which destinations notify you and when to mute."
      />
      {showTabs ? (
        <FilterTabs<NotificationsPageTabSlug>
          value={activeTab}
          onChange={setActiveTab}
          options={tabOptions}
          ariaLabel="Notification defaults scope"
          idPrefix={`${idPrefix}-tabs`}
        />
      ) : null}
      {renderTab === "my" ? (
        <div
          id={`${idPrefix}-tabs-panel-my`}
          role={showTabs ? "tabpanel" : undefined}
          aria-labelledby={showTabs ? `${idPrefix}-tabs-tab-my` : undefined}
        >
          <NotificationsBody
            idPrefix={`${idPrefix}-my`}
            destinations={myDest}
            setDestinations={setMyDest}
            quietHours={myQuiet}
            setQuietHours={setMyQuiet}
            rows={rows}
            tzOptions={tzOptions}
            onSubmit={handleSaveMy}
            disabled={
              validateQuietHoursWindow(myQuiet.from_local, myQuiet.to_local) !==
              null
            }
          />
        </div>
      ) : (
        <div
          id={`${idPrefix}-tabs-panel-workspace`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-tabs-tab-workspace`}
        >
          {workspaceDefaults === null ? (
            <p
              style={{
                color: "var(--color-text-muted, #b4b4b8)",
                fontSize: "var(--font-size-sm, 13px)",
              }}
              data-testid="notifications-workspace-missing"
            >
              Workspace defaults unavailable.
            </p>
          ) : (
            <NotificationsBody
              idPrefix={`${idPrefix}-ws`}
              destinations={wsDest}
              setDestinations={setWsDest}
              quietHours={wsQuiet}
              setQuietHours={setWsQuiet}
              rows={rows}
              tzOptions={tzOptions}
              onSubmit={handleSaveWorkspace}
              disabled={
                onSaveWorkspace === undefined ||
                validateQuietHoursWindow(
                  wsQuiet.from_local,
                  wsQuiet.to_local,
                ) !== null
              }
            />
          )}
        </div>
      )}
    </div>
  );
}
