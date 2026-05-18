// <ProjectFilterChip> — the shared cross-destination project filter widget
// (P6-B1).
//
// Source: projects-prd §9 "Cross-destination project filter (Projects as
// a filter axis on every other destination)". Consumed by:
//
//   - Todos panel (`destinations/todos/TodosPanel.tsx`)
//   - Inbox panel (`destinations/inbox/InboxPanel.tsx`)
//   - Library panel (`destinations/library/LibraryPanel.tsx`, lands P7)
//   - Routines panel (`destinations/routines/RoutinesPanel.tsx`)
//
// Each consuming destination imports this widget and wires it into its
// own panel; the panel updates the same `?filter[project_id]=…` axis
// already plumbed in each destination's route state (cross-audit §1.5
// multi-value OR semantics).
//
// Hard correctness rules (the reason this widget exists at all — DRY):
//   - One canonical chip shape (button + dropdown + search) so every
//     destination's project filter LOOKS and BEHAVES identically.
//   - Pure presentation: no fetch, no router calls. The host supplies
//     the projects list (P6-C wires `useProjectsForFilter()` against
//     `GET /v1/projects?filter[member_user_id]=me&limit=200`).
//   - Emits the selected `ProjectId | null`. `null` = "All projects"
//     (no filter applied); a non-null id maps to
//     `?filter[project_id]=<id>` on the destination's list endpoint.
//   - Multi-select is NOT shipped in P6-B1; spec §9.1 OR-semantics is
//     deferred to P6-B2 (selecting a second project would require a
//     `ProjectId[]` accumulator the host owns). The widget is a single-
//     select with an explicit "All projects" reset.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FocusEvent as ReactFocusEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type { ProjectId } from "@enterprise-search/api-types";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type { ProjectSummary } from "./_projects-stub";

// ===========================================================================
// Public props
// ===========================================================================

/**
 * Lightweight projection of `ProjectSummary` the chip needs. Hosts that
 * already have full `ProjectSummary` rows (Routines / Todos panels) pass
 * them through as-is; hosts that only have `id + name + icon_emoji`
 * (small filter dropdowns) can synthesise the rest as defaults. Using
 * `Pick<>` keeps the shape DRY against the canonical site.
 */
export type ProjectFilterChipOption = Pick<
  ProjectSummary,
  "id" | "name" | "icon_emoji" | "color_hue" | "status" | "viewer_starred"
>;

export interface ProjectFilterChipProps {
  /** Full list of projects available to the caller. Host-supplied. */
  readonly projects: ReadonlyArray<ProjectFilterChipOption>;

  /** Currently selected project id, or `null` when no project filter
   *  is active ("All projects"). */
  readonly value: ProjectId | null;

  /** Fires when the user picks a project (or clears the selection).
   *  Host translates this to `?filter[project_id]=<id>` and re-fetches
   *  the destination's list. */
  readonly onChange: (next: ProjectId | null) => void;

  /** Optional className for host-level styling overrides. */
  readonly className?: string;

  /** Optional label for the trigger button. Defaults to "Project". */
  readonly label?: string;

  /** Optional placeholder for the search input. Defaults to
   *  "Search projects…". */
  readonly searchPlaceholder?: string;
}

// ===========================================================================
// Implementation
// ===========================================================================

