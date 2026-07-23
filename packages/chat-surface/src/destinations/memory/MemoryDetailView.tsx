// <MemoryDetailView /> — detail tabs for one MemoryItem.
//
// Source:
//   docs/atlas-new-design/destinations/team-memory-cmdk-prd.md §7.2:
//     "/memory/<id> — detail tabs (Body / Provenance / Used by)."
//
// Invariants:
//   - Pure presentation. The host supplies the memory record + an
//     optional "used by" list (cross-destination ItemRefs); we render.
//     Mutations (edit / delete) lift through callback props.
//   - SP-1 primitives only. The tab strip reuses `<FilterTabs>` (the
//     same generic that drives every other destination's tab row —
//     cross-audit §1.6 "one tablist primitive"). Status chips render
//     through `<StatusPill>`. Cross-destination links render through
//     `<ItemLink>`.
//   - Markdown rendering reuses `<PagePreview>` from the Library
//     destination — there is one markdown renderer in chat-surface
//     (Streamdown, via `PagePreview`). No new markdown library, no
//     local Streamdown import (cross-audit §1.6 SP-1).
//   - `formatRelativeTime` from `../../util/time` is the canonical
//     relative-time helper (cross-audit §3.4).

import { useState, type CSSProperties, type ReactElement } from "react";

import type { ItemRef, MemoryItem } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { itemKindNoun } from "../../refs/itemKindNoun";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

import { PagePreview } from "../library/preview/PagePreview";

// ===========================================================================
// Tab slug
// ===========================================================================

export type MemoryDetailTabSlug = "body" | "provenance" | "used_by";

const TAB_ORDER: ReadonlyArray<MemoryDetailTabSlug> = [
  "body",
  "provenance",
  "used_by",
];

const TAB_LABEL: Readonly<Record<MemoryDetailTabSlug, string>> = {
  body: "Body",
  provenance: "Provenance",
  used_by: "Used by",
};

// ===========================================================================
// Public props
// ===========================================================================

export interface MemoryDetailViewProps {
  readonly memory: MemoryItem;

  /**
   * Cross-destination refs that have read this memory recently. Empty
   * = "Not used yet". Driven by `GET /v1/memory/{id}/used-by`; the host
   * pre-resolves the refs.
   */
  readonly usedBy?: ReadonlyArray<{
    readonly at: string;
    readonly ref: ItemRef;
  }>;

  /** Active tab; controlled-or-uncontrolled (default `body`). */
  readonly activeTab?: MemoryDetailTabSlug;
  readonly onTabChange?: (next: MemoryDetailTabSlug) => void;

