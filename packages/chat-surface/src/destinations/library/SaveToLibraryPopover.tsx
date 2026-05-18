// <SaveToLibraryPopover> — the shared cross-destination "Save to Library"
// widget (P7-B1).
//
// Source: library-prd §3.6 — the load-bearing UX of the destination.
// One component, five call sites (§3.6.1):
//
//   1. Tool-result card (Chats destination)              [Save to Library]
//   2. Agent message action menu (Chats destination)     [Save as page]
//   3. Chat-thread pin (Chats destination)               [Save thread summary as page]
//   4. Run-completion notification (Inbox destination)   [Save output to Library]
//   5. Routine output (Routines destination)             — direct write,
//      surfaced as a chip on routine detail using this same popover for
//      the rename / re-tag / re-file affordance.
//
// Hard correctness rules (the reason this widget exists at all — DRY):
//   - One canonical form so every "Save to Library" call site looks +
//     behaves identically. No duplicated UI.
//   - Pure presentation: no fetch, no router calls. The host translates
//     `onSubmit({ kind, name, project_id, tags })` into the appropriate
//     `POST /v1/library/{files,pages,datasets}` request (P7-A1 / P7-C).
//   - Kind override defaults to the call-site's recommendation but the
//     user can change it (per library-prd §3.6.2).
//   - Project picker is the same `<ProjectFilterChip>` Projects P6
//     established — single source of truth for "pick a project".
//   - Source preview is a read-only disclosure the host fills with the
//     bytes / markdown / first-rows that will be saved (we don't reach
//     into kind-specific previews here — the host knows what's about to
//     be saved better than the popover does).

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId } from "@enterprise-search/api-types";

import {
  ProjectFilterChip,
  type ProjectFilterChipOption,
} from "../projects/ProjectFilterChip";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type { LibraryItemKind, SaveToLibrarySource } from "./_library-stub";

// ===========================================================================
// Public props
// ===========================================================================

export interface SaveToLibrarySubmit {
  readonly kind: LibraryItemKind;
  readonly name: string;
  readonly project_id: ProjectId | null;
  readonly tags: ReadonlyArray<string>;
}

export interface SaveToLibraryPopoverProps {
  /** Where the popover was launched from — used for telemetry and the
   *  preview subtitle. */
  readonly fromSource: SaveToLibrarySource;

  /** Default kind, per library-prd §3.6.1 call-site defaults. User can
   *  override via the kind selector. */
  readonly defaultKind: LibraryItemKind;

  /** Default name (prefilled from the source — tool result title, agent
   *  message excerpt, thread title, etc). */
  readonly defaultName: string;

  /** Optional default project (inherited from the chat/run's `project_id`
   *  per library-prd §3.6.2). `null` = no project filing. */
  readonly defaultProjectId?: ProjectId | null;

  /** Optional default tags (the host may seed tags from the source — e.g.
   *  routine output gets the routine's name as a tag). */
  readonly defaultTags?: ReadonlyArray<string>;

  /** Projects available to file under — host-supplied. When omitted, the
   *  project selector is hidden. */
  readonly projects?: ReadonlyArray<ProjectFilterChipOption>;

  /** Optional read-only source preview slot (bytes / markdown / first
   *  rows). The popover renders it inside a disclosure. */
  readonly preview?: ReactNode;

  /** Submit handler — fires when the user clicks "Save". The host owns
   *  the network call; reject the returned promise to surface an error
   *  inline. */
  readonly onSubmit: (payload: SaveToLibrarySubmit) => Promise<void>;

  /** Cancel handler — fires on the cancel button or Escape. */
  readonly onCancel?: () => void;
}

// ===========================================================================
// Implementation
// ===========================================================================

const KIND_ORDER: ReadonlyArray<LibraryItemKind> = ["file", "page", "dataset"];

const KIND_LABEL: Readonly<Record<LibraryItemKind, string>> = {
  file: "File",
  page: "Page",
  dataset: "Dataset",
};

const FROM_LABEL: Readonly<Record<SaveToLibrarySource, string>> = {
  chat_tool_result: "From a tool result in a chat",
  chat_agent_msg: "From an agent message",
  chat_thread_pin: "From a pinned chat thread",
  run_completion: "From a completed run",
  routine_output: "From a routine output",
};

