// <PageEditor /> — Library page markdown editor.
//
// Source:
//   docs/atlas-new-design/destinations/library-prd.md §3.4.2 (page
//     editor — title input + markdown textarea + auto-save every 30s
//     + Streamdown preview side-by-side + autosave chip "Saved 3s
//     ago" + optimistic-concurrency `If-Match` 409 banner).
//   docs/atlas-new-design/cross-audit.md §1.6 — autosave-chip pattern
//     is the SP-1 sign-of-life primitive for every editor surface.
//
// Invariants:
//   - **Pure presentation.** Save lands through `onSave` callback;
//     the host owns the PATCH and the etag bookkeeping. NO fetch /
//     transport.request from this file.
//   - **Controlled API.** Host owns `title` + `markdown` state; we
//     emit `onChange` whenever the user edits. (Uncontrolled would
//     hide the buffer from the host's autosave-debounce timer and
//     break the §3.4.2 contract.)
//   - **Streamdown for preview.** Same renderer chat messages use;
//     no new markdown lib.
//   - **Sign-of-life via the autosave chip.** Every save shows
//     "Saved Ns ago" with a relative-time tick driven by `lastSavedAt`.

import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";
import { Streamdown } from "streamdown";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PageEditorView = "edit" | "preview" | "split";

export type PageEditorSaveStatus =
  | { readonly kind: "idle" }
  | { readonly kind: "dirty" }
  | { readonly kind: "saving" }
  | { readonly kind: "saved"; readonly at: string }
  | { readonly kind: "error"; readonly message: string }
  /** 409 from `If-Match` — host received a concurrent edit. */
  | {
      readonly kind: "conflict";
      readonly remoteVersion: number;
      readonly message?: string;
    };

export interface PageEditorProps {
  readonly title: string;
  readonly markdown: string;
  readonly onChange: (next: { title: string; markdown: string }) => void;
  readonly onSave: () => void;

  readonly saveStatus: PageEditorSaveStatus;
  /** Default "split". */
  readonly initialView?: PageEditorView;
  readonly activeView?: PageEditorView;
  readonly onViewChange?: (view: PageEditorView) => void;

  /** Optional cancel / discard CTA. */
  readonly onCancel?: () => void;
  /** Conflict resolution callbacks (only used when saveStatus.kind === "conflict"). */
  readonly onViewRemote?: () => void;
  readonly onOverwrite?: () => void;

  /** Used by the autosave chip relative-time formatter. Mainly for tests. */
  readonly now?: () => number;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const wrapperStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  padding: 12,
  minHeight: 320,
};

const titleInputStyle: CSSProperties = {
  fontSize: "var(--font-size-xl)",
  fontWeight: 700,
  background: "transparent",
  border: "none",
  outline: "none",
  color: "var(--color-text)",
  padding: 4,
  width: "100%",
  boxSizing: "border-box",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  flexWrap: "wrap",
};

const viewToggleStyle: CSSProperties = {
  display: "inline-flex",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  overflow: "hidden",
};

const viewToggleButtonStyle = (active: boolean): CSSProperties => ({
  height: 28,
  padding: "0 10px",
  border: "none",
  background: active ? "var(--color-accent)" : "transparent",
  color: active ? "var(--color-on-accent, #fff)" : "var(--color-text-muted)",
  cursor: "pointer",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
});

const primaryButtonStyle = (busy: boolean): CSSProperties => ({
  height: 30,
  padding: "0 14px",
  borderRadius: 6,
  border: "1px solid var(--color-accent)",
  background: "var(--color-accent)",
  color: "var(--color-on-accent, #fff)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: busy ? "default" : "pointer",
  opacity: busy ? 0.6 : 1,
});

const secondaryButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const splitStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 12,
  minHeight: 320,
};

const textareaStyle: CSSProperties = {
  flex: 1,
  minHeight: 280,
  width: "100%",
  boxSizing: "border-box",
  resize: "vertical",
  padding: 12,
  fontFamily: "var(--font-mono, monospace)",
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.5,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  outline: "none",
};

const previewPaneStyle: CSSProperties = {
  padding: 12,
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  background: "var(--color-bg)",
  minHeight: 280,
  overflow: "auto",
  fontSize: "var(--font-size-md)",
  lineHeight: 1.6,
};

const statusChipStyle = (tone: PageEditorSaveStatus["kind"]): CSSProperties => {
  const color =
    tone === "saved"
      ? "var(--color-success, #6ec48c)"
      : tone === "saving"
        ? "var(--color-warning, #facc15)"
        : tone === "error" || tone === "conflict"
          ? "var(--color-danger, #ef4444)"
          : tone === "dirty"
            ? "var(--color-text-muted)"
            : "var(--color-text-subtle)";
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: "var(--font-size-2xs)",
    fontWeight: 600,
    color,
  };
};

const conflictBannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid var(--color-danger, #ef4444)",
  background: "var(--color-danger-surface, rgba(239, 68, 68, 0.08))",
  color: "var(--color-danger, #ef4444)",
  fontSize: "var(--font-size-xs)",
};

