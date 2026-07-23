// TemplateEditor — P6.5-B1
//
// Edit form for a `ProjectTemplate`'s metadata + read-only preview of
// its snapshot contents. Per projects-extensions-prd §7.5, the snapshot
// is immutable post-create — only name + description are editable.
//
// The snapshot preview surfaces:
//   - default member user-ids (count)
//   - default connector allowlist (tri-mode summary)
//   - seeded todos (text list)
//   - seeded routines (name + trigger summary)
//   - color/icon defaults
//
// Pure presentation; `onSave` is host-owned.
//
// SP-1 primitives:
//   - <StatusPill> for the connector-mode summary
//   - <EmptyState> for empty seeded-list sub-sections

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
} from "react";

import { EmptyState } from "../../shell/EmptyState";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { projectHueRamp } from "../_shared";

import type { ProjectTemplateId } from "./TemplateGallery";

// ── Tokens ───────────────────────────────────────────────────────────

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";

// ── Public types ─────────────────────────────────────────────────────

/** Seeded-todo line — text only summary for preview. Matches §7.2 shape. */
export interface TemplateEditorSeededTodo {
  readonly text: string;
  readonly priority: "low" | "normal" | "high" | null;
  readonly relativeDueDays: number | null;
}

/** Seeded routine — minimal preview shape. Triggers are stripped to a
 *  human-readable summary; the editor never modifies them (snapshot is
 *  immutable). */
export interface TemplateEditorSeededRoutine {
  readonly name: string;
  readonly description: string;
  readonly triggerSummary: string;
}

/** Snapshot preview view-model (read-only). Subset of §7.2's full
 *  `ProjectTemplateSnapshot` reshaped for human display. */
export interface TemplateEditorSnapshot {
  readonly memberCount: number;
  /** Tri-mode allowlist value (§5.1 / §7.2):
   *   - `null` → inherit
   *   - `[]` → none
   *   - `[slug, ...]` → allowlist */
  readonly defaultConnectorAllowlist: ReadonlyArray<string> | null;
  readonly colorHue: number | null;
  readonly iconEmoji: string | null;
  readonly seededTodos: ReadonlyArray<TemplateEditorSeededTodo>;
  readonly seededRoutines: ReadonlyArray<TemplateEditorSeededRoutine>;
}

export interface TemplateEditorValue {
  readonly id: ProjectTemplateId;
  readonly name: string;
  readonly description: string;
  readonly snapshot: TemplateEditorSnapshot;
}

export interface TemplateEditorSavePayload {
  readonly name: string;
  readonly description: string;
}

export interface TemplateEditorProps {
  readonly value: TemplateEditorValue;
  /** Owner-only edit gate; non-owners see read-only view. */
  readonly canEdit?: boolean;
  readonly onSave: (payload: TemplateEditorSavePayload) => Promise<void>;
  readonly onCancel?: () => void;
  /** Soft-delete (§7.3). */
  readonly onDelete?: () => void;
  /** Optional toggle for the snapshot expander; default closed. */
  readonly snapshotDefaultOpen?: boolean;
}

// ── Helpers ──────────────────────────────────────────────────────────

function connectorSummary(allowlist: ReadonlyArray<string> | null): {
  tone: StatusTone;
  label: string;
} {
  if (allowlist === null) return { tone: "muted", label: "Inherit defaults" };
  if (allowlist.length === 0)
    return { tone: "warning", label: "No connectors" };
  return {
    tone: "info",
    label: `${allowlist.length} connector${allowlist.length === 1 ? "" : "s"}`,
  };
}

// ── Snapshot preview ─────────────────────────────────────────────────

