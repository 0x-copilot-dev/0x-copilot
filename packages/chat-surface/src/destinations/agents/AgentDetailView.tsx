// <AgentDetailView /> — read-only summary of a single Agent. Right-rail
// in the gallery, full-page on deep-link/refresh.
//
// Source:
//   - docs/atlas-new-design/destinations/agents-prd.md §7.3 (detail
//     panel), §3.1 (Agent wire), §4.4 (PATCH ACL — system/community
//     immutable, fork via §4.10).
//
// Invariants:
//   - Detail is **read-only by default** (per task UX preamble). The
//     "Customize" CTA forks to the editor:
//       - origin="custom" + viewer-is-owner ⇒ direct edit (onEdit fires).
//       - origin="system" or "community"   ⇒ fork dialog (onForkRequest
//         fires; host renders <ForkDialog />).
//   - SP-1: StatusPill for origin + status surfaces. No bespoke chip
//     primitive here — design-system StatusPill carries the visual.
//   - Pure presentation. Host owns transport + navigation.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { StatusPill, type StatusTone } from "@0x-copilot/design-system";

import type {
  AgentOrigin,
  AgentStatus,
  AgentEditorModelDefault,
  AgentEditorPermissions,
} from "./AgentEditor";

// ===========================================================================
// View-model — what the detail view needs to render. Pure data; host
// composes from Agent (§3.1).
// ===========================================================================

export interface AgentDetailViewModel {
  readonly id: string;
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly icon_emoji: string;
  readonly color_hue: number;
  readonly origin: AgentOrigin;
  readonly status: AgentStatus;
  /** True when the viewer owns this agent (origin="custom" + owner_user_id=me). */
  readonly viewer_is_owner: boolean;
  readonly instructions: string;
  readonly model_default: AgentEditorModelDefault;
  readonly skills: ReadonlyArray<string>;
  readonly connectors_default: ReadonlyArray<string>;
  readonly permissions: AgentEditorPermissions;
  /** Agent.version (monotonic). Display only. */
  readonly version: number;
  /** ISO8601 — display only. */
  readonly updated_at: string;
}

export interface AgentDetailViewProps {
  readonly agent: AgentDetailViewModel;
  /**
   * Direct edit handler — fires when "Customize" is clicked for an agent
   * the viewer can edit (origin="custom" + owner). Host navigates to
   * `/agents/<id>/edit`.
   */
  readonly onEdit?: () => void;
  /**
   * Fork-request handler — fires when "Customize" is clicked for a
   * system / community agent the viewer cannot edit directly. Host
   * opens the <ForkDialog />.
   */
  readonly onForkRequest?: () => void;
  /**
   * "View all versions" link — host routes to `/agents/<id>/versions`.
   */
  readonly onOpenVersions?: () => void;
  /** Optional slot for a usage chart (provided by P8-B3). */
  readonly usageSlot?: ReactNode;
  /** Optional slot for the last-3 versions preview (P8-A2). */
  readonly versionsSlot?: ReactNode;
}

// ===========================================================================
// Component.
// ===========================================================================

