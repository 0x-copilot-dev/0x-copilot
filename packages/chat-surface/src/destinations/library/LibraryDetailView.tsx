// <LibraryDetailView /> — unified Library detail view, switches on kind.
//
// Source:
//   docs/atlas-new-design/destinations/library-prd.md §3.4 (detail view —
//     header + preview pane + metadata panel + audit log; per-kind
//     dispatch on `LibraryFile` / `LibraryPage` / `LibraryDataset`) +
//     §8 layout (2/3 preview / 1/3 sidebar at >= 1024 px; stacked
//     below).
//   docs/atlas-new-design/cross-audit.md §1.1 (ItemRef + <ItemLink>
//     for cross-refs in audit history) + §1.3 (cascade-on-delete
//     deleted-ref signal).
//
// Invariants:
//   - **Pure presentation.** Every side-effect (download, rename,
//     delete, file-under-project, cite-in-chat, page save, etc.) lands
//     through a callback prop. Host owns transport. NO fetch /
//     transport.request / router.navigate calls in this file.
//   - **SP-1 primitives only.** Kind / source / project chips render
//     through `<StatusPill>` with the existing five-tone palette.
//   - **`<ItemLink>` for every cross-destination link.** Audit history
//     rows that mention another item render through `<ItemLink>`. No
//     direct `router.navigate(…)` from this file (cross-audit §1.1).
//   - **Signed URLs supplied by host** for `FilePreview`. We never
//     derive a URL from a `blob_ref` — the host issues a signed-URL
//     fetch (POST /v1/library/<id>/signed-url) and passes the result
//     down via the `signedUrl` prop.
//   - **Three preview components, one switch.** Each kind has its own
//     preview component (`FilePreview` / `PagePreview` / `DatasetPreview`)
//     in `./preview/`. The switch lives here.

import type { CSSProperties, ReactElement } from "react";

import type { ItemRef } from "@enterprise-search/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";

import {
  DatasetPreview,
  type DatasetPreviewProps,
} from "./preview/DatasetPreview";
import { FilePreview, type FilePreviewProps } from "./preview/FilePreview";
import { PagePreview, type PagePreviewProps } from "./preview/PagePreview";

// ===========================================================================
// Display-layer types — mirror `destinations/library-prd.md §4.1` but
// kept local until P7-A lands the wire types in `packages/api-types/src/library.ts`.
// Same pattern as `InboxDetail.tsx` + `RoutineDetail.tsx`.
// ===========================================================================

export type LibraryDetailKind = "file" | "page" | "dataset";

export type LibraryDetailItemId = string;

export type LibraryDetailIndexStatus =
  | "pending"
  | "indexing"
  | "indexed"
  | "failed"
  | "skipped";

export type LibraryDetailSourceKind =
  | "user_upload"
  | "agent_save"
  | "connector_sync";

/**
 * Source attribution for the sidebar. Always pre-formatted by the host;
 * we render the label and (optionally) link back to the originating
 * item via `<ItemLink>`. Cross-audit §1.1 — every originating ref is
 * an `ItemRef`, not a string id.
 */
export interface LibraryDetailSource {
  readonly kind: LibraryDetailSourceKind;
  /** Pre-formatted prose: e.g. "Saved from a chat 6d ago". */
  readonly label: string;
  /** Optional originating ref (chat, run, tool, connector). */
  readonly originatingRef?: ItemRef;
}

/**
 * Project chip. Hidden when `null`. The host pre-resolves the label
 * (a project slug or name); we never call into the projects ACL here.
 */
export interface LibraryDetailProjectChip {
  readonly projectId: string;
  readonly label: string;
}

/**
 * Audit row — pre-formatted prose with optional cross-refs. Each ref
 * embedded in the message renders as a separate `<ItemLink>` in the
 * `refs` array (we do NOT parse markdown-style refs out of `message`;
 * the host pre-extracts).
 */
export interface LibraryDetailAuditEntry {
  readonly id: string;
  readonly at: string;
  /** Pre-formatted prose, e.g. "Alex saved this from a chat". */
  readonly message: string;
  /** Cross-refs embedded in this audit row; rendered as <ItemLink> chips. */
  readonly refs?: ReadonlyArray<ItemRef>;
}

