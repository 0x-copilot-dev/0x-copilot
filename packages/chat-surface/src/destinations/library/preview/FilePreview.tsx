// <FilePreview /> — Library file preview pane (PDF / image / doc / fallback).
//
// Source:
//   docs/atlas-new-design/destinations/library-prd.md §3.4.1 (file
//     detail preview pane — PDF embed / image / thumbnail strip /
//     metadata-only fallback).
//
// Invariants:
//   - **Host supplies the signed URL.** Library file bytes live in an
//     S3-compatible object store; the host issues
//     `POST /v1/library/<id>/signed-url`, receives the time-limited URL,
//     and passes it down via `state.kind === "ready"`. This component
//     NEVER embeds a raw `blob_ref` and NEVER fetches the signed URL
//     itself.
//   - **Pure presentation.** No fetch, no transport call. The host owns
//     the lifecycle (refresh on expiry, retry on 5xx).
//   - Three render branches (loading / error / ready) plus the
//     fallback metadata-only render when the mime can't be embedded
//     in-line (sheets / slides — preview thumbs strip lands later).

import type { CSSProperties, ReactElement } from "react";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type FilePreviewKind =
  | "doc"
  | "image"
  | "pdf"
  | "sheet"
  | "slide"
  | "other";

export type FilePreviewState =
  /** No signed-URL request has been issued yet. */
  | { readonly kind: "idle" }
  /** Signed-URL request in flight. */
  | { readonly kind: "loading" }
  /** Signed-URL request failed. */
  | { readonly kind: "error"; readonly message: string }
  /** Signed URL acquired. */
  | {
      readonly kind: "ready";
      readonly signedUrl: string;
      /** ISO; host may use this to refresh the URL before expiry. */
      readonly expiresAt?: string;
      /** Optional first-page thumbnail signed URL (server pre-rendered). */
      readonly thumbnailUrl?: string;
      /** Optional alt text for images (defaults to filename). */
      readonly alt?: string;
    };

export interface FilePreviewProps {
  readonly fileKind: FilePreviewKind;
  /** Pre-formatted mime label, e.g. "PDF document". */
  readonly mimeLabel: string;
  readonly state: FilePreviewState;
  /** Optional retry callback; surfaces when state.kind === "error". */
  readonly onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const wrapperStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  overflow: "hidden",
  minHeight: 240,
};

const placeholderStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  padding: 32,
  minHeight: 240,
  color: "var(--color-text-muted)",
  fontSize: 13,
  textAlign: "center",
};

const skeletonStyle: CSSProperties = {
  flex: 1,
  background:
    "linear-gradient(90deg, var(--color-surface-muted) 25%, var(--color-bg-elevated) 50%, var(--color-surface-muted) 75%)",
  minHeight: 240,
  opacity: 0.5,
};

const pdfFrameStyle: CSSProperties = {
  width: "100%",
  minHeight: 480,
  border: "none",
  display: "block",
  background: "var(--color-bg)",
};

const imageStyle: CSSProperties = {
  maxWidth: "100%",
  maxHeight: 600,
  display: "block",
  margin: "0 auto",
};

const errorButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
};

const downloadLinkStyle: CSSProperties = {
  color: "var(--color-accent)",
  textDecoration: "underline",
  fontSize: 13,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FilePreview({
  fileKind,
  mimeLabel,
  state,
  onRetry,
}: FilePreviewProps): ReactElement {
  if (state.kind === "loading" || state.kind === "idle") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-file-preview"
        data-state={state.kind}
        data-file-kind={fileKind}
      >
        <div style={skeletonStyle} aria-hidden="true" />
        <div
          style={{
            padding: 12,
            fontSize: 12,
            color: "var(--color-text-subtle)",
          }}
        >
          {state.kind === "loading" ? "Loading preview…" : "Preview"}
        </div>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-file-preview"
        data-state="error"
        data-file-kind={fileKind}
      >
        <div style={placeholderStyle} role="alert">
          <span style={{ fontWeight: 600 }}>Could not load preview</span>
          <span style={{ color: "var(--color-text-subtle)" }}>
            {state.message}
          </span>
          {onRetry !== undefined && (
            <button
              type="button"
              style={errorButtonStyle}
              onClick={onRetry}
              data-testid="library-file-preview-retry"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  // state.kind === "ready"
  if (fileKind === "pdf") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-file-preview"
        data-state="ready"
        data-file-kind="pdf"
      >
        <iframe
          src={state.signedUrl}
          style={pdfFrameStyle}
          title={state.alt ?? "PDF preview"}
          data-testid="library-file-preview-pdf"
        />
      </div>
    );
  }

  if (fileKind === "image") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-file-preview"
        data-state="ready"
        data-file-kind="image"
      >
        <img
          src={state.signedUrl}
          alt={state.alt ?? "Image preview"}
          style={imageStyle}
          data-testid="library-file-preview-image"
        />
      </div>
    );
  }

  // doc / sheet / slide / other — show thumbnail if available, otherwise
  // a metadata-only fallback with a download link.
  return (
    <div
      style={wrapperStyle}
      data-testid="library-file-preview"
      data-state="ready"
      data-file-kind={fileKind}
    >
      {state.thumbnailUrl !== undefined ? (
        <img
          src={state.thumbnailUrl}
          alt={state.alt ?? "Document thumbnail"}
          style={imageStyle}
          data-testid="library-file-preview-thumbnail"
        />
      ) : (
        <div style={placeholderStyle}>
          <span style={{ fontWeight: 600 }}>Preview not available</span>
          <span style={{ color: "var(--color-text-subtle)" }}>{mimeLabel}</span>
          <a
            href={state.signedUrl}
            style={downloadLinkStyle}
            data-testid="library-file-preview-download-link"
            // Signed URL — opening it triggers the download. We let the
            // browser handle it; the host's action-row "Download" button
            // is the primary path.
          >
            Open / download
          </a>
        </div>
      )}
    </div>
  );
}
