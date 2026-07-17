// <ConsumersTab /> — "Used by" projection for one connector.
//
// Source: connectors-prd §7.3 (consumers tab) + cross-audit §1.1 (every
// cross-destination hop is an <ItemLink>). Three sections — Agents /
// Tools / Projects — each rendered as an <ActivityList>. `chats_with_grant`
// is a count only (privacy: per-chat fan-out is admin-only).

import type { CSSProperties, ReactElement } from "react";

import type { ConnectorConsumers, ItemRef } from "@0x-copilot/api-types";

import { ActivityList, type ActivityRow } from "../../shell/ActivityList";

export interface ConsumersTabProps {
  readonly consumers: ConnectorConsumers;
}

const STABLE_TIMESTAMP = "1970-01-01T00:00:00Z";

function rowsFor(
  refs: ReadonlyArray<ItemRef>,
  prefix: string,
): ReadonlyArray<ActivityRow> {
  return refs.map((ref) => ({
    key: `${prefix}:${ref.kind}:${ref.id}`,
    ref,
    // Consumer rows don't carry timestamps — the time vocabulary is
    // not meaningful here. We pass a stable epoch so ActivityList
    // renders a `<time>` element (its contract); destinations that
    // care about consumers don't surface the rendered time string.
    timestamp: STABLE_TIMESTAMP,
  }));
}

export function ConsumersTab(props: ConsumersTabProps): ReactElement {
  const { consumers } = props;

  return (
    <div
      data-testid="connector-consumers"
      style={containerStyle}
      role="region"
      aria-label="Consumers"
    >
      <Section
        title="Agents"
        testIdRoot="connector-consumers-agents"
        refs={consumers.agents}
        emptyLabel="No agents grant this connector."
      />
      <Section
        title="Tools"
        testIdRoot="connector-consumers-tools"
        refs={consumers.tools}
        emptyLabel="No tools use this connector."
      />
      <Section
        title="Projects"
        testIdRoot="connector-consumers-projects"
        refs={consumers.projects}
        emptyLabel="No projects mount this connector."
      />
      <section
        data-testid="connector-consumers-chats"
        style={chatsSectionStyle}
        aria-labelledby="connector-consumers-chats-title"
      >
        <h3 id="connector-consumers-chats-title" style={sectionTitleStyle}>
          Chats with a grant
        </h3>
        <p
          style={chatsCountStyle}
          data-testid="connector-consumers-chats-count"
          aria-label={`${consumers.chats_with_grant} chats with a grant`}
        >
          <span style={chatsNumberStyle}>{consumers.chats_with_grant}</span>
          <span style={chatsLabelStyle}>
            {consumers.chats_with_grant === 1 ? "chat" : "chats"}
          </span>
        </p>
        <p style={hintStyle}>
          Per-chat grants are admin-only data. The list is not rendered here.
        </p>
      </section>
    </div>
  );
}

interface SectionProps {
  readonly title: string;
  readonly testIdRoot: string;
  readonly refs: ReadonlyArray<ItemRef>;
  readonly emptyLabel: string;
}

function Section(props: SectionProps): ReactElement {
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
        <ActivityList rows={rowsFor(refs, testIdRoot)} ariaLabel={title} />
      )}
    </section>
  );
}

// === Styles ============================================================

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

const chatsSectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "var(--color-text-muted, #b4b4b8)",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  padding: "8px 10px",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontStyle: "italic",
};

const chatsCountStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "baseline",
  gap: 6,
  margin: 0,
  padding: "4px 0",
};

const chatsNumberStyle: CSSProperties = {
  fontSize: "var(--font-size-xl, 22px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  fontVariantNumeric: "tabular-nums",
};

const chatsLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const hintStyle: CSSProperties = {
  margin: "4px 0 0 0",
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  fontStyle: "italic",
};
