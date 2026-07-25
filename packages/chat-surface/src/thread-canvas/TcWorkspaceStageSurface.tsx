// Shared workspace-stage surface (Generative Surfaces v2.1, PRD-C3 D4/D10). 🎨
//
// A pure, framework-agnostic review card for one staged workspace effect. This
// component has no Transport, browser, Electron, filesystem, or clock access:
// it can only render the safe `WorkspaceStage` view model and dispatch host
// callbacks. It is exported for a later host integration, but deliberately is
// not mounted by this package-only increment.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import {
  Badge,
  Button,
  Card,
  Caption,
  ItemTitle,
  SectionLabel,
} from "@0x-copilot/design-system";

import {
  projectWorkspaceStage,
  safeWorkspaceDigest,
  safeWorkspaceDisplayText,
  WORKSPACE_STAGE_PLEDGE,
  type WorkspaceStage,
  type WorkspaceStageMode,
} from "./workspaceStageProjection";

export interface TcWorkspaceStageSurfaceProps {
  /** Safe display model only; never pass a physical path or permit token. */
  readonly stage: WorkspaceStage;
  /** Focus is intentionally compact; Studio presents the full review record. */
  readonly mode?: WorkspaceStageMode;
  /** Host records an approval of exactly this opaque stage id and revision. */
  readonly onApprove: (stageId: string, revision: number) => void;
  readonly onReject: (stageId: string, revision: number) => void;
  readonly onRestore: (stageId: string) => void;
  /** Host starts a new/rebased revision; this surface does not edit bytes itself. */
  readonly onEdit: (stageId: string, revision: number) => void;
  readonly busy?: boolean;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-md)",
  minWidth: 0,
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
};

const titleStackStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2xs)",
  minWidth: 0,
  flex: "1 1 14rem",
};

const targetGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(5rem, auto) minmax(0, 1fr)",
  gap: "var(--space-xs) var(--space-md)",
  margin: 0,
};

const termStyle: CSSProperties = { margin: 0 };
const definitionStyle: CSSProperties = {
  margin: 0,
  minWidth: 0,
  overflowWrap: "anywhere",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  minWidth: 0,
};

const warningStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-sm)",
  border: "1px solid var(--color-danger-line)",
  borderRadius: "var(--radius-md)",
  background: "var(--color-bg-danger-subtle)",
};

const resolutionStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  padding: "var(--space-sm)",
  border: "1px solid var(--color-warning-line)",
  borderRadius: "var(--radius-md)",
  background: "var(--color-bg-warn-subtle)",
};

const destructiveResolutionStyle: CSSProperties = {
  ...resolutionStyle,
  border: "1px solid var(--color-danger-line)",
  background: "var(--color-bg-danger-subtle)",
};

const previewStyle: CSSProperties = {
  margin: 0,
  maxHeight: 240,
  overflow: "auto",
  padding: "var(--space-sm)",
  border: "1px solid var(--color-border-subtle)",
  borderRadius: "var(--radius-sm)",
  background: "var(--color-surface-muted)",
  color: "var(--color-text)",
  fontFamily: "var(--font-mono)",
  lineHeight: "var(--line-height-snug)",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

const csvWrapStyle: CSSProperties = {
  overflowX: "auto",
  border: "1px solid var(--color-border-subtle)",
  borderRadius: "var(--radius-sm)",
};

const csvTableStyle: CSSProperties = {
  borderCollapse: "collapse",
  width: "100%",
};

const cellStyle: CSSProperties = {
  padding: "var(--space-xs) var(--space-sm)",
  borderBottom: "1px solid var(--color-border-subtle)",
  textAlign: "left",
  verticalAlign: "top",
  overflowWrap: "anywhere",
};

const diffColumnsStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(12rem, 1fr))",
  gap: "var(--space-sm)",
};

const historyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-xs)",
  listStyle: "none",
  margin: 0,
  padding: 0,
};

const historyItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  flexWrap: "wrap",
  paddingTop: "var(--space-sm)",
  borderTop: "1px solid var(--color-border-subtle)",
};

const pledgeStyle: CSSProperties = { flex: "1 1 16rem", margin: 0 };

const MAX_PREVIEW_TEXT = 8_000;
const MAX_CSV_COLUMNS = 8;
const MAX_CSV_ROWS = 8;
const MAX_CELL_TEXT = 600;

export function TcWorkspaceStageSurface({
  stage,
  mode = "studio",
  onApprove,
  onReject,
  onRestore,
  onEdit,
  busy = false,
}: TcWorkspaceStageSurfaceProps): ReactElement {
  const projected = projectWorkspaceStage(stage, mode);
  const resolutionTone =
    stage.resolution?.state === "precondition_drift" ||
    stage.resolution?.state === "recovery_conflict"
      ? "danger"
      : "warning";
  const terminal = stage.status === "applied";

  return (
    <Card
      tone={projected.destructive ? "danger" : "default"}
      style={rootStyle}
      data-testid="tc-workspace-stage"
      data-operation={projected.operationKind}
      data-presentation={projected.compact ? "compact" : "full"}
      data-destructive={projected.destructive ? "true" : "false"}
    >
      <div style={headerStyle}>
        <div style={titleStackStyle}>
          <SectionLabel data-testid="tc-workspace-stage-kicker">
            Workspace stage
          </SectionLabel>
          <ItemTitle data-testid="tc-workspace-stage-title">
            {projected.title}
          </ItemTitle>
          <Caption data-testid="tc-workspace-stage-revision">
            {`rev ${projected.revision ?? "?"} · ${projected.author}`}
          </Caption>
        </div>
        <Badge
          tone={projected.destructive ? "danger" : "warning"}
          data-testid="tc-workspace-stage-operation"
        >
          {projected.operationLabel}
        </Badge>
        <Badge tone="neutral" data-testid="tc-workspace-stage-status">
          {projected.statusLabel}
        </Badge>
      </div>

      <dl style={targetGridStyle} data-testid="tc-workspace-stage-target">
        <dt className="ui-mono-caps" style={termStyle}>
          Mount
        </dt>
        <dd className="ui-body" style={definitionStyle}>
          {projected.mountLabel}
        </dd>
        <dt className="ui-mono-caps" style={termStyle}>
          Target
        </dt>
        <dd
          className="ui-body"
          style={definitionStyle}
          data-testid="tc-workspace-stage-path"
        >
          {projected.virtualPath ?? "Virtual target unavailable"}
        </dd>
        {projected.sourceVirtualPath !== null ? (
          <>
            <dt className="ui-mono-caps" style={termStyle}>
              From
            </dt>
            <dd className="ui-body" style={definitionStyle}>
              {projected.sourceVirtualPath}
            </dd>
          </>
        ) : null}
      </dl>

      {projected.destructive ? (
        <div
          style={warningStyle}
          role="alert"
          data-testid="tc-workspace-stage-destructive"
        >
          <Badge tone="danger">Destructive</Badge>
          <Caption as="p" style={{ margin: 0 }}>
            This action can remove or overwrite workspace content. A desktop
            host may require native confirmation before recording approval.
          </Caption>
        </div>
      ) : null}

      {projected.resolutionLabel !== null &&
      projected.resolutionSummary !== null ? (
        <div
          style={
            resolutionTone === "danger"
              ? destructiveResolutionStyle
              : resolutionStyle
          }
          role="status"
          data-testid="tc-workspace-stage-resolution"
          data-resolution={stage.resolution?.state}
        >
          <Badge tone={resolutionTone}>{projected.resolutionLabel}</Badge>
          <Caption as="p" style={{ margin: 0 }}>
            {projected.resolutionSummary}
          </Caption>
        </div>
      ) : null}

      {!projected.compact ? (
        <StudioDetails stage={stage} projected={projected} />
      ) : null}

      <div style={actionRowStyle}>
        <Caption
          as="p"
          style={pledgeStyle}
          data-testid="tc-workspace-stage-pledge"
        >
          {WORKSPACE_STAGE_PLEDGE}
        </Caption>
        <WorkspaceStageActions
          stage={stage}
          revision={projected.revision}
          destructive={projected.destructive}
          canDecide={projected.canDecide}
          canRestore={projected.canRestore}
          canEdit={projected.canEdit}
          terminal={terminal}
          busy={busy}
          onApprove={onApprove}
          onReject={onReject}
          onRestore={onRestore}
          onEdit={onEdit}
        />
      </div>
    </Card>
  );
}