function SnapshotPreview({
  snapshot,
}: {
  snapshot: TemplateEditorSnapshot;
}): ReactElement {
  const summary = useMemo(
    () => connectorSummary(snapshot.defaultConnectorAllowlist),
    [snapshot.defaultConnectorAllowlist],
  );

  const sectionTitle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    color: TEXT_SECONDARY,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  };
  const rowStyle: CSSProperties = {
    padding: "8px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 8,
    fontSize: "var(--font-size-xs)",
    color: TEXT_PRIMARY,
    backgroundColor: PANEL_BACKGROUND,
  };
  const metaStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    color: TEXT_FAINT,
    marginTop: 2,
  };
  // PRD-10 D3 — the ONE shared hue ramp (no `hsl(...)` literal in this dir).
  const snapshotIconRamp = projectHueRamp(snapshot.colorHue ?? 200);
  return (
    <div
      data-testid="template-editor-snapshot-preview"
      style={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={sectionTitle}>Defaults</span>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              backgroundColor: snapshotIconRamp.background,
              border: snapshotIconRamp.border,
              color: snapshotIconRamp.color,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "var(--font-size-xl)",
            }}
            data-testid="template-editor-snapshot-icon"
          >
            {snapshot.iconEmoji ?? "📁"}
          </div>
          <span
            style={{ fontSize: "var(--font-size-sm)", color: TEXT_PRIMARY }}
          >
            {snapshot.memberCount} suggested member
            {snapshot.memberCount === 1 ? "" : "s"}
          </span>
          <StatusPill status={summary.tone} label={summary.label} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={sectionTitle}>
          Seeded todos ({snapshot.seededTodos.length})
        </span>
        {snapshot.seededTodos.length === 0 ? (
          <EmptyState title="No seeded todos" />
        ) : (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}
            data-testid="template-editor-snapshot-todos"
          >
            {snapshot.seededTodos.map((todo, i) => (
              <li
                key={`todo-${i}`}
                style={rowStyle}
                data-testid="template-editor-snapshot-todo"
              >
                <div>{todo.text}</div>
                <div style={metaStyle}>
                  {todo.priority ?? "no-priority"}
                  {todo.relativeDueDays !== null
                    ? ` · due in ${todo.relativeDueDays}d`
                    : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={sectionTitle}>
          Seeded routines ({snapshot.seededRoutines.length})
        </span>
        {snapshot.seededRoutines.length === 0 ? (
          <EmptyState title="No seeded routines" />
        ) : (
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}
            data-testid="template-editor-snapshot-routines"
          >
            {snapshot.seededRoutines.map((rt, i) => (
              <li
                key={`rt-${i}`}
                style={rowStyle}
                data-testid="template-editor-snapshot-routine"
              >
                <div style={{ fontWeight: 600 }}>{rt.name}</div>
                <div style={metaStyle}>{rt.triggerSummary}</div>
                {rt.description.length > 0 ? (
                  <div style={metaStyle}>{rt.description}</div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────

export function TemplateEditor(props: TemplateEditorProps): ReactElement {
  const {
    value,
    canEdit = true,
    onSave,
    onCancel,
    onDelete,
    snapshotDefaultOpen = false,
  } = props;

  const [name, setName] = useState(value.name);
  const [description, setDescription] = useState(value.description);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snapshotOpen, setSnapshotOpen] =
    useState<boolean>(snapshotDefaultOpen);

  const dirty = name !== value.name || description !== value.description;

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      if (event !== undefined) event.preventDefault();
      if (!canEdit || !dirty || submitting) return;
      const trimmed = name.trim();
      if (trimmed.length === 0) {
        setError("Name is required.");
        return;
      }
      setSubmitting(true);
      setError(null);
      try {
        await onSave({ name: trimmed, description: description.trim() });
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Failed to save template";
        setError(message);
      } finally {
        setSubmitting(false);
      }
    },
    [canEdit, description, dirty, name, onSave, submitting],
  );

  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 12,
    padding: 20,
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontWeight: 500,
  };
  const inputStyle: CSSProperties = {
    height: 36,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
    outline: "none",
  };
  const textareaStyle: CSSProperties = {
    minHeight: 80,
    padding: "10px 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
    outline: "none",
    fontFamily: "inherit",
    resize: "vertical",
  };
  const footerRow: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginTop: 4,
  };
  const cancelStyle: CSSProperties = {
    height: 34,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-sm)",
    cursor: "pointer",
  };
  const submitStyle: CSSProperties = {
    height: 34,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    cursor: "pointer",
    opacity: !canEdit || !dirty || submitting ? 0.6 : 1,
  };
  const deleteStyle: CSSProperties = {
    height: 34,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: DANGER,
    fontSize: "var(--font-size-sm)",
    cursor: "pointer",
  };
  const expanderToggle: CSSProperties = {
    height: 28,
    padding: "0 10px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-xs)",
    cursor: "pointer",
  };

  return (
    <form
      onSubmit={handleSubmit}
      style={wrapperStyle}
      data-testid="template-editor"
      data-template-id={value.id}
      data-dirty={dirty}
      aria-label="Project template editor"
    >
      <h3
        style={{ margin: 0, fontSize: "var(--font-size-lg)", fontWeight: 600 }}
      >
        Edit template
      </h3>

      <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Name</span>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!canEdit || submitting}
          maxLength={80}
          style={inputStyle}
          data-testid="template-editor-name-input"
          aria-label="Template name"
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Description</span>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={!canEdit || submitting}
          maxLength={200}
          style={textareaStyle}
          data-testid="template-editor-description-input"
          aria-label="Template description"
        />
      </label>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span style={labelStyle}>Snapshot (read-only — immutable)</span>
        <button
          type="button"
          onClick={() => setSnapshotOpen((s) => !s)}
          style={expanderToggle}
          data-testid="template-editor-snapshot-toggle"
          aria-expanded={snapshotOpen}
          aria-controls="template-editor-snapshot-body"
        >
          {snapshotOpen ? "Hide snapshot" : "View snapshot details"}
        </button>
      </div>
      {snapshotOpen ? (
        <div
          id="template-editor-snapshot-body"
          data-testid="template-editor-snapshot-body"
        >
          <SnapshotPreview snapshot={value.snapshot} />
        </div>
      ) : null}

      {error !== null ? (
        <div
          role="alert"
          style={{ color: DANGER, fontSize: "var(--font-size-xs)" }}
          data-testid="template-editor-error"
        >
          {error}
        </div>
      ) : null}

      <div style={footerRow}>
        {onDelete !== undefined ? (
          <button
            type="button"
            onClick={onDelete}
            disabled={submitting}
            style={deleteStyle}
            data-testid="template-editor-delete"
          >
            Delete template
          </button>
        ) : null}
        <div style={{ flex: 1 }} />
        {onCancel !== undefined ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            style={cancelStyle}
            data-testid="template-editor-cancel"
          >
            Cancel
          </button>
        ) : null}
        {canEdit ? (
          <button
            type="submit"
            disabled={!dirty || submitting}
            style={submitStyle}
            data-testid="template-editor-save"
            aria-label="Save template"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        ) : null}
      </div>
    </form>
  );
}
