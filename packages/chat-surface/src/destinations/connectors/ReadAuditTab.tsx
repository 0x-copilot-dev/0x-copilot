// <ReadAuditTab /> — paginated audit log for one connector.
//
// Source: connectors-prd §4.8 (audit projection) + §6 (admin gating).
// Non-admin viewers see an explanatory empty state — the rows themselves
// are an admin-only data product.
//
// Caller column is an <ItemLink> (cross-audit §1.1). CSV export is a
// callback the host wires (the actual download lives in the data binder).

import type { CSSProperties, ReactElement } from "react";

import type { ConnectorAuditEntry } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

const STATUS_LABEL: Readonly<Record<ConnectorAuditEntry["status"], string>> = {
  ok: "OK",
  error: "Error",
  auth_required: "Auth required",
};

export interface ReadAuditTabProps {
  readonly isAdmin: boolean;
  readonly entries: ReadonlyArray<ConnectorAuditEntry>;
  readonly nextCursor?: string | null;
  readonly onLoadMore?: (cursor: string) => void;
  readonly onExportCsv?: () => void;
  /** Test seam for relative-time formatting. */
  readonly now?: number;
}

export function ReadAuditTab(props: ReadAuditTabProps): ReactElement {
  const { isAdmin, entries, nextCursor, onLoadMore, onExportCsv, now } = props;

  if (!isAdmin) {
    return (
      <div
        data-testid="connector-audit-tab"
        data-admin="false"
        style={containerStyle}
      >
        <div
          data-testid="connector-audit-admin-empty"
          role="status"
          style={adminEmptyStyle}
        >
          <h3 style={emptyTitleStyle}>Audit is admin-only</h3>
          <p style={emptyBodyStyle}>
            The connector read-audit log is visible to workspace admins. Ask
            your admin to share an export, or open this page with an admin
            session.
          </p>
        </div>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div
        data-testid="connector-audit-tab"
        data-admin="true"
        style={containerStyle}
      >
        <div
          data-testid="connector-audit-empty"
          role="status"
          style={emptyRowStyle}
        >
          No read activity yet.
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="connector-audit-tab"
      data-admin="true"
      style={containerStyle}
    >
      <div style={toolbarStyle}>
        {onExportCsv !== undefined ? (
          <button
            type="button"
            onClick={onExportCsv}
            style={secondaryButtonStyle}
            data-testid="connector-audit-export-csv"
          >
            Export CSV
          </button>
        ) : null}
      </div>
      <table
        style={tableStyle}
        data-testid="connector-audit-table"
        aria-label="Connector read audit"
      >
        <thead>
          <tr style={headerRowStyle}>
            <th style={thStyle}>When</th>
            <th style={thStyle}>Caller</th>
            <th style={thStyle}>Endpoint</th>
            <th style={thStyle}>Bytes</th>
            <th style={thStyle}>Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr
              key={entry.id}
              style={rowStyle}
              data-testid="connector-audit-row"
              data-status={entry.status}
            >
              <td style={tdStyle}>
                <time dateTime={entry.ts}>
                  {formatRelativeTime(entry.ts, now)}
                </time>
              </td>
              <td style={tdStyle}>
                <ItemLink ref={entry.caller} />
              </td>
              <td style={tdEndpointStyle}>
                <code style={codeStyle}>{entry.endpoint}</code>
              </td>
              <td style={tdStyle}>
                {entry.bytes_read === null ? "—" : String(entry.bytes_read)}
              </td>
              <td style={tdStyle}>
                <span
                  style={statusChipStyle(entry.status)}
                  data-testid="connector-audit-status"
                >
                  {STATUS_LABEL[entry.status]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {nextCursor !== null &&
      nextCursor !== undefined &&
      onLoadMore !== undefined ? (
        <button
          type="button"
          onClick={() => onLoadMore(nextCursor)}
          style={loadMoreStyle}
          data-testid="connector-audit-load-more"
        >
          Load more
        </button>
      ) : null}
    </div>
  );
}

// === Styles ============================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: 8,
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const headerRowStyle: CSSProperties = {
  borderBottom: "1px solid var(--color-border, #232325)",
  textAlign: "left",
};

const thStyle: CSSProperties = {
  padding: "6px 8px",
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const rowStyle: CSSProperties = {
  borderBottom: "1px solid var(--color-border, #232325)",
};

const tdStyle: CSSProperties = {
  padding: "6px 8px",
  verticalAlign: "top",
};

const tdEndpointStyle: CSSProperties = {
  ...tdStyle,
  fontFamily: "var(--font-mono)",
  maxWidth: 280,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const codeStyle: CSSProperties = {
  fontFamily: "inherit",
  fontSize: "inherit",
  color: "var(--color-text, #ededee)",
};

function statusChipStyle(status: ConnectorAuditEntry["status"]): CSSProperties {
  const palette =
    status === "ok"
      ? {
          bg: "var(--color-success-bg, #1a2f23)",
          fg: "var(--color-success, #6ec48c)",
        }
      : status === "auth_required"
        ? {
            bg: "var(--color-warning-bg, #322615)",
            fg: "var(--color-warning, #d9a857)",
          }
        : {
            bg: "var(--color-danger-bg, #321a1a)",
            fg: "var(--color-danger, #d97777)",
          };
  return {
    display: "inline-flex",
    alignItems: "center",
    height: 18,
    padding: "0 6px",
    borderRadius: "var(--radius-full, 999px)",
    background: palette.bg,
    color: palette.fg,
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  };
}

const loadMoreStyle: CSSProperties = {
  alignSelf: "flex-start",
  height: 28,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  height: 28,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};

const adminEmptyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: 16,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #18181b)",
};

const emptyTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const emptyBodyStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.55,
};

const emptyRowStyle: CSSProperties = {
  padding: "16px 12px",
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontStyle: "italic",
  borderRadius: "var(--radius-sm, 6px)",
  background: "var(--color-bg-elevated, #18181b)",
};