function StudioDetails({
  stage,
  projected,
}: {
  readonly stage: WorkspaceStage;
  readonly projected: ReturnType<typeof projectWorkspaceStage>;
}): ReactElement {
  return (
    <>
      <DetailSection
        title="Artifact preview"
        testId="tc-workspace-stage-preview"
      >
        <Preview preview={stage.preview ?? null} />
      </DetailSection>
      <DetailSection title="Diff" testId="tc-workspace-stage-diff">
        <Diff diff={stage.diff ?? null} />
      </DetailSection>
      <DetailSection
        title="Baseline & preconditions"
        testId="tc-workspace-stage-preconditions"
      >
        <dl style={targetGridStyle}>
          <dt className="ui-mono-caps" style={termStyle}>
            Baseline
          </dt>
          <dd className="ui-body" style={definitionStyle}>
            {projected.baselineSummary}
          </dd>
          {projected.baselineDigest !== null ? (
            <>
              <dt className="ui-mono-caps" style={termStyle}>
                Hash
              </dt>
              <dd className="ui-body" style={definitionStyle}>
                {projected.baselineDigest}
              </dd>
            </>
          ) : null}
          <dt className="ui-mono-caps" style={termStyle}>
            Check
          </dt>
          <dd className="ui-body" style={definitionStyle}>
            {projected.preconditionSummary}
          </dd>
        </dl>
      </DetailSection>
      <DetailSection
        title="Revision history"
        testId="tc-workspace-stage-history"
      >
        {projected.history.length > 0 ? (
          <ul style={historyStyle}>
            {projected.history.map((entry) => (
              <li
                key={`${entry.revision}-${entry.author}`}
                style={historyItemStyle}
              >
                <Badge tone="neutral">{`rev ${entry.revision}`}</Badge>
                <Caption>{entry.author}</Caption>
                {entry.summary !== null ? (
                  <Caption>{entry.summary}</Caption>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <Caption>No revision history is available.</Caption>
        )}
      </DetailSection>
    </>
  );
}

function DetailSection({
  title,
  testId,
  children,
}: {
  readonly title: string;
  readonly testId: string;
  readonly children: ReactNode;
}): ReactElement {
  return (
    <section style={sectionStyle} data-testid={testId}>
      <SectionLabel>{title}</SectionLabel>
      {children}
    </section>
  );
}

function Preview({ preview }: { readonly preview: unknown }): ReactElement {
  if (!isRecord(preview)) {
    return (
      <Caption data-testid="tc-workspace-stage-preview-empty">
        No safely previewable artifact payload was supplied.
      </Caption>
    );
  }
  switch (preview.kind) {
    case "text": {
      const language = optionalText(preview.language, MAX_CELL_TEXT);
      return (
        <>
          {language !== null ? <Caption>{language}</Caption> : null}
          <pre
            style={previewStyle}
            data-testid="tc-workspace-stage-preview-text"
          >
            {boundedText(preview.content, MAX_PREVIEW_TEXT, "No text payload.")}
          </pre>
        </>
      );
    }
    case "csv":
      return <CsvPreview columns={preview.columns} rows={preview.rows} />;
    case "binary":
      return <BinaryMetadata metadata={preview.metadata} />;
    default:
      return (
        <Caption data-testid="tc-workspace-stage-preview-empty">
          No safely previewable artifact payload was supplied.
        </Caption>
      );
  }
}

function CsvPreview({
  columns,
  rows,
}: {
  readonly columns: unknown;
  readonly rows: unknown;
}): ReactElement {
  const visibleColumns = Array.isArray(columns)
    ? columns.slice(0, MAX_CSV_COLUMNS)
    : [];
  const visibleRows = Array.isArray(rows)
    ? rows
        .filter((row): row is readonly unknown[] => Array.isArray(row))
        .slice(0, MAX_CSV_ROWS)
    : [];
  if (visibleColumns.length === 0 && visibleRows.length === 0) {
    return (
      <Caption data-testid="tc-workspace-stage-preview-empty">
        No safely previewable artifact payload was supplied.
      </Caption>
    );
  }
  return (
    <div style={csvWrapStyle} data-testid="tc-workspace-stage-preview-csv">
      <table style={csvTableStyle}>
        {visibleColumns.length > 0 ? (
          <thead>
            <tr>
              {visibleColumns.map((column, index) => (
                <th key={`${column}-${index}`} scope="col" style={cellStyle}>
                  {boundedText(column, MAX_CELL_TEXT)}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {visibleRows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.slice(0, MAX_CSV_COLUMNS).map((cell, cellIndex) => (
                <td key={cellIndex} style={cellStyle}>
                  {boundedText(cell, MAX_CELL_TEXT)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Diff({ diff }: { readonly diff: unknown }): ReactElement {
  if (!isRecord(diff)) {
    return (
      <Caption data-testid="tc-workspace-stage-diff-empty">
        No safely previewable diff payload was supplied.
      </Caption>
    );
  }
  switch (diff.kind) {
    case "text":
      return (
        <div
          style={diffColumnsStyle}
          data-testid="tc-workspace-stage-diff-text"
        >
          <DiffText label="Before" text={diff.before} />
          <DiffText label="After" text={diff.after} />
        </div>
      );
    case "csv":
      return (
        <dl style={targetGridStyle} data-testid="tc-workspace-stage-diff-csv">
          <dt className="ui-mono-caps" style={termStyle}>
            Rows
          </dt>
          <dd className="ui-body" style={definitionStyle}>
            {`${formatCount(diff.beforeRows)} → ${formatCount(diff.afterRows)}`}
          </dd>
          <dt className="ui-mono-caps" style={termStyle}>
            Changed
          </dt>
          <dd className="ui-body" style={definitionStyle}>
            {formatCount(diff.changedRows)}
          </dd>
        </dl>
      );
    case "binary":
      return (
        <div
          style={diffColumnsStyle}
          data-testid="tc-workspace-stage-diff-binary"
        >
          <BinaryMetadata label="Before" metadata={diff.before ?? null} />
          <BinaryMetadata label="After" metadata={diff.after ?? null} />
        </div>
      );
    default:
      return (
        <Caption data-testid="tc-workspace-stage-diff-empty">
          No safely previewable diff payload was supplied.
        </Caption>
      );
  }
}

function DiffText({
  label,
  text,
}: {
  readonly label: string;
  readonly text: unknown;
}): ReactElement {
  return (
    <div style={sectionStyle}>
      <SectionLabel>{label}</SectionLabel>
      <pre style={previewStyle}>
        {boundedText(text, MAX_PREVIEW_TEXT, "No text payload.")}
      </pre>
    </div>
  );
}

function BinaryMetadata({
  label,
  metadata,
}: {
  readonly label?: string;
  readonly metadata: unknown;
}): ReactElement {
  if (!isRecord(metadata)) {
    return <Caption>No binary metadata was supplied.</Caption>;
  }
  const digest = safeWorkspaceDigest(metadata.sha256);
  return (
    <div style={sectionStyle} data-testid="tc-workspace-stage-binary-metadata">
      {label ? <SectionLabel>{label}</SectionLabel> : null}
      <dl style={targetGridStyle}>
        <dt className="ui-mono-caps" style={termStyle}>
          Type
        </dt>
        <dd className="ui-body" style={definitionStyle}>
          {boundedText(metadata.mediaType ?? "Unavailable", MAX_CELL_TEXT)}
        </dd>
        <dt className="ui-mono-caps" style={termStyle}>
          Size
        </dt>
        <dd className="ui-body" style={definitionStyle}>
          {formatBytes(metadata.byteSize)}
        </dd>
        <dt className="ui-mono-caps" style={termStyle}>
          SHA-256
        </dt>
        <dd className="ui-body" style={definitionStyle}>
          {digest ?? "Unavailable"}
        </dd>
      </dl>
    </div>
  );
}

function WorkspaceStageActions({
  stage,
  revision,
  destructive,
  canDecide,
  canRestore,
  canEdit,
  terminal,
  busy,
  onApprove,
  onReject,
  onRestore,
  onEdit,
}: {
  readonly stage: WorkspaceStage;
  readonly revision: number | null;
  readonly destructive: boolean;
  readonly canDecide: boolean;
  readonly canRestore: boolean;
  readonly canEdit: boolean;
  readonly terminal: boolean;
  readonly busy: boolean;
  readonly onApprove: TcWorkspaceStageSurfaceProps["onApprove"];
  readonly onReject: TcWorkspaceStageSurfaceProps["onReject"];
  readonly onRestore: TcWorkspaceStageSurfaceProps["onRestore"];
  readonly onEdit: TcWorkspaceStageSurfaceProps["onEdit"];
}): ReactElement {
  if (terminal) {
    return (
      <Caption data-testid="tc-workspace-stage-applied">
        Applied state reported.
      </Caption>
    );
  }
  if (canRestore) {
    return (
      <Button
        variant="primary"
        disabled={busy}
        onClick={() => onRestore(stage.stageId)}
        data-testid="tc-workspace-stage-restore"
      >
        Restore
      </Button>
    );
  }
  return (
    <>
      <Button
        variant={destructive ? "danger" : "primary"}
        disabled={busy || !canDecide || revision === null}
        onClick={() => {
          if (revision !== null) onApprove(stage.stageId, revision);
        }}
        data-testid="tc-workspace-stage-approve"
      >
        {stage.status === "approved"
          ? "Approved"
          : `Approve rev ${revision ?? "?"}`}
      </Button>
      <Button
        variant="secondary"
        disabled={busy || !canDecide || revision === null}
        onClick={() => {
          if (revision !== null) onReject(stage.stageId, revision);
        }}
        data-testid="tc-workspace-stage-reject"
      >
        Reject
      </Button>
      {canEdit ? (
        <Button
          variant="ghost"
          disabled={busy || revision === null}
          onClick={() => {
            if (revision !== null) onEdit(stage.stageId, revision);
          }}
          data-testid="tc-workspace-stage-edit"
        >
          Edit
        </Button>
      ) : null}
    </>
  );
}

function boundedText(
  value: unknown,
  max: number,
  fallback = "Unavailable",
): string {
  const safe = safeWorkspaceDisplayText(value, fallback, max);
  return safe.length <= max ? safe : `${safe.slice(0, max)}…`;
}

function optionalText(value: unknown, max: number): string | null {
  if (typeof value !== "string") return null;
  const safe = safeWorkspaceDisplayText(value, "", max);
  return safe.length > 0 ? safe : null;
}

function formatCount(value: unknown): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? String(value)
    : "Unavailable";
}

function formatBytes(value: unknown): string {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    !Number.isSafeInteger(value)
  ) {
    return "Unavailable";
  }
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