export function SaveToLibraryPopover(
  props: SaveToLibraryPopoverProps,
): ReactElement {
  const {
    fromSource,
    defaultKind,
    defaultName,
    defaultProjectId = null,
    defaultTags = [],
    projects,
    preview,
    onSubmit,
    onCancel,
  } = props;

  const [kind, setKind] = useState<LibraryItemKind>(defaultKind);
  const [name, setName] = useState<string>(defaultName);
  const [projectId, setProjectId] = useState<ProjectId | null>(
    defaultProjectId,
  );
  const [tagsInput, setTagsInput] = useState<string>(defaultTags.join(", "));
  const [previewOpen, setPreviewOpen] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const nameInputRef = useRef<HTMLInputElement>(null);

  // Autofocus the name field on mount — the user usually just confirms
  // the name and hits Enter, so this is the path of least friction.
  useEffect(() => {
    nameInputRef.current?.focus();
    nameInputRef.current?.select();
  }, []);

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      if (event !== undefined) event.preventDefault();
      const trimmedName = name.trim();
      if (trimmedName.length === 0) {
        setError("Name is required.");
        return;
      }
      const tags = tagsInput
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
      setSubmitting(true);
      setError(null);
      try {
        await onSubmit({
          kind,
          name: trimmedName,
          project_id: projectId,
          tags,
        });
      } catch (e) {
        const message = e instanceof Error ? e.message : "Save failed.";
        setError(message);
        setSubmitting(false);
      }
    },
    [name, tagsInput, onSubmit, kind, projectId],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (onCancel !== undefined) onCancel();
      }
    },
    [onCancel],
  );

  // === Styles ==========================================================
  const wrapperStyle: CSSProperties = {
    width: 320,
    backgroundColor: "var(--color-bg-elevated, #1a1a1c)",
    border: "1px solid var(--color-border, #232325)",
    borderRadius: "var(--radius-md, 12px)",
    boxShadow: "0 8px 24px rgba(0, 0, 0, 0.32)",
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
  };
  const headerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    margin: 0,
  };
  const subtitleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const fieldStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    color: "var(--color-text-muted, #b4b4b8)",
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  const inputStyle: CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    height: 28,
    padding: "0 8px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    outline: "none",
  };
  const kindRowStyle: CSSProperties = {
    display: "flex",
    gap: 4,
  };
  function kindButtonStyle(active: boolean): CSSProperties {
    return {
      flex: 1,
      height: 28,
      padding: "0 8px",
      borderRadius: "var(--radius-sm, 6px)",
      border: `1px solid ${active ? "var(--color-accent, #d97757)" : "var(--color-border, #232325)"}`,
      backgroundColor: active
        ? "color-mix(in srgb, var(--color-accent, #d97757) 14%, transparent)"
        : "transparent",
      color: active
        ? "var(--color-text, #ededee)"
        : "var(--color-text-muted, #b4b4b8)",
      fontSize: "var(--font-size-sm, 13px)",
      fontWeight: active ? 600 : 500,
      cursor: "pointer",
    };
  }
  const actionsStyle: CSSProperties = {
    display: "flex",
    justifyContent: "flex-end",
    gap: 6,
    marginTop: 2,
  };
  const submitButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 14px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-accent, #d97757)",
    backgroundColor: "var(--color-accent, #d97757)",
    color: "var(--color-accent-contrast, #1a0f0a)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    cursor: submitting || name.trim().length === 0 ? "not-allowed" : "pointer",
    opacity: submitting || name.trim().length === 0 ? 0.6 : 1,
  };
  const cancelButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "transparent",
    color: "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
    cursor: "pointer",
  };
  const errorStyle: CSSProperties = {
    color: "var(--color-danger, #d97777)",
    fontSize: "var(--font-size-xs, 12px)",
  };
  const disclosureSummaryStyle: CSSProperties = {
    cursor: "pointer",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const previewBodyStyle: CSSProperties = {
    marginTop: 4,
    maxHeight: 120,
    overflowY: "auto",
    padding: 8,
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    whiteSpace: "pre-wrap",
  };

  return (
    <div
      role="dialog"
      aria-label="Save to Library"
      style={wrapperStyle}
      data-testid="save-to-library-popover"
      data-from-source={fromSource}
      data-kind={kind}
      onKeyDown={handleKeyDown}
    >
      <div style={headerStyle}>
        <h3 style={titleStyle}>Save to Library</h3>
        <div style={subtitleStyle} data-testid="save-to-library-from-label">
          {FROM_LABEL[fromSource]}
        </div>
      </div>

      <form
        onSubmit={(e) => {
          void handleSubmit(e);
        }}
        aria-label="Save to Library form"
        style={{ display: "flex", flexDirection: "column", gap: 10 }}
      >
        {/* Kind override (library-prd §3.6.2) */}
        <div style={fieldStyle}>
          <label style={labelStyle}>Save as</label>
          <div
            style={kindRowStyle}
            role="radiogroup"
            aria-label="Library item kind"
            data-testid="save-to-library-kind-row"
          >
            {KIND_ORDER.map((k) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={kind === k}
                onClick={() => setKind(k)}
                style={kindButtonStyle(kind === k)}
                data-testid={`save-to-library-kind-${k}`}
              >
                {KIND_LABEL[k]}
              </button>
            ))}
          </div>
        </div>

        {/* Name */}
        <div style={fieldStyle}>
          <label style={labelStyle} htmlFor="save-to-library-name">
            Name
          </label>
          <input
            id="save-to-library-name"
            ref={nameInputRef}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this item"
            disabled={submitting}
            style={inputStyle}
            data-testid="save-to-library-name"
          />
        </div>

        {/* Project picker (shared widget — DRY) */}
        {projects !== undefined ? (
          <div style={fieldStyle}>
            <label style={labelStyle}>Project</label>
            <ProjectFilterChip
              projects={projects}
              value={projectId}
              onChange={(next) => setProjectId(next)}
              label="No project"
            />
          </div>
        ) : null}

        {/* Tags */}
        <div style={fieldStyle}>
          <label style={labelStyle} htmlFor="save-to-library-tags">
            Tags (comma-separated)
          </label>
          <input
            id="save-to-library-tags"
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder="quarterly, finance"
            disabled={submitting}
            style={inputStyle}
            data-testid="save-to-library-tags"
          />
        </div>

        {/* Source preview disclosure */}
        {preview !== undefined ? (
          <details
            open={previewOpen}
            onToggle={(e) =>
              setPreviewOpen((e.target as HTMLDetailsElement).open)
            }
            data-testid="save-to-library-preview"
          >
            <summary style={disclosureSummaryStyle}>Source preview</summary>
            <div style={previewBodyStyle}>{preview}</div>
          </details>
        ) : null}

        {error !== null ? (
          <div
            role="alert"
            style={errorStyle}
            data-testid="save-to-library-error"
          >
            {error}
          </div>
        ) : null}

        <div style={actionsStyle}>
          {onCancel !== undefined ? (
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              style={cancelButtonStyle}
              data-testid="save-to-library-cancel"
            >
              Cancel
            </button>
          ) : null}
          <button
            type="submit"
            disabled={submitting || name.trim().length === 0}
            style={submitButtonStyle}
            data-testid="save-to-library-submit"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
