// PRD-09c — edit-on-surface overlay (the *edit* in accept/decline/edit).
//
// Source: docs/plan/generative-ui/PRD-09-edit-and-commit.md ("Frontend
// (chat-surface)"). This is the HOST-owned edit UI that `TcSurfaceMount` mounts
// in its `editSlot` OVER the pure adapter — the archetype renderers stay
// input-free (D28). It never fetches and touches no substrate global
// (window/fetch/localStorage/EventSource): it takes the proposed `SurfaceDiff`
// plus `onSubmit`/`onCancel` callbacks and derives a `SurfaceEdits` payload the
// host POSTs as `{ decision: "approve_with_edits", edits }` — the SAME decision
// endpoint + optimistic machinery the plain approve/reject path uses.
//
// v1 edit surfaces (PRD non-goal guard): message body + record fields only.
//   - MessageEditForm: to/subject read-only, a body <textarea> seeded from the
//     proposal, and a PRD-06 `DiffText` hunk list (`onHunkToggle`) → derives
//     `accepted_hunk_ids` (the subset of proposed hunks the reviewer keeps).
//   - RecordEditForm: one input per changed field, seeded with the proposed
//     value → derives `fields`.
//
// The SERVER (ai-backend 09b) is the authority on the merge (final = proposal
// ⊕ edits); this component only carries the reviewer's intent on the wire.

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  SurfaceDiff,
  SurfaceEdits,
  SurfaceFieldChange,
  SurfaceSpec,
} from "@0x-copilot/api-types";

import { DiffText } from "../../textdiff/DiffText";
import { wordDiff, type DiffHunk } from "../../textdiff/wordDiff";

// Field-path hints that mark a change as the free-text *body* of a message
// (the prose the word diff runs over). Matched against the last dotted segment,
// case-insensitively; ties (and no match) fall back to the longest text.
const BODY_HINTS = ["body", "snippet", "content", "text", "message"] as const;

export interface EditOverlayProps {
  /** Surface archetype (the uri scheme): `"message"` | `"record"` | … . Selects
   * the per-archetype form; anything non-message renders the generic field form. */
  readonly archetype: string;
  /** The pending proposal being edited (PRD-01 `SurfaceDiff`). */
  readonly diff: SurfaceDiff;
  /** Display title for the overlay header (best-effort from the proposal). */
  readonly title?: string;
  /** Commit the reviewer's edits — the host POSTs `approve_with_edits`. */
  readonly onSubmit: (edits: SurfaceEdits) => void;
  /** Dismiss without committing — returns to the pending diff (no POST). */
  readonly onCancel: () => void;
}

/**
 * The host-owned edit overlay. Self-contained: it seeds a `SurfaceEdits` draft
 * from the proposal, renders the archetype form as a controlled child, and
 * hands the current draft to `onSubmit`. Presentational only — no ports, no
 * fetching, no substrate globals.
 */
export function EditOverlay(props: EditOverlayProps): ReactElement {
  const { archetype, diff, title, onSubmit, onCancel } = props;
  const [edits, setEdits] = useState<SurfaceEdits>(() =>
    seedEdits(archetype, diff),
  );
  const isMessage = archetype === "message";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Suggest changes"
      data-testid="surface-edit-overlay"
      data-archetype={archetype}
      style={overlayStyle}
    >
      <div style={panelStyle}>
        <header style={headerStyle}>
          <span style={kickerStyle}>Suggest changes</span>
          <span style={titleStyle} data-testid="surface-edit-title">
            {title && title.trim() !== "" ? title : "Proposed changes"}
          </span>
        </header>

        <div style={bodyStyle}>
          {isMessage ? (
            <MessageEditForm
              diff={diff}
              edits={edits}
              onEditsChange={setEdits}
            />
          ) : (
            <RecordEditForm
              diff={diff}
              edits={edits}
              onEditsChange={setEdits}
            />
          )}
        </div>

        <footer style={footerStyle}>
          <button
            type="button"
            data-testid="surface-edit-cancel"
            onClick={onCancel}
            style={secondaryButtonStyle}
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="surface-edit-submit"
            onClick={() => onSubmit(edits)}
            style={primaryButtonStyle}
          >
            Approve with edits
          </button>
        </footer>
      </div>
    </div>
  );
}