export function AgentDetailView(props: AgentDetailViewProps): ReactElement {
  const {
    agent,
    onEdit,
    onForkRequest,
    onOpenVersions,
    usageSlot,
    versionsSlot,
  } = props;

  const isImmutableOrigin =
    agent.origin === "system" || agent.origin === "community";
  // "Customize" routes through one of two callbacks. We never hide the
  // CTA — the user always gets a forward path; for system/community
  // it's via the fork dialog.
  const handleCustomize = (): void => {
    if (isImmutableOrigin || !agent.viewer_is_owner) {
      onForkRequest?.();
    } else {
      onEdit?.();
    }
  };

  return (
    <article
      data-testid="agent-detail-view"
      data-agent-id={agent.id}
      data-origin={agent.origin}
      data-viewer-is-owner={agent.viewer_is_owner ? "true" : "false"}
      style={containerStyle}
    >
      {/* Hero */}
      <header style={heroStyle}>
        <div
          aria-hidden="true"
          style={{
            ...iconSwatchStyle,
            background: `hsl(${agent.color_hue}, 60%, 90%)`,
          }}
          data-testid="agent-detail-icon"
        >
          <span style={iconGlyphStyle}>{agent.icon_emoji}</span>
        </div>
        <div style={heroBodyStyle}>
          <h1 style={titleStyle} data-testid="agent-detail-name">
            {agent.name}
          </h1>
          <p style={slugStyle} data-testid="agent-detail-slug">
            {agent.slug}
          </p>
          <div style={pillRowStyle}>
            <StatusPill
              tone="idle"
              label={agent.origin}
              data-testid="agent-detail-origin-pill"
            />
            <StatusPill
              tone={statusTone(agent.status)}
              label={agent.status}
              data-testid="agent-detail-status-pill"
            />
            <span style={metaStyle} data-testid="agent-detail-version">
              v{agent.version}
            </span>
          </div>
        </div>
      </header>

      {agent.description.length > 0 ? (
        <p style={descriptionStyle} data-testid="agent-detail-description">
          {agent.description}
        </p>
      ) : null}

      {/* Quick facts (per §7.3) */}
      <dl style={factsGridStyle} data-testid="agent-detail-facts">
        <Fact
          label="Model"
          value={agent.model_default.model_id}
          testId="agent-detail-fact-model"
        />
        <Fact
          label="Reasoning depth"
          value={agent.model_default.reasoning_depth}
          testId="agent-detail-fact-depth"
        />
        <Fact
          label="Skills"
          value={String(agent.skills.length)}
          testId="agent-detail-fact-skills"
        />
        <Fact
          label="Connectors"
          value={String(agent.connectors_default.length)}
          testId="agent-detail-fact-connectors"
        />
        <Fact
          label="Autonomy"
          value={agent.permissions.autonomy}
          testId="agent-detail-fact-autonomy"
        />
        <Fact
          label="Read-only"
          value={agent.permissions.read_only ? "Yes" : "No"}
          testId="agent-detail-fact-read-only"
        />
      </dl>

      {/* Instructions — read-only preview (per §7.3 "collapsed by default"
          we render the disclosure open here; host can pass a controlled
          state in a follow-up if collapse is required. Read-only is the
          hard contract per the task brief.). */}
      <section data-testid="agent-detail-instructions" style={sectionStyle}>
        <h2 style={sectionTitleStyle}>Instructions</h2>
        {agent.instructions.length > 0 ? (
          <pre
            style={instructionsBlockStyle}
            data-testid="agent-detail-instructions-body"
          >
            {agent.instructions}
          </pre>
        ) : (
          <p
            style={emptyStyle}
            data-testid="agent-detail-instructions-empty"
            role="status"
          >
            No instructions yet.
          </p>
        )}
      </section>

      {/* Usage chart slot (host-provided; P8-B3 owns the chart). */}
      {usageSlot !== undefined ? (
        <section data-testid="agent-detail-usage-slot" style={sectionStyle}>
          <h2 style={sectionTitleStyle}>Usage</h2>
          {usageSlot}
        </section>
      ) : null}

      {/* Version history slot (host-provided; P8-A2 + VersionHistoryTab). */}
      {versionsSlot !== undefined ? (
        <section data-testid="agent-detail-versions-slot" style={sectionStyle}>
          <h2 style={sectionTitleStyle}>Versions</h2>
          {versionsSlot}
          {onOpenVersions !== undefined ? (
            <button
              type="button"
              onClick={onOpenVersions}
              data-testid="agent-detail-open-versions"
              style={linkButtonStyle}
            >
              View all versions →
            </button>
          ) : null}
        </section>
      ) : null}

      {/* Action row — sticky on wide viewports; the test surface only
          checks data-testid hooks so the visual is host-controllable. */}
      <div style={actionRowStyle} data-testid="agent-detail-actions">
        <span
          style={timestampStyle}
          data-testid="agent-detail-updated-at"
          aria-label={`Updated ${agent.updated_at}`}
        >
          Updated {agent.updated_at}
        </span>
        <button
          type="button"
          onClick={handleCustomize}
          data-testid="agent-detail-customize"
          data-fork-required={isImmutableOrigin ? "true" : "false"}
          style={primaryButtonStyle}
        >
          Customize
        </button>
      </div>
    </article>
  );
}

// ===========================================================================
// Helpers.
// ===========================================================================

function statusTone(status: AgentStatus): StatusTone {
  if (status === "installed") return "ready";
  if (status === "available" || status === "draft") return "idle";
  return "idle"; // disabled
}

interface FactProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function Fact(props: FactProps): ReactElement {
  return (
    <div style={factStyle} data-testid={props.testId}>
      <dt style={factLabelStyle}>{props.label}</dt>
      <dd style={factValueStyle}>{props.value}</dd>
    </div>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const heroStyle: CSSProperties = {
  display: "flex",
  gap: 12,
  alignItems: "center",
};

const iconSwatchStyle: CSSProperties = {
  width: 56,
  height: 56,
  borderRadius: 12,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
};

const iconGlyphStyle: CSSProperties = {
  fontSize: "var(--font-size-3xl)",
  lineHeight: 1,
};

const heroBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xl)",
  fontWeight: 600,
  color: "var(--color-text)",
};

const slugStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
};

const pillRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
  marginTop: 6,
};

const metaStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  padding: "2px 6px",
  borderRadius: 4,
  background: "var(--color-bg-elevated)",
};

const descriptionStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.55,
  color: "var(--color-text)",
};

const factsGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
  gap: 10,
  margin: 0,
  padding: 0,
};

const factStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "8px 10px",
  background: "var(--color-bg-elevated)",
  borderRadius: 6,
};

const factLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const factValueStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  color: "var(--color-text)",
  margin: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "var(--color-text-muted)",
};

const instructionsBlockStyle: CSSProperties = {
  margin: 0,
  padding: 10,
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-xs)",
  fontFamily: "inherit",
  whiteSpace: "pre-wrap",
  maxHeight: 240,
  overflow: "auto",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  paddingTop: 8,
  borderTop: "1px solid var(--color-border)",
};

const timestampStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
};

const primaryButtonStyle: CSSProperties = {
  background: "var(--color-accent)",
  color: "var(--color-bg)",
  border: "none",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  cursor: "pointer",
};

const linkButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-accent)",
  border: "none",
  padding: 0,
  margin: 0,
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
  textAlign: "left",
};
