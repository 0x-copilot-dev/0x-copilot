// <UsedByTab /> — the "Used by" surface in the Tool detail view.
//
// Source:
//   - docs/atlas-new-design/destinations/tools-prd.md §3.1 `ToolDetailResponse`
//     (`consumers.agents`, `consumers.routines`, `consumers.chats_with_grant`).
//   - tools-prd.md §7.2 — "Used by" tab in the detail view.
//
// Invariants:
//   - Every consumer link goes through `<ItemLink>`. No hardcoded route,
//     no direct `router.navigate`. cross-audit §1.1 + §3.3.
//   - Three sections (Agents / Routines / Chats). Each renders an empty
//     state independently; sections never merge.
//   - Per-chat grants are admin-only, so this tab renders the COUNT of
//     chats with a grant, not the list. tools-prd §3.1 explicit on this.

import type { CSSProperties, ReactElement } from "react";

import type { ItemRef } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";

// ===========================================================================
// Public props.
// ===========================================================================

export interface UsedByTabProps {
  /** Narrowed to `kind: "agent"` (server-side guarantee). */
  readonly agents: ReadonlyArray<ItemRef>;
  /** Narrowed to `kind: "routine"` (server-side guarantee). */
  readonly routines: ReadonlyArray<ItemRef>;
  /** Per-chat grants are an admin-only list; non-admins see the count only. */
  readonly chats_with_grant: number;
}

// ===========================================================================
// Component.
// ===========================================================================

export function UsedByTab(props: UsedByTabProps): ReactElement {
  const { agents, routines, chats_with_grant } = props;

  return (
    <div
      data-testid="tool-used-by"
      style={containerStyle}
      role="region"
      aria-label="Used by"
    >
      <ConsumerSection
        title="Agents"
        testIdRoot="tool-used-by-agents"
        refs={agents}
        emptyLabel="No agents grant this tool."
      />
      <ConsumerSection
        title="Routines"
        testIdRoot="tool-used-by-routines"
        refs={routines}
        emptyLabel="No routines call this tool."
      />
      <section
        data-testid="tool-used-by-chats"
        style={sectionStyle}
        aria-labelledby="tool-used-by-chats-title"
      >
        <h3 id="tool-used-by-chats-title" style={sectionTitleStyle}>
          Chats with a grant
        </h3>
        {chats_with_grant === 0 ? (
          <p
            data-testid="tool-used-by-chats-empty"
            role="status"
            style={emptyStyle}
          >
            No chats grant this tool.
          </p>
        ) : (
          <p
            data-testid="tool-used-by-chats-count"
            style={countStyle}
            aria-label={`${chats_with_grant} chat${chats_with_grant === 1 ? "" : "s"} with a grant`}
          >
            <span style={countNumberStyle}>{chats_with_grant}</span>
            <span style={countLabelStyle}>
              {chats_with_grant === 1 ? "chat" : "chats"}
            </span>
          </p>
        )}
        <p style={hintStyle}>
          Per-chat grants are admin-only data. The list is not rendered here.
        </p>
      </section>
    </div>
  );
}

// ===========================================================================
// Subcomponents.
// ===========================================================================

interface ConsumerSectionProps {
  readonly title: string;
  readonly testIdRoot: string;
  readonly refs: ReadonlyArray<ItemRef>;
  readonly emptyLabel: string;
}

function ConsumerSection(props: ConsumerSectionProps): ReactElement {
  const { title, testIdRoot, refs, emptyLabel } = props;
  return (
    <section
      data-testid={testIdRoot}
      style={sectionStyle}
      aria-labelledby={`${testIdRoot}-title`}
    >
      <h3 id={`${testIdRoot}-title`} style={sectionTitleStyle}>
        {title}
      </h3>
      {refs.length === 0 ? (
        <p data-testid={`${testIdRoot}-empty`} role="status" style={emptyStyle}>
          {emptyLabel}
        </p>
      ) : (
        <ul
          style={listStyle}
          data-testid={`${testIdRoot}-list`}
          aria-label={title}
        >
          {refs.map((ref) => (
            <li
              key={`${ref.kind}:${ref.id}`}
              style={rowStyle}
              data-testid={`${testIdRoot}-row`}
              data-item-kind={ref.kind}
            >
              <ItemLink ref={ref} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "var(--color-text-muted)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "6px 10px",
  borderRadius: 6,
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
};

const countStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "baseline",
  gap: 6,
  margin: 0,
  padding: "4px 0",
};

const countNumberStyle: CSSProperties = {
  fontSize: "var(--font-size-2xl)",
  fontWeight: 600,
  color: "var(--color-text)",
  fontVariantNumeric: "tabular-nums",
};

const countLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const hintStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
  fontStyle: "italic",
};
