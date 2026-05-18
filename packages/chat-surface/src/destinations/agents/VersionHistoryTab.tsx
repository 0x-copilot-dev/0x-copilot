// <VersionHistoryTab /> — list AgentVersion rows; clicking a row shows
// the immutable snapshot in a read-only inline panel.
//
// Source:
//   - docs/atlas-new-design/destinations/agents-prd.md §3.2 (immutability
//     rule — versions are immutable; no PATCH endpoint), §4.7 (snapshot
//     create), §4.8 (list), §7.3 (last-3 versions preview), §7.4 ("Save as
//     version" footer CTA).
//
// Invariants:
//   - Version history is **read-only**. Per the task brief: clicking a
//     version shows the snapshot, NOT an editor. There is no PATCH path
//     and the UI offers none.
//   - SP-1: StatusPill for the active/selected version surface; no
//     bespoke chip primitive.
//   - Pure presentation. Host owns GET /v1/agents/<id>/versions, and
//     navigation between snapshot rows.

import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { StatusPill } from "@enterprise-search/design-system";

import type {
  AgentEditorModelDefault,
  AgentEditorPermissions,
} from "./AgentEditor";

// ===========================================================================
// View-model — what each version row needs. Mirrors agents-prd §3.1
// `AgentVersion` minus the immutable id/agent_id pointers (host carries).
// ===========================================================================

export interface AgentVersionRow {
  readonly id: string;
  readonly agent_id: string;
  /** Monotonic counter; matches Agent.version at snapshot time. */
  readonly version: number;
  readonly label: string | null;
  /** ISO8601 display string. */
  readonly created_at: string;
  /** Display name of the user who snapshotted. */
  readonly created_by: string;
  readonly instructions_snapshot: string;
  readonly model_default_snapshot: AgentEditorModelDefault;
  readonly skills_snapshot: ReadonlyArray<string>;
  readonly connectors_default_snapshot: ReadonlyArray<string>;
  readonly permissions_snapshot: AgentEditorPermissions;
}

export interface VersionHistoryTabProps {
  readonly versions: ReadonlyArray<AgentVersionRow>;
  /** When undefined, defaults to the highest-version row. */
  readonly initialSelectedId?: string;
  /**
   * Optional: fires when the user clicks "Use this version" — host may
   * route to a routine-edit flow that pins the agent_version_pin to the
   * snapshot. Pure callback; no transport.
   */
  readonly onUseForRoutine?: (versionId: string) => void;
}

// ===========================================================================
// Component.
// ===========================================================================

export function VersionHistoryTab(props: VersionHistoryTabProps): ReactElement {
  const { versions, initialSelectedId, onUseForRoutine } = props;

  // Default selection: caller-controlled, else highest-version row.
  const defaultSelected =
    initialSelectedId ??
    versions.reduce<string | null>(
      (acc, v) =>
        acc === null || v.version > findVersion(versions, acc) ? v.id : acc,
      null,
    );
  const [selectedId, setSelectedId] = useState<string | null>(defaultSelected);

  const selected =
    selectedId === null
      ? null
      : (versions.find((v) => v.id === selectedId) ?? null);

  const select = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  if (versions.length === 0) {
    return (
      <div
        data-testid="agent-version-history-empty"
        role="status"
        style={emptyStyle}
      >
        No versions yet. Use "Save as version" in the editor to snapshot.
      </div>
    );
  }

  return (
    <div data-testid="agent-version-history" style={containerStyle}>
      <ol
        style={listStyle}
        data-testid="agent-version-history-list"
        aria-label="Versions"
      >
        {versions.map((v) => {
          const active = selectedId === v.id;
          return (
            <li
              key={v.id}
              style={{ ...rowStyle, ...(active ? rowActiveStyle : null) }}
              data-testid={`agent-version-row-${v.id}`}
              data-version={v.version}
              data-active={active ? "true" : "false"}
            >
              <button
                type="button"
                onClick={() => select(v.id)}
                aria-pressed={active}
                aria-label={`Version v${v.version}`}
                data-testid={`agent-version-row-${v.id}-button`}
                style={rowButtonStyle}
              >
                <span style={versionStyle}>v{v.version}</span>
                <span style={labelStyle}>{v.label ?? "(no label)"}</span>
                <span style={metaStyle}>
                  by {v.created_by} · {v.created_at}
                </span>
                {active ? (
                  <StatusPill
                    tone="running"
                    label="Viewing"
                    data-testid={`agent-version-row-${v.id}-pill`}
                  />
                ) : null}
              </button>
            </li>
          );
        })}
      </ol>

      {selected !== null ? (
        <SnapshotView
          version={selected}
          onUseForRoutine={
            onUseForRoutine === undefined
              ? undefined
              : () => onUseForRoutine(selected.id)
          }
        />
      ) : null}
    </div>
  );
}

// ===========================================================================
// SnapshotView — read-only block showing one version's frozen fields.
// ===========================================================================

interface SnapshotViewProps {
  readonly version: AgentVersionRow;
  readonly onUseForRoutine?: () => void;
}

