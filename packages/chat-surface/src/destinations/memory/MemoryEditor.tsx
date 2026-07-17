// <MemoryEditor /> — create / edit form for a single MemoryItem.
//
// Source:
//   docs/atlas-new-design/destinations/team-memory-cmdk-prd.md §7.2:
//     "MemoryEditor.tsx — title + body (markdown) + scope toggle + tags."
//
// Invariants:
//   - Pure presentation. The host owns persistence. On Save we hand the
//     host either a `CreateMemoryRequest` (when no `initial` was passed)
//     or an `UpdateMemoryRequest` patch (only-changed-fields — never
//     fields that match the initial value, so PATCH stays minimal).
//   - SP-1 primitives only. Scope toggle reuses `<FilterTabs>` (the
//     same generic the destination uses) — one tablist primitive
//     across destinations (cross-audit §1.6).
//   - Wire types from `@0x-copilot/api-types/memory` only.
//   - No markdown preview here; the editor only collects raw markdown.
//     The host can render `<MemoryDetailView>` side-by-side if it wants
//     a live preview.

import {
  useCallback,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
} from "react";

import type {
  CreateMemoryRequest,
  MemoryItem,
  MemoryKind,
  MemoryScope,
  UpdateMemoryRequest,
} from "@0x-copilot/api-types";

import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";

// ===========================================================================
// Public props
// ===========================================================================

/**
 * Save payload — discriminated on whether the editor was opened against
 * an existing memory.
 *
 *   * `mode: "create"` — `body` is a full `CreateMemoryRequest`.
 *   * `mode: "update"` — `id` is the existing memory id; `patch` is an
 *     `UpdateMemoryRequest` carrying only the fields the user actually
 *     changed (per §4.2 — PATCH is partial-update). The host posts
 *     this verbatim.
 */
export type MemoryEditorSavePayload =
  | { readonly mode: "create"; readonly body: CreateMemoryRequest }
  | {
      readonly mode: "update";
      readonly id: MemoryItem["id"];
      readonly patch: UpdateMemoryRequest;
    };

export interface MemoryEditorProps {
  /** When supplied, the editor seeds from this memory and emits an
   *  `UpdateMemoryRequest` patch. Otherwise emits a `CreateMemoryRequest`. */
  readonly initial?: MemoryItem;

  readonly onSave: (payload: MemoryEditorSavePayload) => void;
  readonly onCancel?: () => void;

  /** Saving=true disables the Save button + the form. */
  readonly saving?: boolean;
  /** Validation / server error rendered above the form. */
  readonly error?: string;
}

// ===========================================================================
// Implementation
// ===========================================================================

const KIND_ORDER: ReadonlyArray<MemoryKind> = ["skill", "fact", "preference"];
const KIND_LABEL: Readonly<Record<MemoryKind, string>> = {
  skill: "Skill",
  fact: "Fact",
  preference: "Preference",
};

const SCOPE_ORDER: ReadonlyArray<MemoryScope> = ["user", "workspace"];
const SCOPE_LABEL: Readonly<Record<MemoryScope, string>> = {
  user: "My",
  workspace: "Workspace",
};

