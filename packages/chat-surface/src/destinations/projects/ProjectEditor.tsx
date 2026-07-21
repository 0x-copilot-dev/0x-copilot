// ProjectEditor — P6.5-B1
//
// Tabbed editor for a single project's owner-editable configuration.
// Mirrors the planned `RoutineEditor` tab pattern (destinations-master-prd
// §5 / implementation-plan P5-B2): each tab is a horizontal section
// hosted under a single `<FilterTabs>` row, and the form is pure
// presentation — every mutation surfaces via callbacks.
//
// Tabs (projects-extensions-prd §5):
//   - Name / Description   (basic metadata)
//   - Icon + Color         (emoji + hue picker)
//   - Connectors           (default_connector_allowlist tri-mode)
//   - Members              (slot for ProjectMembersTab — owned by host)
//
// Connector allowlist semantics (§5.1):
//   - `null`      → "inherit owner defaults"   (mode = "inherit")
//   - `[]`        → "no connectors by default" (mode = "none")
//   - `["x","y"]` → "specific allowlist"       (mode = "allowlist")
//
// Empty array vs null is load-bearing: tests guard the distinction and
// any caller mapping back to the server must preserve it (the §5.4
// inheritance hook keys off `null` vs other).
//
// SP-1 primitives: <FilterTabs> for the tab row, <StatusPill> for the
// connector-mode summary, <EmptyState> for the members slot when not
// supplied. No bespoke colors — design-system tokens via CSS variables.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ConnectorSlug, ProjectId } from "@0x-copilot/api-types";

import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";

import type { ProjectColorHue, ProjectIconEmoji } from "@0x-copilot/api-types";

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

/** Connector kind — canonical site is `@0x-copilot/api-types`
 *  (`packages/api-types/src/projects.ts`). Kept as a presentation alias
 *  so existing `ProjectEditorConnectorSlug` imports still resolve. */
export type ProjectEditorConnectorSlug = ConnectorSlug;

/** Tri-mode for the connector allowlist editor (§5.3). */
export type ProjectConnectorAllowlistMode = "inherit" | "none" | "allowlist";

/** A connector kind the user has connected; populates the allowlist picker. */
export interface ProjectEditorConnectorOption {
  readonly slug: ProjectEditorConnectorSlug;
  /** Human-readable name ("Salesforce", "Gmail"). */
  readonly label: string;
  /** Optional brand-mark glyph (emoji or short text). */
  readonly icon?: ReactNode;
}

/** Snapshot of the project that the editor is editing. The editor mirrors
 *  this into local state on mount; saves surface via `onSave`. */
export interface ProjectEditorValue {
  readonly id: ProjectId;
  readonly name: string;
  readonly description: string;
  readonly iconEmoji: ProjectIconEmoji;
  readonly colorHue: ProjectColorHue;
  /** Tri-mode connector allowlist (§5.1):
   *  - `null` = inherit owner defaults
   *  - `[]` = no connectors by default
   *  - `[slug, ...]` = specific allowlist */
  readonly defaultConnectorAllowlist: ReadonlyArray<ProjectEditorConnectorSlug> | null;
}

/** Save payload shape — identical to value except `id` is implied. */
export interface ProjectEditorSavePayload {
  readonly name: string;
  readonly description: string;
  readonly iconEmoji: ProjectIconEmoji;
  readonly colorHue: ProjectColorHue;
  readonly defaultConnectorAllowlist: ReadonlyArray<ProjectEditorConnectorSlug> | null;
}

export type ProjectEditorTabId =
  | "metadata"
  | "appearance"
  | "connectors"
  | "members";

export interface ProjectEditorProps {
  readonly value: ProjectEditorValue;

  /** Owner's connected connector kinds — the only things selectable in
   *  the allowlist picker (§5.3). Empty array is allowed: it renders the
   *  EmptyState ("connect a connector first…"). */
  readonly availableConnectors: ReadonlyArray<ProjectEditorConnectorOption>;

  /** Owner-only edit gate (§5.3). When `false`, all inputs render
   *  read-only and the Save button is hidden. */
  readonly canEdit?: boolean;

  /** Controlled tab. If omitted, the editor manages its own selection
   *  starting at `initialTab` (or `"metadata"`). */
  readonly activeTab?: ProjectEditorTabId;
  readonly onTabChange?: (tab: ProjectEditorTabId) => void;
  readonly initialTab?: ProjectEditorTabId;