// ---------------------------------------------------------------------------
// Save-chip relative-time formatter
// ---------------------------------------------------------------------------

function relativeSeconds(at: string, now: number): string {
  const parsed = Date.parse(at);
  if (Number.isNaN(parsed)) return "just now";
  const delta = Math.max(0, Math.floor((now - parsed) / 1000));
  if (delta < 2) return "just now";
  if (delta < 60) return `${delta}s ago`;
  const minutes = Math.floor(delta / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function statusLabel(
  status: PageEditorSaveStatus,
  nowFn: () => number,
): string {
  if (status.kind === "idle") return "All changes saved";
  if (status.kind === "dirty") return "Unsaved changes";
  if (status.kind === "saving") return "Saving…";
  if (status.kind === "error") return `Save failed: ${status.message}`;
  if (status.kind === "conflict") return "Conflict — page updated elsewhere";
  return `Saved ${relativeSeconds(status.at, nowFn())}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PageEditor({
  title,
  markdown,
  onChange,
  onSave,
  saveStatus,
  initialView = "split",
  activeView,
  onViewChange,
  onCancel,
  onViewRemote,
  onOverwrite,
  now,
}: PageEditorProps): ReactElement {
  // Controlled / uncontrolled view toggle (mirrors the RoutineDetail
  // tabs-controlled pattern).
  const [internalView, setInternalView] = useState<PageEditorView>(initialView);
  const view = activeView ?? internalView;
  const setView = (next: PageEditorView): void => {
    if (activeView === undefined) setInternalView(next);
    onViewChange?.(next);
  };

  // Tick the save-chip every second when in `saved` state so the
  // relative-time copy stays fresh ("Saved 3s ago" → "Saved 4s ago").
  const [, setTick] = useState(0);
  useEffect(() => {
    if (saveStatus.kind !== "saved") return undefined;
    const interval = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, [saveStatus.kind]);

  const nowFn = now ?? Date.now;

  const busy = saveStatus.kind === "saving";
  const showEdit = view === "edit" || view === "split";
  const showPreview = view === "preview" || view === "split";

  return (
    <div
      style={wrapperStyle}
      data-testid="library-page-editor"
      data-view={view}
      data-save-status={saveStatus.kind}
    >
      <input
        type="text"
        value={title}
        onChange={(e) => onChange({ title: e.target.value, markdown })}
        placeholder="Untitled page"
        aria-label="Page title"
        style={titleInputStyle}
        data-testid="library-page-editor-title"
      />

      {saveStatus.kind === "conflict" && (
        <div style={conflictBannerStyle} role="alert">
          <span>
            {saveStatus.message ??
              "Someone (or another tab) updated this page."}
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            {onViewRemote !== undefined && (
              <button
                type="button"
                onClick={onViewRemote}
                style={secondaryButtonStyle}
                data-testid="library-page-editor-conflict-view"
              >
                View their version
              </button>
            )}
            {onOverwrite !== undefined && (
              <button
                type="button"
                onClick={onOverwrite}
                style={{
                  ...secondaryButtonStyle,
                  color: "var(--color-danger, #ef4444)",
                  borderColor: "var(--color-danger, #ef4444)",
                }}
                data-testid="library-page-editor-conflict-overwrite"
              >
                Overwrite
              </button>
            )}
          </div>
        </div>
      )}

      <div style={toolbarStyle}>
        <div style={viewToggleStyle} role="tablist" aria-label="Editor view">
          {(["edit", "split", "preview"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              role="tab"
              aria-selected={view === kind}
              onClick={() => setView(kind)}
              style={viewToggleButtonStyle(view === kind)}
              data-testid={`library-page-editor-view-${kind}`}
            >
              {kind === "edit"
                ? "Edit"
                : kind === "preview"
                  ? "Preview"
                  : "Split"}
            </button>
          ))}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={statusChipStyle(saveStatus.kind)}
            data-testid="library-page-editor-save-chip"
            data-save-status={saveStatus.kind}
            aria-live="polite"
          >
            {statusLabel(saveStatus, nowFn)}
          </span>
          {onCancel !== undefined && (
            <button
              type="button"
              onClick={onCancel}
              style={secondaryButtonStyle}
              data-testid="library-page-editor-cancel"
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            style={primaryButtonStyle(busy)}
            data-testid="library-page-editor-save"
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div style={view === "split" ? splitStyle : { display: "flex" }}>
        {showEdit && (
          <textarea
            value={markdown}
            onChange={(e) => onChange({ title, markdown: e.target.value })}
            placeholder="Write in markdown…"
            aria-label="Page body markdown"
            style={textareaStyle}
            data-testid="library-page-editor-textarea"
            spellCheck
          />
        )}
        {showPreview && (
          <div
            style={previewPaneStyle}
            data-testid="library-page-editor-preview"
            aria-label="Rendered preview"
          >
            {markdown.length === 0 ? (
              <span
                style={{
                  color: "var(--color-text-subtle)",
                  fontStyle: "italic",
                }}
              >
                Preview appears here.
              </span>
            ) : (
              <Streamdown mode="static">{markdown}</Streamdown>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