export function MemoryEditor(props: MemoryEditorProps): ReactElement {
  const { initial, onSave, onCancel, saving = false, error } = props;

  const [title, setTitle] = useState<string>(initial?.title ?? "");
  const [body, setBody] = useState<string>(initial?.body ?? "");
  const [kind, setKind] = useState<MemoryKind>(initial?.kind ?? "fact");
  const [scope, setScope] = useState<MemoryScope>(initial?.scope ?? "user");
  const [tagsInput, setTagsInput] = useState<string>(
    (initial?.tags ?? []).join(", "),
  );

  const parseTags = useCallback((raw: string): ReadonlyArray<string> => {
    return raw
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (saving) return;
    if (title.trim().length === 0 || body.trim().length === 0) return;

    const tags = parseTags(tagsInput);

    if (initial === undefined) {
      // === Create ===
      const createBody: CreateMemoryRequest = {
        scope,
        kind,
        title: title.trim(),
        body: body.trim(),
        // Only include tags when the user supplied at least one — keeps
        // the request body minimal.
        ...(tags.length > 0 ? { tags } : {}),
      };
      onSave({ mode: "create", body: createBody });
      return;
    }

    // === Update — only-changed-fields ===
    const patch: {
      -readonly [K in keyof UpdateMemoryRequest]?: UpdateMemoryRequest[K];
    } = {};
    if (title.trim() !== initial.title) {
      patch.title = title.trim();
    }
    if (body.trim() !== initial.body) {
      patch.body = body.trim();
    }
    if (kind !== initial.kind) {
      patch.kind = kind;
    }
    if (scope !== initial.scope) {
      patch.scope = scope;
    }
    if (!sameTags(tags, initial.tags)) {
      patch.tags = tags;
    }

    onSave({
      mode: "update",
      id: initial.id,
      patch: patch as UpdateMemoryRequest,
    });
  };

  const kindOptions: ReadonlyArray<FilterTabOption<MemoryKind>> =
    KIND_ORDER.map((slug) => ({ slug, label: KIND_LABEL[slug] }));
  const scopeOptions: ReadonlyArray<FilterTabOption<MemoryScope>> =
    SCOPE_ORDER.map((slug) => ({ slug, label: SCOPE_LABEL[slug] }));

  const submitDisabled =
    saving || title.trim().length === 0 || body.trim().length === 0;

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="memory-editor"
      data-mode={initial === undefined ? "create" : "update"}
      aria-label={initial === undefined ? "Add memory" : "Edit memory"}
      style={formStyle}
    >
      <h2 style={headingStyle}>
        {initial === undefined ? "Add memory" : "Edit memory"}
      </h2>

      {error !== undefined && error.length > 0 ? (
        <div role="alert" data-testid="memory-editor-error" style={errorStyle}>
          {error}
        </div>
      ) : null}

      <label style={labelStyle}>
        <span style={labelTextStyle}>Title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          data-testid="memory-editor-title"
          aria-label="Memory title"
          style={inputStyle}
          disabled={saving}
          required
          maxLength={200}
        />
      </label>

      <label style={labelStyle}>
        <span style={labelTextStyle}>Body (markdown)</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          data-testid="memory-editor-body"
          aria-label="Memory body"
          style={textareaStyle}
          disabled={saving}
          required
          rows={8}
        />
      </label>

      <div style={fieldRowStyle}>
        <div style={fieldGroupStyle}>
          <span style={labelTextStyle}>Kind</span>
          <FilterTabs<MemoryKind>
            value={kind}
            onChange={setKind}
            options={kindOptions}
            ariaLabel="Memory kind"
            idPrefix="memory-editor-kind"
          />
        </div>
        <div style={fieldGroupStyle}>
          <span style={labelTextStyle}>Scope</span>
          <FilterTabs<MemoryScope>
            value={scope}
            onChange={setScope}
            options={scopeOptions}
            ariaLabel="Memory scope"
            idPrefix="memory-editor-scope"
          />
        </div>
      </div>

      <label style={labelStyle}>
        <span style={labelTextStyle}>Tags (comma-separated)</span>
        <input
          type="text"
          value={tagsInput}
          onChange={(e) => setTagsInput(e.target.value)}
          data-testid="memory-editor-tags"
          aria-label="Memory tags"
          placeholder="e.g. python, billing, atlas"
          style={inputStyle}
          disabled={saving}
        />
      </label>

      <div style={actionRowStyle}>
        {onCancel !== undefined ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            style={cancelButtonStyle}
            data-testid="memory-editor-cancel"
          >
            Cancel
          </button>
        ) : null}
        <button
          type="submit"
          disabled={submitDisabled}
          aria-disabled={submitDisabled}
          style={{
            ...saveButtonStyle,
            opacity: submitDisabled ? 0.6 : 1,
            cursor: submitDisabled ? "not-allowed" : "pointer",
          }}
          data-testid="memory-editor-save"
        >
          {saving ? "Saving…" : initial === undefined ? "Add memory" : "Save"}
        </button>
      </div>
    </form>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function sameTags(a: ReadonlyArray<string>, b: ReadonlyArray<string>): boolean {
  if (a.length !== b.length) return false;
  // Tag sets are not order-sensitive but the wire shape is a positional
  // array; we use a multiset compare so re-ordering doesn't mark the
  // field as changed.
  const counts = new Map<string, number>();
  for (const t of a) counts.set(t, (counts.get(t) ?? 0) + 1);
  for (const t of b) {
    const c = counts.get(t);
    if (c === undefined) return false;
    if (c === 1) counts.delete(t);
    else counts.set(t, c - 1);
  }
  return counts.size === 0;
}

// ===========================================================================
// Styles
// ===========================================================================

const formStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  padding: 16,
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-md, 12px)",
  background: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
  maxWidth: 720,
};

const headingStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 16px)",
  fontWeight: 600,
};

const errorStyle: CSSProperties = {
  background:
    "color-mix(in srgb, var(--color-danger, #d24545) 16%, transparent)",
  color: "var(--color-danger, #d24545)",
  border: "1px solid var(--color-danger, #d24545)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "8px 10px",
  fontSize: "var(--font-size-sm, 13px)",
};

const labelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelTextStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-muted, #b4b4b8)",
};

const inputStyle: CSSProperties = {
  height: 32,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-surface, #161617)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  outline: "none",
};

const textareaStyle: CSSProperties = {
  padding: "10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-surface, #161617)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontFamily: "inherit",
  outline: "none",
  resize: "vertical",
};

const fieldRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 16,
};

const fieldGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  minWidth: 220,
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  justifyContent: "flex-end",
};

const cancelButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "transparent",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-sm, 13px)",
  cursor: "pointer",
};

const saveButtonStyle: CSSProperties = {
  height: 32,
  padding: "0 14px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
};