  /** Render slot for the Members tab. The host owns the actual member
   *  list (see ProjectMembersTab) — the editor only chrome-wraps it. */
  readonly renderMembersTab?: () => ReactNode;

  readonly onSave: (payload: ProjectEditorSavePayload) => Promise<void>;
  readonly onCancel?: () => void;

  /** Optional destructive action (e.g. archive). Rendered as a faint
   *  outline button at the footer-left, distinct from primary Save. */
  readonly onDelete?: () => void;
  readonly deleteLabel?: string;
}

// ── Constants for the appearance tab ─────────────────────────────────

/** A small fixed palette of hues so the picker is one-click. Hosts can
 *  swap this out later via a design-token map; the UI shape is stable. */
const COLOR_HUES: ReadonlyArray<number> = [
  0, 30, 60, 120, 180, 210, 260, 300, 330,
];

/** Common project glyphs — quick-pick row. The text input below still
 *  accepts any emoji (server validates that it's a single glyph). */
const COMMON_EMOJI: ReadonlyArray<string> = [
  "📁",
  "📂",
  "📊",
  "🚀",
  "🛠️",
  "🧪",
  "📣",
  "🎯",
  "💼",
  "🧭",
];

// ── Helpers ──────────────────────────────────────────────────────────

function deriveMode(
  allowlist: ReadonlyArray<ProjectEditorConnectorSlug> | null,
): ProjectConnectorAllowlistMode {
  if (allowlist === null) return "inherit";
  if (allowlist.length === 0) return "none";
  return "allowlist";
}

function modeSummary(
  mode: ProjectConnectorAllowlistMode,
  count: number,
): { tone: StatusTone; label: string } {
  if (mode === "inherit")
    return { tone: "muted", label: "Inherit owner defaults" };
  if (mode === "none") return { tone: "warning", label: "No connectors" };
  return {
    tone: "info",
    label: `Allowlist · ${count} connector${count === 1 ? "" : "s"}`,
  };
}

// ── Tab body components (kept local; pure functions) ─────────────────