  readonly onEdit?: (id: MemoryItem["id"]) => void;
  readonly onDelete?: (id: MemoryItem["id"]) => void;
  readonly onClose?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Implementation
// ===========================================================================

export function MemoryDetailView(props: MemoryDetailViewProps): ReactElement {
  const {
    memory,
    usedBy = [],
    activeTab: controlledTab,
    onTabChange,
    onEdit,
    onDelete,
    onClose,
    now,
  } = props;

  const [internalTab, setInternalTab] = useState<MemoryDetailTabSlug>("body");
  const activeTab = controlledTab ?? internalTab;

  const handleTabChange = (next: MemoryDetailTabSlug): void => {
    setInternalTab(next);
    if (onTabChange !== undefined) onTabChange(next);
  };

  const tabOptions: ReadonlyArray<FilterTabOption<MemoryDetailTabSlug>> =
    TAB_ORDER.map((slug) => ({
      slug,
      label: TAB_LABEL[slug],
      count:
        slug === "used_by" && usedBy.length > 0 ? usedBy.length : undefined,
    }));

  const reference = now ?? Date.now();

  return (
    <article
      aria-label={`Memory ${memory.title}`}
      data-testid="memory-detail"
      data-memory-id={memory.id}
      data-memory-kind={memory.kind}
      data-memory-scope={memory.scope}
      style={rootStyle}
    >
      {/* === Header strip ================================================ */}
      <header style={headerStyle}>
        <div style={titleBlockStyle}>
          <h2 style={titleStyle} data-testid="memory-detail-title">
            {memory.title}
          </h2>
          <div style={chipRowStyle} data-testid="memory-detail-chips">
            <StatusPill
              status="info"
              label={kindLabel(memory.kind)}
              data-testid="memory-detail-kind-chip"
            />
            <StatusPill
              status={memory.scope === "workspace" ? "info" : "muted"}
              label={memory.scope === "workspace" ? "Workspace" : "My"}
            />
            {memory.tags.map((tag) => (
              <StatusPill key={tag} status="muted" label={`#${tag}`} />
            ))}
          </div>
        </div>
        <div style={actionsStyle}>
          {onEdit !== undefined ? (
            <button
              type="button"
              onClick={() => onEdit(memory.id)}
              style={textButtonStyle}
              data-testid="memory-detail-edit"
              aria-label={`Edit ${memory.title}`}
            >
              Edit
            </button>
          ) : null}
          {onDelete !== undefined ? (
            <button
              type="button"
              onClick={() => onDelete(memory.id)}
              style={textButtonStyle}
              data-testid="memory-detail-delete"
              aria-label={`Delete ${memory.title}`}
            >
              Delete
            </button>
          ) : null}
          {onClose !== undefined ? (
            <button
              type="button"
              onClick={onClose}
              style={textButtonStyle}
              data-testid="memory-detail-close"
              aria-label="Close memory"
            >
              Close
            </button>
          ) : null}
        </div>
      </header>

      {/* === Tab strip =================================================== */}
      <FilterTabs<MemoryDetailTabSlug>
        value={activeTab}
        onChange={handleTabChange}
        options={tabOptions}
        ariaLabel="Memory detail sections"
        idPrefix="memory-detail"
      />

      {/* === Tab body ==================================================== */}
      <div
        role="tabpanel"
        id={`memory-detail-panel-${activeTab}`}
        aria-labelledby={`memory-detail-tab-${activeTab}`}
        data-testid={`memory-detail-panel-${activeTab}`}
        style={panelStyle}
      >
        {activeTab === "body" ? (
          <BodyTab markdown={memory.body} />
        ) : activeTab === "provenance" ? (
          <ProvenanceTab memory={memory} now={reference} />
        ) : (
          <UsedByTab usedBy={usedBy} now={reference} />
        )}
      </div>
    </article>
  );
}

// ===========================================================================
// Body tab — markdown via the shared PagePreview (Streamdown).
// ===========================================================================

function BodyTab({ markdown }: { readonly markdown: string }): ReactElement {
  return (
    <div data-testid="memory-detail-body">
      <PagePreview markdown={markdown} />
    </div>
  );
}

// ===========================================================================
// Provenance tab — who/when/last used summary.
// ===========================================================================

function ProvenanceTab({
  memory,
  now,
}: {
  readonly memory: MemoryItem;
  readonly now: number;
}): ReactElement {
  const rowStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "10px 0",
    borderBottom: "1px solid var(--color-border, #232325)",
  };
  const labelStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-subtle, #7e7e84)",
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  const valueStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 13px)",
    color: "var(--color-text, #ededee)",
  };

  return (
    <dl data-testid="memory-detail-provenance" style={{ margin: 0 }}>
      <div style={rowStyle}>
        <dt style={labelStyle}>Created by</dt>
        <dd style={valueStyle}>
          {memory.created_by.kind === "agent"
            ? `Agent · ${memory.created_by.id}`
            : `User · ${memory.created_by.id}`}
        </dd>
      </div>
      <div style={rowStyle}>
        <dt style={labelStyle}>Created</dt>
        <dd style={valueStyle}>{formatRelativeTime(memory.created_at, now)}</dd>
      </div>
      <div style={rowStyle}>
        <dt style={labelStyle}>Updated</dt>
        <dd style={valueStyle}>{formatRelativeTime(memory.updated_at, now)}</dd>
      </div>
      <div style={rowStyle}>
        <dt style={labelStyle}>Last used</dt>
        <dd style={valueStyle}>
          {memory.last_used_at !== null
            ? formatRelativeTime(memory.last_used_at, now)
            : "never"}
        </dd>
      </div>
      {memory.project_id !== undefined && memory.project_id !== null ? (
        <div style={rowStyle}>
          <dt style={labelStyle}>Project</dt>
          <dd style={valueStyle}>
            <ItemLink
              ref={{ kind: "project", id: memory.project_id }}
              label={itemKindNoun("project")}
            />
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

// ===========================================================================
// Used-by tab — cross-destination refs that retrieved this memory.
// ===========================================================================

function UsedByTab({
  usedBy,
  now,
}: {
  readonly usedBy: ReadonlyArray<{
    readonly at: string;
    readonly ref: ItemRef;
  }>;
  readonly now: number;
}): ReactElement {
  if (usedBy.length === 0) {
    return (
      <div data-testid="memory-detail-used-by-empty" style={emptyUsedByStyle}>
        Not used by any runs yet.
      </div>
    );
  }
  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 0",
    borderBottom: "1px solid var(--color-border, #232325)",
    fontSize: "var(--font-size-sm, 13px)",
  };
  const whenStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-subtle, #7e7e84)",
  };
  return (
    <ul
      data-testid="memory-detail-used-by"
      style={{ listStyle: "none", margin: 0, padding: 0 }}
    >
      {usedBy.map((u, idx) => (
        <li
          key={`${u.ref.kind}:${u.ref.id}:${idx}`}
          style={rowStyle}
          data-testid="memory-detail-used-by-row"
        >
          <ItemLink ref={u.ref} label={itemKindNoun(u.ref.kind)} />
          <span style={whenStyle}>{formatRelativeTime(u.at, now)}</span>
        </li>
      ))}
    </ul>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function kindLabel(kind: MemoryItem["kind"]): string {
  if (kind === "skill") return "Skill";
  if (kind === "fact") return "Fact";
  return "Preference";
}

// ===========================================================================
// Styles
// ===========================================================================

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 4,
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 16,
};

const titleBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xl, 18px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const chipRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexShrink: 0,
};

const textButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-sm, 13px)",
  cursor: "pointer",
  padding: "4px 8px",
};

const panelStyle: CSSProperties = {
  minHeight: 120,
};

const emptyUsedByStyle: CSSProperties = {
  padding: 24,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontStyle: "italic",
  textAlign: "center",
};