function SnapshotView(props: SnapshotViewProps): ReactElement {
  const { version, onUseForRoutine } = props;
  return (
    <section
      data-testid={`agent-version-snapshot-${version.id}`}
      data-version={version.version}
      aria-label={`Version v${version.version} snapshot (read-only)`}
      style={snapshotStyle}
    >
      <header style={snapshotHeaderStyle}>
        <h3 style={snapshotTitleStyle}>
          Snapshot v{version.version}
          {version.label !== null && version.label.length > 0
            ? ` · ${version.label}`
            : ""}
        </h3>
        <StatusPill
          tone="idle"
          label="Read-only"
          data-testid={`agent-version-snapshot-${version.id}-read-only-pill`}
        />
      </header>
      <p style={snapshotMetaStyle}>
        Snapshotted by {version.created_by} on {version.created_at}. Per
        agents-prd §3.2 this version is immutable.
      </p>
      <dl style={snapshotFactsStyle}>
        <SnapshotFact
          label="Model"
          value={version.model_default_snapshot.model_id}
          testId={`agent-version-snapshot-${version.id}-model`}
        />
        <SnapshotFact
          label="Reasoning depth"
          value={version.model_default_snapshot.reasoning_depth}
          testId={`agent-version-snapshot-${version.id}-depth`}
        />
        <SnapshotFact
          label="Skills"
          value={String(version.skills_snapshot.length)}
          testId={`agent-version-snapshot-${version.id}-skills`}
        />
        <SnapshotFact
          label="Connectors"
          value={String(version.connectors_default_snapshot.length)}
          testId={`agent-version-snapshot-${version.id}-connectors`}
        />
        <SnapshotFact
          label="Autonomy"
          value={version.permissions_snapshot.autonomy}
          testId={`agent-version-snapshot-${version.id}-autonomy`}
        />
        <SnapshotFact
          label="Read-only at fire"
          value={version.permissions_snapshot.read_only ? "Yes" : "No"}
          testId={`agent-version-snapshot-${version.id}-read-only`}
        />
      </dl>
      <pre
        style={instructionsBlockStyle}
        data-testid={`agent-version-snapshot-${version.id}-instructions`}
        // Snapshot is immutable + read-only — `aria-readonly` advertises
        // this fact to assistive tech.
        aria-readonly="true"
      >
        {version.instructions_snapshot}
      </pre>
      {onUseForRoutine !== undefined ? (
        <div style={snapshotActionsStyle}>
          <button
            type="button"
            onClick={onUseForRoutine}
            data-testid={`agent-version-snapshot-${version.id}-use-for-routine`}
            style={secondaryButtonStyle}
          >
            Use this version in a routine
          </button>
        </div>
      ) : null}
    </section>
  );
}

interface SnapshotFactProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function SnapshotFact(props: SnapshotFactProps): ReactElement {
  return (
    <div style={factCellStyle} data-testid={props.testId}>
      <dt style={factLabelStyle}>{props.label}</dt>
      <dd style={factValueStyle}>{props.value}</dd>
    </div>
  );
}

// ===========================================================================
// Helpers.
// ===========================================================================

function findVersion(
  versions: ReadonlyArray<AgentVersionRow>,
  id: string,
): number {
  const v = versions.find((row) => row.id === id);
  return v === undefined ? -1 : v.version;
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const rowStyle: CSSProperties = {
  borderRadius: 6,
  border: "1px solid var(--color-border)",
};

const rowActiveStyle: CSSProperties = {
  borderColor: "var(--color-accent)",
};

const rowButtonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  padding: "8px 10px",
  background: "transparent",
  color: "var(--color-text)",
  border: "none",
  cursor: "pointer",
  fontFamily: "inherit",
  fontSize: 13,
  textAlign: "left",
};

const versionStyle: CSSProperties = {
  fontWeight: 600,
  minWidth: 44,
  fontFamily: "var(--font-family-mono, ui-monospace, monospace)",
};

const labelStyle: CSSProperties = {
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--color-text-muted)",
};

const snapshotStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  borderRadius: 8,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
};

const snapshotHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  flexWrap: "wrap",
};

const snapshotTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  fontWeight: 600,
};

const snapshotMetaStyle: CSSProperties = {
  margin: 0,
  fontSize: 12,
  color: "var(--color-text-muted)",
};

const snapshotFactsStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
  gap: 8,
  margin: 0,
  padding: 0,
};

const factCellStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  padding: "6px 8px",
  background: "var(--color-bg)",
  borderRadius: 4,
};

const factLabelStyle: CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-muted)",
  margin: 0,
};

const factValueStyle: CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  margin: 0,
  color: "var(--color-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const instructionsBlockStyle: CSSProperties = {
  margin: 0,
  padding: 10,
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg)",
  color: "var(--color-text)",
  fontSize: 12,
  fontFamily: "inherit",
  whiteSpace: "pre-wrap",
  maxHeight: 240,
  overflow: "auto",
};

const snapshotActionsStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "5px 12px",
  fontSize: 13,
  cursor: "pointer",
};

const emptyStyle: CSSProperties = {
  padding: 16,
  textAlign: "center",
  fontSize: 13,
  color: "var(--color-text-muted)",
  fontStyle: "italic",
};