/**
 * Cross-references — "Cited in 3 chats" expandable list. Each item
 * renders through `<ItemLink>`.
 */
export interface LibraryDetailCrossRefs {
  /** Optional pre-formatted summary like "Cited in 3 chats". */
  readonly summary?: string;
  readonly refs: ReadonlyArray<ItemRef>;
}

interface LibraryDetailCommon {
  readonly id: LibraryDetailItemId;
  readonly title: string;
  readonly source: LibraryDetailSource;
  readonly project: LibraryDetailProjectChip | null;
  readonly tags: ReadonlyArray<string>;
  readonly indexStatus: LibraryDetailIndexStatus;
  readonly indexError: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  /** Pre-formatted relative time, e.g. "updated 3m ago". */
  readonly updatedRelative: string;
  /** Pre-formatted size string for the header chip, e.g. "2.4 MB" or "812 rows". */
  readonly sizeLabel: string;
  /** Audit log rows (last 20). */
  readonly auditEntries: ReadonlyArray<LibraryDetailAuditEntry>;
  /** Optional cross-refs — citations back-index. */
  readonly crossRefs?: LibraryDetailCrossRefs;
}

export interface LibraryFileDetailItem extends LibraryDetailCommon {
  readonly kind: "file";
  /** Pre-formatted mime label (e.g. "PDF document"). */
  readonly mimeLabel: string;
  /** Discriminator for the FilePreview rendering. */
  readonly fileKind: "doc" | "image" | "pdf" | "sheet" | "slide" | "other";
}

export interface LibraryPageDetailItem extends LibraryDetailCommon {
  readonly kind: "page";
  /** Markdown body. The PagePreview renders it through Streamdown. */
  readonly markdown: string;
  /** Monotonic version — surfaces in the sidebar. */
  readonly version: number;
  /** Optimistic-concurrency etag (library-prd §3.4.2). */
  readonly versionEtag: string;
}

export interface LibraryDatasetDetailItem extends LibraryDetailCommon {
  readonly kind: "dataset";
  /** Column specs for the dataset preview header row. */
  readonly schema: DatasetPreviewProps["schema"];
  readonly rowCount: number;
  readonly format: "parquet" | "csv" | "jsonl";
}

export type LibraryDetailItem =
  | LibraryFileDetailItem
  | LibraryPageDetailItem
  | LibraryDatasetDetailItem;

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface LibraryDetailViewProps {
  readonly item: LibraryDetailItem;

  // --- File-only preview state (signed-URL-flow). Required when
  // item.kind === "file", ignored otherwise. ---
  readonly filePreview?: Omit<FilePreviewProps, "fileKind" | "mimeLabel">;

  // --- Dataset-only preview state (host-loaded rows). Required when
  // item.kind === "dataset", ignored otherwise. ---
  readonly datasetPreview?: Omit<DatasetPreviewProps, "schema">;

  // --- Page-only props ---
  /** When non-null, replaces the read-only PagePreview with `pageEditor`. */
  readonly pageEditor?: ReactElement;
  readonly pagePreview?: Omit<PagePreviewProps, "markdown">;

  // --- Action callbacks (all optional; missing → button hidden) ---
  readonly onBack?: () => void;
  readonly onDownload?: (id: LibraryDetailItemId) => void;
  readonly onCiteInChat?: (id: LibraryDetailItemId) => void;
  readonly onFileUnderProject?: (id: LibraryDetailItemId) => void;
  readonly onPin?: (id: LibraryDetailItemId) => void;
  readonly onEdit?: (id: LibraryDetailItemId) => void;
  readonly onDelete?: (id: LibraryDetailItemId) => void;
  readonly onRetryIndex?: (id: LibraryDetailItemId) => void;

  /** Pending action ids; drives button-level disabled state. */
  readonly pending?: ReadonlySet<
    "download" | "cite" | "file-under-project" | "pin" | "edit" | "delete"
  >;
}

// ===========================================================================
// Styles
// ===========================================================================