function MetadataTab({
  name,
  description,
  onNameChange,
  onDescriptionChange,
  readOnly,
}: {
  name: string;
  description: string;
  onNameChange: (next: string) => void;
  onDescriptionChange: (next: string) => void;
  readOnly: boolean;
}): ReactElement {
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
  return (
    <div
      data-testid="project-editor-tab-metadata"
      style={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Name</span>
        <input
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          disabled={readOnly}
          maxLength={80}
          style={inputStyle}
          data-testid="project-editor-name-input"
          aria-label="Project name"
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Description</span>
        <textarea
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          disabled={readOnly}
          maxLength={200}
          style={textareaStyle}
          data-testid="project-editor-description-input"
          aria-label="Project description"
        />
      </label>
    </div>
  );
}

function AppearanceTab({
  iconEmoji,
  colorHue,
  onIconChange,
  onColorChange,
  readOnly,
}: {
  iconEmoji: ProjectIconEmoji;
  colorHue: ProjectColorHue;
  onIconChange: (next: ProjectIconEmoji) => void;
  onColorChange: (next: ProjectColorHue) => void;
  readOnly: boolean;
}): ReactElement {
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontWeight: 500,
  };
  const previewStyle: CSSProperties = {
    width: 56,
    height: 56,
    borderRadius: 12,
    backgroundColor: `hsl(${colorHue}, 55%, 35%)`,
    color: ACCENT_CONTRAST,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "var(--font-size-2xl)",
  };
  const swatchRow: CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
  };
  const inputStyle: CSSProperties = {
    height: 36,
    width: 80,
    padding: "0 12px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-md)",
    outline: "none",
    textAlign: "center",
  };
  return (
    <div
      data-testid="project-editor-tab-appearance"
      style={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <div
          style={previewStyle}
          data-testid="project-editor-appearance-preview"
          data-color-hue={colorHue}
          aria-label="Icon preview"
        >
          <span>{iconEmoji}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Icon</span>
          <input
            type="text"
            value={iconEmoji}
            onChange={(e) => onIconChange(e.target.value as ProjectIconEmoji)}
            disabled={readOnly}
            style={inputStyle}
            data-testid="project-editor-icon-input"
            aria-label="Icon emoji"
          />
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Quick icons</span>
        <div style={swatchRow}>
          {COMMON_EMOJI.map((glyph) => (
            <button
              key={glyph}
              type="button"
              onClick={() => onIconChange(glyph as ProjectIconEmoji)}
              disabled={readOnly}
              data-testid={`project-editor-icon-swatch-${glyph}`}
              aria-label={`Use icon ${glyph}`}
              aria-pressed={glyph === iconEmoji}
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                border: `1px solid ${glyph === iconEmoji ? ACCENT : PANEL_BORDER}`,
                backgroundColor: PANEL_BACKGROUND,
                color: TEXT_PRIMARY,
                cursor: readOnly ? "not-allowed" : "pointer",
                fontSize: "var(--font-size-lg)",
              }}
            >
              {glyph}
            </button>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>Color</span>
        <div style={swatchRow}>
          {COLOR_HUES.map((hue) => {
            const selected = hue === colorHue;
            return (
              <button
                key={hue}
                type="button"
                onClick={() => onColorChange(hue)}
                disabled={readOnly}
                data-testid={`project-editor-color-swatch-${hue}`}
                aria-label={`Use hue ${hue}`}
                aria-pressed={selected}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: "50%",
                  backgroundColor: `hsl(${hue}, 55%, 45%)`,
                  border: `2px solid ${selected ? ACCENT : "transparent"}`,
                  cursor: readOnly ? "not-allowed" : "pointer",
                }}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ConnectorsTab({
  allowlist,
  mode,
  onModeChange,
  available,
  onAllowlistChange,
  readOnly,
}: {
  allowlist: ReadonlyArray<ProjectEditorConnectorSlug> | null;
  mode: ProjectConnectorAllowlistMode;
  onModeChange: (next: ProjectConnectorAllowlistMode) => void;
  available: ReadonlyArray<ProjectEditorConnectorOption>;
  onAllowlistChange: (
    next: ReadonlyArray<ProjectEditorConnectorSlug> | null,
  ) => void;
  readOnly: boolean;
}): ReactElement {
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
    fontWeight: 500,
  };
  const helpStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_FAINT,
    lineHeight: 1.5,
  };
  const radioRow: CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    padding: 10,
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    cursor: readOnly ? "not-allowed" : "pointer",
  };
  const onPickMode = (next: ProjectConnectorAllowlistMode) => {
    if (next === "inherit") onAllowlistChange(null);
    else if (next === "none") onAllowlistChange([]);
    else if (allowlist === null || allowlist.length === 0) {
      // Entering allowlist mode with an empty list is a separate UI
      // state ("user wants an allowlist, hasn't picked yet") that
      // collapses back to mode=none if persisted as-is. Track the
      // explicit choice in the parent's `mode` state so the radio
      // group reflects the user's intent.
      onAllowlistChange([]);
    } else {
      onAllowlistChange(allowlist);
    }
    onModeChange(next);
  };
  const toggleSlug = (slug: ProjectEditorConnectorSlug) => {
    const current = allowlist ?? [];
    const next = current.includes(slug)
      ? current.filter((s) => s !== slug)
      : [...current, slug];
    onAllowlistChange(next);
  };

  const selectedSlugs = new Set<ProjectEditorConnectorSlug>(allowlist ?? []);

  return (
    <div
      data-testid="project-editor-tab-connectors"
      data-allowlist-mode={mode}
      style={{ display: "flex", flexDirection: "column", gap: 14 }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>
          Default connectors for new chats and routines
        </span>
        <span style={helpStyle}>
          Applied when a chat or routine is created inside this project and the
          caller does not pick connectors explicitly. Per-chat overrides always
          win.
        </span>
      </div>
      {(
        [
          "inherit",
          "none",
          "allowlist",
        ] as ReadonlyArray<ProjectConnectorAllowlistMode>
      ).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => {
            if (!readOnly) onPickMode(m);
          }}
          disabled={readOnly}
          style={{
            ...radioRow,
            textAlign: "left",
            background:
              m === mode
                ? "var(--color-bg-accent-subtle, #2a1a14)"
                : "transparent",
            borderColor: m === mode ? ACCENT : PANEL_BORDER,
            color: TEXT_PRIMARY,
          }}
          data-testid={`project-editor-mode-${m}`}
          data-selected={m === mode}
          role="radio"
          aria-checked={m === mode}
          aria-label={`Mode: ${m}`}
        >
          <span
            aria-hidden="true"
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              border: `2px solid ${m === mode ? ACCENT : PANEL_BORDER_STRONG}`,
              backgroundColor: m === mode ? ACCENT : "transparent",
              flexShrink: 0,
              marginTop: 3,
            }}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span
              style={{
                fontSize: "var(--font-size-sm)",
                color: TEXT_PRIMARY,
                fontWeight: 500,
              }}
            >
              {m === "inherit"
                ? "Inherit owner defaults"
                : m === "none"
                  ? "No connectors by default"
                  : "Specific allowlist"}
            </span>
            <span style={helpStyle}>
              {m === "inherit"
                ? "Chats and routines start with whatever the creator's default is."
                : m === "none"
                  ? "Chats and routines start with no connectors pre-enabled."
                  : "Pick the connector kinds that should be pre-enabled."}
            </span>
          </div>
        </button>
      ))}

      {mode === "allowlist" ? (
        available.length === 0 ? (
          <EmptyState
            title="No connectors available"
            body="Connect a connector in Settings → Connectors before building an allowlist."
          />
        ) : (
          <div
            style={{ display: "flex", flexWrap: "wrap", gap: 8 }}
            data-testid="project-editor-allowlist-chip-group"
          >
            {available.map((opt) => {
              const selected = selectedSlugs.has(opt.slug);
              return (
                <button
                  key={opt.slug}
                  type="button"
                  onClick={() => toggleSlug(opt.slug)}
                  disabled={readOnly}
                  data-testid={`project-editor-allowlist-chip-${opt.slug}`}
                  data-selected={selected}
                  aria-pressed={selected}
                  aria-label={`${selected ? "Remove" : "Add"} ${opt.label} from allowlist`}
                  style={{
                    height: 30,
                    padding: "0 12px",
                    borderRadius: 999,
                    border: `1px solid ${selected ? ACCENT : PANEL_BORDER_STRONG}`,
                    backgroundColor: selected
                      ? "var(--color-bg-accent-subtle, #2a1a14)"
                      : "transparent",
                    color: selected ? ACCENT : TEXT_PRIMARY,
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 500,
                    cursor: readOnly ? "not-allowed" : "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  {opt.icon}
                  {opt.label}
                </button>
              );
            })}
          </div>
        )
      ) : null}
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────

export function ProjectEditor(props: ProjectEditorProps): ReactElement {
  const {
    value,
    availableConnectors,
    canEdit = true,
    activeTab: controlledTab,
    onTabChange,
    initialTab = "metadata",
    renderMembersTab,
    onSave,
    onCancel,
    onDelete,
    deleteLabel,
  } = props;

  // ── Tab state (controlled or internal) ─────────────────────────────
  const [internalTab, setInternalTab] =
    useState<ProjectEditorTabId>(initialTab);
  const tab = controlledTab ?? internalTab;
  const setTab = useCallback(
    (next: ProjectEditorTabId) => {
      if (onTabChange !== undefined) onTabChange(next);
      if (controlledTab === undefined) setInternalTab(next);
    },
    [controlledTab, onTabChange],
  );

  // ── Form state (mirrored from `value`; reset on `value.id` change). ─
  const [name, setName] = useState(value.name);
  const [description, setDescription] = useState(value.description);
  const [iconEmoji, setIconEmoji] = useState<ProjectIconEmoji>(value.iconEmoji);
  const [colorHue, setColorHue] = useState<ProjectColorHue>(value.colorHue);
  const [allowlist, setAllowlist] =
    useState<ReadonlyArray<ProjectEditorConnectorSlug> | null>(
      value.defaultConnectorAllowlist,
    );
  // Track the user's mode selection explicitly so "user wants allowlist
  // mode with no slugs yet" stays visually distinct from "user picked
  // mode=none". When persisted, an empty allowlist will collapse to mode
  // "none" on the server — that's fine; the in-form distinction is the
  // editor's affordance, not a wire-shape distinction.
  const [allowlistMode, setAllowlistMode] =
    useState<ProjectConnectorAllowlistMode>(
      deriveMode(value.defaultConnectorAllowlist),
    );

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Dirty check: cheap structural compare. Saves footprint vs deep-equal.
  const dirty = useMemo(() => {
    if (name !== value.name) return true;
    if (description !== value.description) return true;
    if (iconEmoji !== value.iconEmoji) return true;
    if (colorHue !== value.colorHue) return true;
    const a = allowlist;
    const b = value.defaultConnectorAllowlist;
    if (a === null || b === null) return a !== b;
    if (a.length !== b.length) return true;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return true;
    return false;
  }, [name, description, iconEmoji, colorHue, allowlist, value]);

  const handleSubmit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      if (event !== undefined) event.preventDefault();
      if (!canEdit || !dirty || submitting) return;
      const trimmedName = name.trim();
      if (trimmedName.length === 0) {
        setError("Name is required.");
        return;
      }
      setSubmitting(true);
      setError(null);
      try {
        await onSave({
          name: trimmedName,
          description: description.trim(),
          iconEmoji,
          colorHue,
          defaultConnectorAllowlist: allowlist,
        });
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Failed to save project";
        setError(message);
      } finally {
        setSubmitting(false);
      }
    },
    [
      allowlist,
      canEdit,
      colorHue,
      description,
      dirty,
      iconEmoji,
      name,
      onSave,
      submitting,
    ],
  );

  // ── Tabs row ───────────────────────────────────────────────────────
  const connectorSummary = useMemo(() => {
    return modeSummary(allowlistMode, allowlist?.length ?? 0);
  }, [allowlist, allowlistMode]);

  const tabOptions: ReadonlyArray<FilterTabOption<ProjectEditorTabId>> = [
    { slug: "metadata", label: "Name & description" },
    { slug: "appearance", label: "Icon & color" },
    {
      slug: "connectors",
      label: "Connectors",
      count: allowlist?.length,
    },
    { slug: "members", label: "Members" },
  ];

  // ── Render ─────────────────────────────────────────────────────────
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
  const footerStyle: CSSProperties = {
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

  return (
    <form
      style={wrapperStyle}
      onSubmit={handleSubmit}
      data-testid="project-editor"
      data-project-id={value.id}
      data-active-tab={tab}
      data-dirty={dirty}
      aria-label="Project editor"
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <h3
          style={{
            margin: 0,
            fontSize: "var(--font-size-lg)",
            fontWeight: 600,
          }}
        >
          Edit project
        </h3>
        <StatusPill
          status={connectorSummary.tone}
          label={connectorSummary.label}
        />
      </div>

      <FilterTabs<ProjectEditorTabId>
        value={tab}
        onChange={setTab}
        options={tabOptions}
        ariaLabel="Project editor sections"
        idPrefix="project-editor"
      />

      <div
        role="tabpanel"
        id={`project-editor-panel-${tab}`}
        aria-labelledby={`project-editor-tab-${tab}`}
      >
        {tab === "metadata" ? (
          <MetadataTab
            name={name}
            description={description}
            onNameChange={setName}
            onDescriptionChange={setDescription}
            readOnly={!canEdit || submitting}
          />
        ) : null}
        {tab === "appearance" ? (
          <AppearanceTab
            iconEmoji={iconEmoji}
            colorHue={colorHue}
            onIconChange={setIconEmoji}
            onColorChange={setColorHue}
            readOnly={!canEdit || submitting}
          />
        ) : null}
        {tab === "connectors" ? (
          <ConnectorsTab
            allowlist={allowlist}
            mode={allowlistMode}
            onModeChange={setAllowlistMode}
            available={availableConnectors}
            onAllowlistChange={setAllowlist}
            readOnly={!canEdit || submitting}
          />
        ) : null}
        {tab === "members" ? (
          <div data-testid="project-editor-tab-members">
            {renderMembersTab !== undefined ? (
              renderMembersTab()
            ) : (
              <EmptyState
                title="Members tab not wired"
                body="Pass renderMembersTab to inject the host's ProjectMembersTab."
              />
            )}
          </div>
        ) : null}
      </div>

      {error !== null ? (
        <div
          role="alert"
          style={{ color: DANGER, fontSize: "var(--font-size-xs)" }}
          data-testid="project-editor-error"
        >
          {error}
        </div>
      ) : null}

      <div style={footerStyle}>
        {onDelete !== undefined ? (
          <button
            type="button"
            onClick={onDelete}
            disabled={submitting}
            style={deleteStyle}
            data-testid="project-editor-delete"
          >
            {deleteLabel ?? "Archive project"}
          </button>
        ) : null}
        <div style={{ flex: 1 }} />
        {onCancel !== undefined ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            style={cancelStyle}
            data-testid="project-editor-cancel"
          >
            Cancel
          </button>
        ) : null}
        {canEdit ? (
          <button
            type="submit"
            disabled={!dirty || submitting}
            style={submitStyle}
            data-testid="project-editor-save"
            aria-label="Save project"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        ) : null}
      </div>
    </form>
  );
}