// ============================================================
// MessageEditForm — body textarea + PRD-06 hunk toggles
// ============================================================

export interface MessageEditFormProps {
  readonly diff: SurfaceDiff;
  readonly edits: SurfaceEdits;
  readonly onEditsChange: (edits: SurfaceEdits) => void;
}

/**
 * The message-archetype editor: read-only to/subject rows over the proposed
 * meta changes, a body `<textarea>` seeded from the proposed body, and the
 * PRD-06 `DiffText` word diff wired with `onHunkToggle` so the reviewer keeps or
 * excludes individual hunks. Both the edited body and the kept-hunk set flow up
 * as `SurfaceEdits`.
 */
export function MessageEditForm(props: MessageEditFormProps): ReactElement {
  const { diff, edits, onEditsChange } = props;
  const changes = useMemo(() => changeList(diff), [diff]);
  const bodyChange = useMemo(() => pickBodyChange(changes), [changes]);
  const metaChanges = useMemo(
    () => changes.filter((change) => change !== bodyChange),
    [changes, bodyChange],
  );
  const hunks = useMemo<readonly DiffHunk[]>(() => {
    if (bodyChange === null) {
      return [];
    }
    return wordDiff(
      toEditableString(bodyChange.old),
      toEditableString(bodyChange.new),
    );
  }, [bodyChange]);
  const changedHunks = useMemo(
    () => hunks.filter((hunk) => hunk.kind !== "equal"),
    [hunks],
  );

  const accepted = new Set(edits.accepted_hunk_ids ?? []);

  const handleBodyChange = (value: string): void => {
    onEditsChange({ ...edits, body: value });
  };
  const handleHunkToggle = (id: string): void => {
    const next = new Set(accepted);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    onEditsChange({ ...edits, accepted_hunk_ids: [...next] });
  };

  return (
    <div data-testid="message-edit-form" style={formStyle}>
      {metaChanges.map((change, index) => (
        <label key={`${change.field}:${index}`} style={fieldRowStyle}>
          <span style={fieldLabelStyle}>
            {labelFor(change.field, diff.spec)}
          </span>
          <input
            type="text"
            readOnly
            disabled
            value={toEditableString(change.new)}
            data-testid={`message-edit-meta-${change.field}`}
            style={readonlyInputStyle}
          />
        </label>
      ))}

      <label style={fieldRowStyle}>
        <span style={fieldLabelStyle}>Body</span>
        <textarea
          data-testid="message-edit-body"
          value={edits.body ?? ""}
          onChange={(event) => handleBodyChange(event.target.value)}
          rows={8}
          style={textareaStyle}
        />
      </label>

      {changedHunks.length > 0 ? (
        <div style={hunkSectionStyle}>
          <span style={fieldLabelStyle}>Proposed edits</span>
          {/* PRD-06 DiffText — click a red/green hunk to keep or exclude it. */}
          <div
            data-testid="message-edit-hunks"
            data-accepted-count={
              changedHunks.filter((hunk) => accepted.has(hunk.id)).length
            }
            data-changed-count={changedHunks.length}
            style={hunkDiffStyle}
          >
            <DiffText hunks={hunks} onHunkToggle={handleHunkToggle} />
          </div>
          {/* Read-only mirror of which hunks are kept/excluded (a11y + clarity;
              the DiffText above is the interactive toggle). */}
          <ul
            style={hunkStatusListStyle}
            data-testid="message-edit-hunk-status"
          >
            {changedHunks.map((hunk) => {
              const kept = accepted.has(hunk.id);
              return (
                <li
                  key={hunk.id}
                  data-testid={`message-edit-hunk-status-${hunk.id}`}
                  data-accepted={kept ? "true" : "false"}
                  style={hunkStatusRowStyle}
                >
                  <span style={hunkKindStyle(hunk.kind)}>
                    {hunk.kind === "insert" ? "＋" : "－"}
                  </span>
                  <span style={kept ? hunkTextStyle : hunkTextExcludedStyle}>
                    {hunk.text}
                  </span>
                  <span style={hunkStateStyle}>
                    {kept ? "kept" : "excluded"}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

// ============================================================
// RecordEditForm — one input per changed field
// ============================================================

export interface RecordEditFormProps {
  readonly diff: SurfaceDiff;
  readonly edits: SurfaceEdits;
  readonly onEditsChange: (edits: SurfaceEdits) => void;
}

/**
 * The record-archetype editor: one text input per proposed field change, seeded
 * with the proposed value, with the previous value shown for reference. Edits
 * flow up as `SurfaceEdits.fields` keyed by the surface field path.
 */
export function RecordEditForm(props: RecordEditFormProps): ReactElement {
  const { diff, edits, onEditsChange } = props;
  const changes = useMemo(() => changeList(diff), [diff]);
  const fields = edits.fields ?? {};

  const handleFieldChange = (field: string, value: string): void => {
    onEditsChange({ ...edits, fields: { ...fields, [field]: value } });
  };

  return (
    <div data-testid="record-edit-form" style={formStyle}>
      {changes.length === 0 ? (
        <p style={emptyStyle} data-testid="record-edit-empty">
          No fields to edit.
        </p>
      ) : (
        changes.map((change, index) => (
          <label key={`${change.field}:${index}`} style={fieldRowStyle}>
            <span style={fieldLabelStyle}>
              {labelFor(change.field, diff.spec)}
            </span>
            <input
              type="text"
              data-testid={`record-edit-field-${change.field}`}
              value={fields[change.field] ?? toEditableString(change.new)}
              onChange={(event) =>
                handleFieldChange(change.field, event.target.value)
              }
              style={inputStyle}
            />
            <span
              data-testid={`record-edit-old-${change.field}`}
              style={oldValueStyle}
            >
              was {toEditableString(change.old) || "—"}
            </span>
          </label>
        ))
      )}
    </div>
  );
}

// ============================================================
// Seeding + change helpers
// ============================================================

/**
 * Derive the initial `SurfaceEdits` draft from the proposal so an immediate
 * Submit (no reviewer changes) carries the full proposed artifact.
 * - message → the proposed body + every changed hunk id accepted.
 * - record  → the proposed value for every changed field.
 */
export function seedEdits(archetype: string, diff: SurfaceDiff): SurfaceEdits {
  const changes = changeList(diff);
  if (archetype === "message") {
    const bodyChange = pickBodyChange(changes);
    const newBody = bodyChange === null ? "" : toEditableString(bodyChange.new);
    const oldBody = bodyChange === null ? "" : toEditableString(bodyChange.old);
    const acceptedHunkIds = wordDiff(oldBody, newBody)
      .filter((hunk) => hunk.kind !== "equal")
      .map((hunk) => hunk.id);
    return { body: newBody, accepted_hunk_ids: acceptedHunkIds };
  }
  const fields: Record<string, string> = {};
  for (const change of changes) {
    fields[change.field] = toEditableString(change.new);
  }
  return { fields };
}

/** Defensive read of the change list — the diff is untrusted tool output. */
function changeList(diff: SurfaceDiff): readonly SurfaceFieldChange[] {
  const changes = (diff as { changes?: unknown }).changes;
  if (!Array.isArray(changes)) {
    return [];
  }
  return changes.filter(
    (change): change is SurfaceFieldChange =>
      typeof change === "object" &&
      change !== null &&
      typeof (change as SurfaceFieldChange).field === "string",
  );
}

/**
 * Pick the change that carries the message *body* — the prose the word diff
 * runs over. Prefers a field whose last segment hints "body"/"snippet"/…,
 * tie-broken (and otherwise) by the longest combined text. Returns `null` for
 * an empty change list.
 */
function pickBodyChange(
  changes: readonly SurfaceFieldChange[],
): SurfaceFieldChange | null {
  if (changes.length === 0) {
    return null;
  }
  let best: SurfaceFieldChange = changes[0];
  let bestScore = bodyScore(changes[0]);
  for (let i = 1; i < changes.length; i++) {
    const score = bodyScore(changes[i]);
    if (score > bestScore) {
      best = changes[i];
      bestScore = score;
    }
  }
  return best;
}

function bodyScore(change: SurfaceFieldChange): number {
  const segment = lastSegment(change.field).toLowerCase();
  const hinted = BODY_HINTS.some((hint) => segment.includes(hint));
  const textLength =
    toEditableString(change.new).length + toEditableString(change.old).length;
  return (hinted ? 1_000_000 : 0) + textLength;
}

function lastSegment(field: string): string {
  const parts = field.split(".");
  return parts[parts.length - 1] ?? field;
}

/** Map a field path to a display label via the spec, else a title-cased tail. */
function labelFor(field: string, spec: SurfaceSpec | undefined): string {
  const fromSpec = spec?.fields?.find((entry) => entry.path === field)?.label;
  if (fromSpec !== undefined && fromSpec !== "") {
    return fromSpec;
  }
  const tail = lastSegment(field).replace(/[_-]+/g, " ").trim();
  if (tail === "") {
    return field;
  }
  return tail.charAt(0).toUpperCase() + tail.slice(1);
}

/** Coerce an untrusted `unknown` change value to an editable string. */
function toEditableString(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

// ============================================================
// Styles (design-system tokens; theme-aware via CSS vars)
// ============================================================

const overlayStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  alignItems: "stretch",
  justifyContent: "center",
  padding: 16,
  // No fallback — --color-scrim is a real design-system token now. This site
  // used to fall back to rgba(8,10,14,0.6) while Modal fell back to
  // rgb(0 0 0/0.54): two different colours for one role.
  background: "var(--color-scrim)",
  zIndex: 5,
  overflow: "auto",
};

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: "100%",
  maxWidth: 640,
  maxHeight: "100%",
  borderRadius: 12,
  border: "1px solid var(--color-border, #2a2d31)",
  background: "var(--color-surface, #181a1c)",
  color: "var(--color-text, #f4f5f6)",
  boxShadow: "0 12px 40px rgba(0,0,0,0.4)",
  overflow: "hidden",
  fontFamily: "var(--font-sans)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "14px 16px",
  borderBottom: "1px solid var(--color-border, #2a2d31)",
};

const kickerStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
  padding: 16,
};

const footerStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  padding: "12px 16px",
  borderTop: "1px solid var(--color-border, #2a2d31)",
};

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const fieldRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const fieldLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--color-border, #2a2d31)",
  background: "var(--color-bg, #0e1015)",
  color: "var(--color-text, #f4f5f6)",
  fontSize: "var(--font-size-sm, 13px)",
  fontFamily: "inherit",
};

const readonlyInputStyle: CSSProperties = {
  ...inputStyle,
  color: "var(--color-text-muted, #9aa0a6)",
  cursor: "not-allowed",
};

const textareaStyle: CSSProperties = {
  ...inputStyle,
  minHeight: 140,
  resize: "vertical",
  lineHeight: 1.5,
};

const oldValueStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-muted, #9aa0a6)",
};

const hunkSectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const hunkDiffStyle: CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--color-border, #2a2d31)",
  background: "var(--color-bg, #0e1015)",
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.6,
};

const hunkStatusListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const hunkStatusRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 8,
  fontSize: "var(--font-size-2xs, 11px)",
};

function hunkKindStyle(kind: DiffHunk["kind"]): CSSProperties {
  return {
    fontWeight: 700,
    color:
      kind === "insert"
        ? "var(--color-accent, #5fb2ec)"
        : "var(--color-danger, #f0764f)",
  };
}

const hunkTextStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--color-text, #f4f5f6)",
};

const hunkTextExcludedStyle: CSSProperties = {
  ...hunkTextStyle,
  textDecoration: "line-through",
  color: "var(--color-text-muted, #9aa0a6)",
};

const hunkStateStyle: CSSProperties = {
  flexShrink: 0,
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #9aa0a6)",
};

const primaryButtonStyle: CSSProperties = {
  background: "var(--color-accent, #5fb2ec)",
  color: "#101113",
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text, #f4f5f6)",
  border: "1px solid var(--color-border, #2a2d31)",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};