const rootStyle: CSSProperties = {
  width: "100%",
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  color: "var(--color-text)",
  background: "var(--color-bg)",
  boxSizing: "border-box",
};

const containerStyle: CSSProperties = {
  width: "100%",
  maxWidth: 1200,
  margin: "0 auto",
  padding: "16px 20px 32px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 16,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
};

const titleRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  flexWrap: "wrap",
};

const titleStyle: CSSProperties = {
  fontSize: 18,
  fontWeight: 700,
  margin: 0,
  lineHeight: 1.3,
  flex: "1 1 auto",
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const chipRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 6,
  fontSize: 12,
  color: "var(--color-text-muted)",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  paddingTop: 6,
};

const secondaryButtonStyle = (busy: boolean): CSSProperties => ({
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: 12,
  fontWeight: 600,
  cursor: busy ? "default" : "pointer",
  opacity: busy ? 0.6 : 1,
});

const bodyLayoutStyle: CSSProperties = {
  display: "grid",
  // 2/3 preview / 1/3 sidebar above 1024px, single column below.
  gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
  gap: 16,
};

const previewPaneStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  minWidth: 0,
};

const sidebarStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  minWidth: 0,
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  borderRadius: 8,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
};

const sectionTitleStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-muted)",
};

const metaLineStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  fontSize: 12,
  color: "var(--color-text)",
};

const metaLabelStyle: CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-subtle)",
};

const auditRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "8px 0",
  borderTop: "1px solid var(--color-border)",
  fontSize: 12,
};

const auditFirstRowStyle: CSSProperties = {
  ...auditRowStyle,
  borderTop: "none",
  paddingTop: 0,
};

const auditAtStyle: CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-subtle)",
};

const auditRefsRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  paddingTop: 4,
};

const errorBannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid var(--color-danger, #ef4444)",
  background: "var(--color-danger-surface, rgba(239, 68, 68, 0.08))",
  color: "var(--color-danger, #ef4444)",
  fontSize: 12,
};

// ===========================================================================
// Tone mapping
// ===========================================================================

function kindTone(_kind: LibraryDetailKind): StatusTone {
  return "info";
}

function kindLabel(kind: LibraryDetailKind): string {
  if (kind === "file") return "File";
  if (kind === "page") return "Page";
  return "Dataset";
}

function sourceTone(kind: LibraryDetailSourceKind): StatusTone {
  if (kind === "user_upload") return "muted";
  if (kind === "agent_save") return "info";
  return "ok"; // connector_sync
}

function sourceLabel(kind: LibraryDetailSourceKind): string {
  if (kind === "user_upload") return "Uploaded";
  if (kind === "agent_save") return "From agent";
  return "Connector";
}

function indexTone(status: LibraryDetailIndexStatus): StatusTone {
  if (status === "indexed") return "ok";
  if (status === "failed") return "error";
  if (status === "indexing" || status === "pending") return "warning";
  return "muted"; // skipped
}

function indexLabel(status: LibraryDetailIndexStatus): string {
  if (status === "indexed") return "Indexed";
  if (status === "indexing") return "Indexing…";
  if (status === "pending") return "Queued";
  if (status === "failed") return "Index failed";
  return "Not indexed";
}

// ===========================================================================
// Component
// ===========================================================================