export function ProjectFilterChip(props: ProjectFilterChipProps): ReactElement {
  const {
    projects,
    value,
    onChange,
    className,
    label = "Project",
    searchPlaceholder = "Search projects…",
  } = props;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  // === Close on outside focus loss + Escape ============================
  // chat-surface is substrate-agnostic — we don't reach for `document`
  // here. Outside-click close is implemented via `onBlur` on the wrapper
  // (the wrapper has `tabIndex=-1` so it participates in focus); Escape
  // is handled by `onKeyDown` on the wrapper. This trades a tiny UX
  // difference (a click on a non-focusable element outside the chip
  // doesn't close it on its own, but the next focus into another element
  // does) for substrate purity per the chat-surface package boundary.
  const handleWrapperBlur = useCallback(
    (event: ReactFocusEvent<HTMLDivElement>): void => {
      // `relatedTarget` is the element gaining focus; when it's outside
      // the wrapper subtree we close the dropdown.
      if (
        event.relatedTarget !== null &&
        event.currentTarget.contains(event.relatedTarget as Node)
      ) {
        return;
      }
      setOpen(false);
    },
    [],
  );

  const handleWrapperKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
      }
    },
    [],
  );

  // === Auto-focus search when dropdown opens ===========================
  useEffect(() => {
    if (open) searchInputRef.current?.focus();
  }, [open]);

  // === Reset query when closing ========================================
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // === Filtered list (single source of truth — fed to both Starred and
  //     All sections so they never drift). ==============================
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length === 0) return projects;
    return projects.filter((p) => p.name.toLowerCase().includes(q));
  }, [projects, query]);

  // === Starred subset (§9.1 spec — starred surfaced above active). =====
  const starred = useMemo(
    () => filtered.filter((p) => p.viewer_starred),
    [filtered],
  );

  // === Active (non-archived) and archived sections. ====================
  const active = useMemo(
    () => filtered.filter((p) => p.status === "active" && !p.viewer_starred),
    [filtered],
  );
  const archived = useMemo(
    () => filtered.filter((p) => p.status === "archived" && !p.viewer_starred),
    [filtered],
  );

  // === Trigger label — shows current selection name when active. =======
  const selected = useMemo(
    () => projects.find((p) => p.id === value) ?? null,
    [projects, value],
  );
  const triggerLabel = selected !== null ? selected.name : label;

  const handlePick = useCallback(
    (next: ProjectId | null): void => {
      onChange(next);
      setOpen(false);
    },
    [onChange],
  );

  // === Styles ==========================================================
  const wrapperStyle: CSSProperties = {
    position: "relative",
    display: "inline-block",
  };
  const triggerStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 28,
    padding: "0 10px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor:
      value === null
        ? "transparent"
        : "color-mix(in srgb, var(--color-accent, #d97757) 10%, transparent)",
    color:
      value === null
        ? "var(--color-text-muted, #b4b4b8)"
        : "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 500,
    cursor: "pointer",
  };
  const dropdownStyle: CSSProperties = {
    position: "absolute",
    top: "calc(100% + 4px)",
    left: 0,
    minWidth: 240,
    maxWidth: 320,
    maxHeight: 360,
    overflowY: "auto",
    backgroundColor: "var(--color-bg-elevated, #1a1a1c)",
    border: "1px solid var(--color-border, #232325)",
    borderRadius: "var(--radius-md, 12px)",
    boxShadow: "0 8px 24px rgba(0, 0, 0, 0.32)",
    padding: 6,
    zIndex: 1000,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  };
  const searchInputStyle: CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    height: 28,
    padding: "0 8px",
    border: "1px solid var(--color-border, #232325)",
    borderRadius: "var(--radius-sm, 6px)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    outline: "none",
  };
  const sectionLabelStyle: CSSProperties = {
    padding: "6px 8px 2px",
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    color: "var(--color-text-subtle, #7e7e84)",
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  const dividerStyle: CSSProperties = {
    height: 1,
    background: "var(--color-border, #232325)",
    margin: "4px 0",
  };

  return (
    <div
      className={className}
      style={wrapperStyle}
      data-testid="project-filter-chip"
      data-open={open ? "true" : "false"}
      data-selected-project-id={value ?? undefined}
      onBlur={handleWrapperBlur}
      onKeyDown={handleWrapperKeyDown}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        style={triggerStyle}
        data-testid="project-filter-chip-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={
          value === null
            ? "Filter by project"
            : `Filter by project: ${triggerLabel}`
        }
      >
        <span aria-hidden="true">{selected?.icon_emoji ?? "▾"}</span>
        <span>{triggerLabel}</span>
        {value !== null ? (
          <span
            aria-hidden="true"
            style={{
              opacity: 0.6,
              fontSize: "var(--font-size-2xs, 11px)",
            }}
          >
            ✕
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="Project filter options"
          style={dropdownStyle}
          data-testid="project-filter-chip-dropdown"
        >
          <input
            ref={searchInputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            style={searchInputStyle}
            data-testid="project-filter-chip-search"
            aria-label="Search projects"
          />

          {/* "All projects" reset option — always at top, always present. */}
          <ProjectFilterOption
            label="All projects"
            icon="—"
            active={value === null}
            onClick={() => handlePick(null)}
            testId="project-filter-chip-all"
          />

          {starred.length > 0 ? (
            <>
              <div style={dividerStyle} aria-hidden="true" />
              <div style={sectionLabelStyle}>Starred</div>
              {starred.map((p) => (
                <ProjectFilterOption
                  key={p.id}
                  label={p.name}
                  icon={p.icon_emoji}
                  active={value === p.id}
                  onClick={() => handlePick(p.id)}
                  testId={`project-filter-chip-option-${p.id}`}
                />
              ))}
            </>
          ) : null}

          {active.length > 0 ? (
            <>
              <div style={dividerStyle} aria-hidden="true" />
              <div style={sectionLabelStyle}>Active</div>
              {active.map((p) => (
                <ProjectFilterOption
                  key={p.id}
                  label={p.name}
                  icon={p.icon_emoji}
                  active={value === p.id}
                  onClick={() => handlePick(p.id)}
                  testId={`project-filter-chip-option-${p.id}`}
                />
              ))}
            </>
          ) : null}

          {archived.length > 0 ? (
            <>
              <div style={dividerStyle} aria-hidden="true" />
              <div style={sectionLabelStyle}>Archived</div>
              {archived.map((p) => (
                <ProjectFilterOption
                  key={p.id}
                  label={p.name}
                  icon={p.icon_emoji}
                  active={value === p.id}
                  onClick={() => handlePick(p.id)}
                  testId={`project-filter-chip-option-${p.id}`}
                />
              ))}
            </>
          ) : null}

          {filtered.length === 0 ? (
            <div
              style={{
                padding: "12px 8px",
                fontSize: "var(--font-size-sm, 13px)",
                color: "var(--color-text-subtle, #7e7e84)",
                textAlign: "center",
              }}
              data-testid="project-filter-chip-empty"
            >
              No matching projects
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// One row in the dropdown listbox
// ===========================================================================

interface ProjectFilterOptionProps {
  readonly label: string;
  readonly icon: string;
  readonly active: boolean;
  readonly onClick: () => void;
  readonly testId: string;
}

function ProjectFilterOption({
  label,
  icon,
  active,
  onClick,
  testId,
}: ProjectFilterOptionProps): ReactElement {
  const buttonStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    padding: "6px 8px",
    border: "none",
    borderRadius: "var(--radius-sm, 6px)",
    background: active
      ? "color-mix(in srgb, var(--color-accent, #d97757) 14%, transparent)"
      : "transparent",
    color: active
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: active ? 600 : 500,
    cursor: "pointer",
    textAlign: "left",
  };
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onClick={onClick}
      style={buttonStyle}
      data-testid={testId}
      data-active={active ? "true" : "false"}
    >
      <span aria-hidden="true">{icon}</span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      {active ? (
        <span aria-hidden="true" style={{ fontSize: 12 }}>
          ✓
        </span>
      ) : null}
    </button>
  );
}