export function LibraryDetailView({
  item,
  filePreview,
  datasetPreview,
  pageEditor,
  pagePreview,
  onBack,
  onDownload,
  onCiteInChat,
  onFileUnderProject,
  onPin,
  onEdit,
  onDelete,
  onRetryIndex,
  pending,
}: LibraryDetailViewProps): ReactElement {
  const busyDownload = pending?.has("download") ?? false;
  const busyCite = pending?.has("cite") ?? false;
  const busyFileProject = pending?.has("file-under-project") ?? false;
  const busyPin = pending?.has("pin") ?? false;
  const busyEdit = pending?.has("edit") ?? false;
  const busyDelete = pending?.has("delete") ?? false;

  // Per-kind preview slot. Three kinds; one switch; each kind owns its
  // own component file under `./preview/`.
  let preview: ReactElement;
  if (item.kind === "file") {
    preview = (
      <FilePreview
        fileKind={item.fileKind}
        mimeLabel={item.mimeLabel}
        {...(filePreview ?? { state: { kind: "idle" } })}
      />
    );
  } else if (item.kind === "page") {
    // PageEditor takes precedence when supplied; otherwise read-only preview.
    if (pageEditor !== undefined) {
      preview = pageEditor;
    } else {
      preview = (
        <PagePreview
          markdown={item.markdown}
          {...(pagePreview ?? { mode: "static" })}
        />
      );
    }
  } else {
    preview = (
      <DatasetPreview
        schema={item.schema}
        {...(datasetPreview ?? { state: { kind: "idle" } })}
      />
    );
  }

  return (
    <section
      aria-label="Library detail"
      data-testid="library-detail-view"
      data-item-kind={item.kind}
      data-item-id={item.id}
      style={rootStyle}
    >
      <div style={containerStyle}>
        {/* ===== Header ===== */}
        <header style={headerStyle} data-testid="library-detail-header">
          <div style={titleRowStyle}>
            {onBack !== undefined && (
              <button
                type="button"
                onClick={onBack}
                style={{
                  ...secondaryButtonStyle(false),
                  padding: "0 10px",
                  height: 28,
                }}
                aria-label="Back to Library"
                data-testid="library-detail-back"
              >
                ← Back
              </button>
            )}
            <h1 style={titleStyle} title={item.title}>
              {item.title}
            </h1>
          </div>

          <div style={chipRowStyle} data-testid="library-detail-chips">
            <StatusPill
              status={kindTone(item.kind)}
              label={kindLabel(item.kind)}
            />
            <StatusPill
              status={sourceTone(item.source.kind)}
              label={sourceLabel(item.source.kind)}
            />
            {item.project !== null && (
              <StatusPill status="muted" label={item.project.label} />
            )}
            <StatusPill status="muted" label={item.sizeLabel} />
            <StatusPill
              status={indexTone(item.indexStatus)}
              label={indexLabel(item.indexStatus)}
            />
            <span aria-hidden="true">·</span>
            <span>{item.updatedRelative}</span>
          </div>

          {item.indexStatus === "failed" && (
            <div style={errorBannerStyle} role="alert">
              <span>
                Indexing failed{item.indexError ? `: ${item.indexError}` : ""}.
              </span>
              {onRetryIndex !== undefined && (
                <button
                  type="button"
                  style={secondaryButtonStyle(false)}
                  onClick={() => onRetryIndex(item.id)}
                  data-testid="library-detail-retry-index"
                >
                  Retry
                </button>
              )}
            </div>
          )}

          <div style={actionsRowStyle}>
            {item.kind === "page" && onEdit !== undefined && (
              <button
                type="button"
                onClick={() => onEdit(item.id)}
                disabled={busyEdit}
                style={secondaryButtonStyle(busyEdit)}
                data-testid="library-detail-action-edit"
              >
                {busyEdit ? "Opening…" : "Edit"}
              </button>
            )}
            {(item.kind === "file" || item.kind === "dataset") &&
              onDownload !== undefined && (
                <button
                  type="button"
                  onClick={() => onDownload(item.id)}
                  disabled={busyDownload}
                  style={secondaryButtonStyle(busyDownload)}
                  data-testid="library-detail-action-download"
                >
                  {busyDownload ? "Preparing…" : "Download"}
                </button>
              )}
            {onCiteInChat !== undefined && (
              <button
                type="button"
                onClick={() => onCiteInChat(item.id)}
                disabled={busyCite}
                style={secondaryButtonStyle(busyCite)}
                data-testid="library-detail-action-cite"
              >
                Cite in chat
              </button>
            )}
            {onPin !== undefined && (
              <button
                type="button"
                onClick={() => onPin(item.id)}
                disabled={busyPin}
                style={secondaryButtonStyle(busyPin)}
                data-testid="library-detail-action-pin"
              >
                Pin
              </button>
            )}
            {onFileUnderProject !== undefined && (
              <button
                type="button"
                onClick={() => onFileUnderProject(item.id)}
                disabled={busyFileProject}
                style={secondaryButtonStyle(busyFileProject)}
                data-testid="library-detail-action-file-under-project"
              >
                File under project
              </button>
            )}
            {onDelete !== undefined && (
              <button
                type="button"
                onClick={() => onDelete(item.id)}
                disabled={busyDelete}
                style={{
                  ...secondaryButtonStyle(busyDelete),
                  color: "var(--color-danger, #ef4444)",
                  borderColor: "var(--color-danger, #ef4444)",
                }}
                data-testid="library-detail-action-delete"
              >
                Delete
              </button>
            )}
          </div>
        </header>

        {/* ===== Body: preview + sidebar ===== */}
        <div style={bodyLayoutStyle} data-testid="library-detail-body">
          <div
            style={previewPaneStyle}
            data-testid="library-detail-preview-slot"
          >
            {preview}
          </div>

          <aside style={sidebarStyle} aria-label="Metadata and history">
            <section style={sectionStyle} data-testid="library-detail-meta">
              <div style={sectionTitleStyle}>Metadata</div>
              <div style={metaLineStyle}>
                <span style={metaLabelStyle}>Source</span>
                <span>{item.source.label}</span>
                {item.source.originatingRef !== undefined && (
                  <div style={{ paddingTop: 2 }}>
                    <ItemLink ref={item.source.originatingRef} />
                  </div>
                )}
              </div>
              {item.project !== null && (
                <div style={metaLineStyle}>
                  <span style={metaLabelStyle}>Project</span>
                  <span>{item.project.label}</span>
                </div>
              )}
              {item.tags.length > 0 && (
                <div style={metaLineStyle}>
                  <span style={metaLabelStyle}>Tags</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {item.tags.map((tag) => (
                      <StatusPill key={tag} status="muted" label={tag} />
                    ))}
                  </div>
                </div>
              )}
              <div style={metaLineStyle}>
                <span style={metaLabelStyle}>Created</span>
                <span>{item.createdAt}</span>
              </div>
              <div style={metaLineStyle}>
                <span style={metaLabelStyle}>Updated</span>
                <span>{item.updatedAt}</span>
              </div>
              {item.kind === "page" && (
                <div style={metaLineStyle}>
                  <span style={metaLabelStyle}>Version</span>
                  <span>v{item.version}</span>
                </div>
              )}
              {item.kind === "dataset" && (
                <div style={metaLineStyle}>
                  <span style={metaLabelStyle}>Rows</span>
                  <span>{item.rowCount.toLocaleString()}</span>
                </div>
              )}
            </section>

            {item.crossRefs !== undefined && item.crossRefs.refs.length > 0 && (
              <section
                style={sectionStyle}
                data-testid="library-detail-cross-refs"
              >
                <div style={sectionTitleStyle}>
                  {item.crossRefs.summary ??
                    `Cited in ${item.crossRefs.refs.length}`}
                </div>
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 4 }}
                >
                  {item.crossRefs.refs.map((ref, idx) => (
                    <div key={`${ref.kind}:${ref.id}:${idx}`}>
                      <ItemLink ref={ref} />
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section style={sectionStyle} data-testid="library-detail-audit">
              <div style={sectionTitleStyle}>Audit history</div>
              {item.auditEntries.length === 0 ? (
                <div
                  style={{ fontSize: 12, color: "var(--color-text-subtle)" }}
                >
                  No audit entries yet.
                </div>
              ) : (
                item.auditEntries.map((entry, idx) => (
                  <div
                    key={entry.id}
                    style={idx === 0 ? auditFirstRowStyle : auditRowStyle}
                    data-testid="library-detail-audit-row"
                  >
                    <span style={auditAtStyle}>{entry.at}</span>
                    <span>{entry.message}</span>
                    {entry.refs !== undefined && entry.refs.length > 0 && (
                      <div style={auditRefsRowStyle}>
                        {entry.refs.map((ref, refIdx) => (
                          <ItemLink
                            key={`${ref.kind}:${ref.id}:${refIdx}`}
                            ref={ref}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ))
              )}
            </section>
          </aside>
        </div>
      </div>
    </section>
  );
}
