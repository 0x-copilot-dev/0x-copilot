// <ToolDetailView /> — read-only summary + tabbed views for a single Tool.
//
// Source:
//   - docs/atlas-new-design/destinations/tools-prd.md §7.2 (header +
//     tabs: Overview / Args & Returns / Invocations / Used by / Audit /
//     Edit).
//   - tools-prd.md §3.1 — wire shapes (Tool, ToolUsageProjection,
//     ToolInvocation, ToolDetailResponse.consumers).
//   - Phase 8 AgentDetailView — the detail-view pattern (hero + facts
//     + slot composition). We diverge from agents on the tab model:
//     tools have 6 surfaces (incl. Edit), agents have one read-only
//     surface + a Customize CTA. Both files use the same SP-1 hero
//     layout (icon swatch + name + slug + status pill row).
//
// Invariants:
//   - SP-1: design-system <Badge> for status chips; no bespoke chips.
//   - SUBSTITUTION: pure data in, callbacks out. The host wires the
//     transport (PATCH, load-more invocations, etc).
//   - SINGLE SOURCE OF TRUTH: every wire-side type comes from
//     `@0x-copilot/api-types`. Zero brand redeclarations here.
//   - ARIA tabs (cross-audit §1.6, master §3.6): tablist + tab +
//     tabpanel + arrow / Home / End keyboard nav.
//   - Audit + Edit panels are slot-driven so the host (data-binder)
//     can plug in `<ToolEditor />` and an audit-log component without
//     pulling them into this presentational layer.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ItemRef, Tool, ToolInvocation } from "@0x-copilot/api-types";
import { Badge } from "@0x-copilot/design-system";

// The design-system Badge tone subset used by tool-status chips. (Only these
// two are reachable from `statusTone` below; typed narrowly so a stray tone is
// a compile error.)
type ToolBadgeTone = "success" | "neutral";

import { ToolInvocationsTable } from "./ToolInvocationsTable";
import { ToolUsageChart, type ToolDailyCallPoint } from "./ToolUsageChart";
import { UsedByTab } from "./UsedByTab";

// ===========================================================================
// Tabs.
// ===========================================================================

export type ToolDetailTabId =
  | "overview"
  | "schema"
  | "invocations"
  | "used_by"
  | "audit"
  | "edit";

const TAB_ORDER: ReadonlyArray<ToolDetailTabId> = [
  "overview",
  "schema",
  "invocations",
  "used_by",
  "audit",
  "edit",
];

const TAB_LABEL: Readonly<Record<ToolDetailTabId, string>> = {
  overview: "Overview",
  schema: "Args & Returns",
  invocations: "Invocations",
  used_by: "Used by",
  audit: "Audit",
  edit: "Edit",
};

// ===========================================================================
// Props.
// ===========================================================================

export interface ToolDetailViewProps {
  readonly tool: Tool;
  /** Server-projected consumer rollup (tools-prd §3.1). */
  readonly consumers: {
    readonly agents: ReadonlyArray<ItemRef>;
    readonly routines: ReadonlyArray<ItemRef>;
    readonly chats_with_grant: number;
  };
  /** Page of invocations (host owns the page; we render what we get). */
  readonly invocations?: ReadonlyArray<ToolInvocation>;
  readonly invocationsNextCursor?: string | null;
  readonly onLoadMoreInvocations?: (cursor: string) => void;
  /** Optional daily-calls series for the 30-day window. */
  readonly dailyCalls?: ReadonlyArray<ToolDailyCallPoint>;
  /** Whether the viewer can edit this tool (owner or admin). */
  readonly viewer_can_edit?: boolean;
  /** Slot for the editor surface (data-binder mounts <ToolEditor />). */
  readonly editorSlot?: ReactNode;
  /** Slot for the audit log surface. */
  readonly auditSlot?: ReactNode;
  /** Frozen `now` for tests / SSR. */
  readonly now?: number;
  readonly initialTab?: ToolDetailTabId;
  readonly onTabChange?: (next: ToolDetailTabId) => void;
}

// ===========================================================================
// Component.
// ===========================================================================

export function ToolDetailView(props: ToolDetailViewProps): ReactElement {
  const {
    tool,
    consumers,
    invocations = [],
    invocationsNextCursor,
    onLoadMoreInvocations,
    dailyCalls,
    viewer_can_edit = false,
    editorSlot,
    auditSlot,
    now,
    initialTab = "overview",
    onTabChange,
  } = props;

  const [activeTab, setActiveTab] = useState<ToolDetailTabId>(initialTab);
  const tabRefs = useRef<Record<ToolDetailTabId, HTMLButtonElement | null>>({
    overview: null,
    schema: null,
    invocations: null,
    used_by: null,
    audit: null,
    edit: null,
  });

  const switchTab = useCallback(
    (next: ToolDetailTabId) => {
      setActiveTab(next);
      onTabChange?.(next);
    },
    [onTabChange],
  );

  const focusTab = useCallback(
    (next: ToolDetailTabId) => {
      switchTab(next);
      tabRefs.current[next]?.focus();
    },
    [switchTab],
  );

  const onTabKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const idx = TAB_ORDER.indexOf(activeTab);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length]!);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]!);
      } else if (event.key === "Home") {
        event.preventDefault();
        focusTab(TAB_ORDER[0]!);
      } else if (event.key === "End") {
        event.preventDefault();
        focusTab(TAB_ORDER[TAB_ORDER.length - 1]!);
      }
    },
    [activeTab, focusTab],
  );

  const tabs = useMemo<ReadonlyArray<ToolDetailTabId>>(() => {
    // The Edit tab is only rendered when the viewer can edit. We don't
    // hide it entirely from the tablist when no editorSlot is provided —
    // the host owns the slot wiring — but if viewer_can_edit is false,
    // there's no path to the editor anyway.
    if (viewer_can_edit) return TAB_ORDER;
    return TAB_ORDER.filter((t) => t !== "edit");
  }, [viewer_can_edit]);

  return (
    <article
      data-testid="tool-detail-view"
      data-tool-id={tool.id}
      data-tool-kind={tool.kind}
      data-active-tab={activeTab}
      style={containerStyle}
    >
      {/* Header --------------------------------------------------------- */}
      <header style={heroStyle}>
        <div style={heroBodyStyle}>
          <h1 style={titleStyle} data-testid="tool-detail-name">
            {tool.name}
          </h1>
          {tool.description.length > 0 ? (
            <p style={descriptionStyle} data-testid="tool-detail-description">
              {tool.description}
            </p>
          ) : null}
          <div style={pillRowStyle} data-testid="tool-detail-pill-row">
            <KindChip kind={tool.kind} />
            <Badge
              tone={statusTone(tool.status)}
              data-testid="tool-detail-status-pill"
            >
              {tool.status}
            </Badge>
            <ScopeChip scope={tool.scope} />
          </div>
        </div>
        <KpiStrip tool={tool} />
      </header>

      {/* Tabs ----------------------------------------------------------- */}
      <div
        role="tablist"
        aria-label="Tool detail"
        style={tabStripStyle}
        onKeyDown={onTabKeyDown}
      >
        {tabs.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            type="button"
            role="tab"
            id={`tool-detail-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`tool-detail-tabpanel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => switchTab(tab)}
            data-testid={`tool-detail-tab-${tab}`}
            style={tabButtonStyle(activeTab === tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`tool-detail-tabpanel-${activeTab}`}
        aria-labelledby={`tool-detail-tab-${activeTab}`}
        data-testid={`tool-detail-tabpanel-${activeTab}`}
        style={panelStyle}
      >
        {activeTab === "overview" ? (
          <OverviewTab tool={tool} dailyCalls={dailyCalls} now={now} />
        ) : null}
        {activeTab === "schema" ? <SchemaTab tool={tool} /> : null}
        {activeTab === "invocations" ? (
          <ToolInvocationsTable
            invocations={invocations}
            nextCursor={invocationsNextCursor}
            onLoadMore={onLoadMoreInvocations}
            now={now}
          />
        ) : null}
        {activeTab === "used_by" ? (
          <UsedByTab
            agents={consumers.agents}
            routines={consumers.routines}
            chats_with_grant={consumers.chats_with_grant}
          />
        ) : null}
        {activeTab === "audit" ? (
          <div data-testid="tool-detail-audit-slot">
            {auditSlot ?? (
              <p style={emptyStyle} role="status">
                No audit slot wired.
              </p>
            )}
          </div>
        ) : null}
        {activeTab === "edit" ? (
          <div data-testid="tool-detail-editor-slot">
            {editorSlot ?? (
              <p style={emptyStyle} role="status">
                No editor slot wired.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </article>
  );
}

// ===========================================================================
// Sub-components.
// ===========================================================================

function KpiStrip({ tool }: { readonly tool: Tool }): ReactElement {
  const u = tool.usage;
  const p50 =
    u.p50_latency_ms_30d === null
      ? "—"
      : u.p50_latency_ms_30d < 1000
        ? `${Math.round(u.p50_latency_ms_30d)}ms`
        : `${(u.p50_latency_ms_30d / 1000).toFixed(2)}s`;
  const success =
    u.success_rate_30d === null
      ? "—"
      : `${Math.round(u.success_rate_30d * 100)}%`;
  return (
    <dl style={kpiStripStyle} data-testid="tool-detail-kpis">
      <Kpi
        label="Calls · 24h"
        value={String(u.calls_24h)}
        testId="tool-detail-kpi-calls-24h"
      />
      <Kpi
        label="Calls · 30d"
        value={String(u.calls_30d)}
        testId="tool-detail-kpi-calls-30d"
      />
      <Kpi label="p50 · 30d" value={p50} testId="tool-detail-kpi-p50-latency" />
      <Kpi
        label="Success · 30d"
        value={success}
        testId="tool-detail-kpi-success-rate"
      />
    </dl>
  );
}

interface KpiProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function Kpi(props: KpiProps): ReactElement {
  return (
    <div style={kpiStyle} data-testid={props.testId}>
      <dt style={kpiLabelStyle}>{props.label}</dt>
      <dd style={kpiValueStyle}>{props.value}</dd>
    </div>
  );
}

function KindChip({ kind }: { readonly kind: Tool["kind"] }): ReactElement {
  return (
    <span
      data-testid="tool-detail-kind-chip"
      data-kind={kind}
      style={chipStyle}
    >
      {kind}
    </span>
  );
}

function ScopeChip({ scope }: { readonly scope: Tool["scope"] }): ReactElement {
  return (
    <span
      data-testid="tool-detail-scope-chip"
      data-scope={scope}
      style={chipStyle}
    >
      scope: {scope}
    </span>
  );
}

interface OverviewTabProps {
  readonly tool: Tool;
  readonly dailyCalls?: ReadonlyArray<ToolDailyCallPoint>;
  readonly now?: number;
}

function OverviewTab({
  tool,
  dailyCalls,
  now,
}: OverviewTabProps): ReactElement {
  return (
    <div data-testid="tool-detail-overview">
      <ToolUsageChart usage={tool.usage} daily_calls={dailyCalls} now={now} />
      <dl style={factsGridStyle} data-testid="tool-detail-facts">
        <Fact label="Kind" value={tool.kind} testId="tool-detail-fact-kind" />
        <Fact
          label="Scope"
          value={tool.scope}
          testId="tool-detail-fact-scope"
        />
        <Fact
          label="Transport"
          value={tool.transport.kind}
          testId="tool-detail-fact-transport"
        />
        <Fact
          label="Tags"
          value={tool.tags.length === 0 ? "—" : tool.tags.join(", ")}
          testId="tool-detail-fact-tags"
        />
      </dl>
    </div>
  );
}

function SchemaTab({ tool }: { readonly tool: Tool }): ReactElement {
  return (
    <div data-testid="tool-detail-schema">
      <section style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Args schema</h3>
        <pre style={codeBlockStyle} data-testid="tool-detail-args-schema">
          {JSON.stringify(tool.args_schema, null, 2)}
        </pre>
      </section>
      <section style={sectionStyle}>
        <h3 style={sectionTitleStyle}>Returns schema</h3>
        <pre style={codeBlockStyle} data-testid="tool-detail-returns-schema">
          {JSON.stringify(tool.returns_schema, null, 2)}
        </pre>
      </section>
    </div>
  );
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
// Helpers.
// ===========================================================================

function statusTone(status: Tool["status"]): ToolBadgeTone {
  if (status === "enabled") return "success";
  return "neutral";
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const heroStyle: CSSProperties = {
  display: "flex",
  gap: 16,
  alignItems: "flex-start",
  flexWrap: "wrap",
};

const heroBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  minWidth: 0,
  flex: 1,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xl)",
  fontWeight: 600,
  color: "var(--color-text)",
};

const descriptionStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm)",
  lineHeight: 1.55,
  color: "var(--color-text-muted)",
};

const pillRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
  marginTop: 4,
};

const chipStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 20,
  padding: "0 8px",
  borderRadius: 999,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  letterSpacing: 0.3,
  textTransform: "uppercase",
};

const kpiStripStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, minmax(80px, 1fr))",
  gap: 8,
  margin: 0,
  padding: 0,
  minWidth: 320,
};

const kpiStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "8px 10px",
  background: "var(--color-bg-elevated)",
  borderRadius: 6,
};

const kpiLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const kpiValueStyle: CSSProperties = {
  fontSize: "var(--font-size-lg)",
  fontWeight: 600,
  color: "var(--color-text)",
  margin: 0,
  fontVariantNumeric: "tabular-nums",
};

const tabStripStyle: CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--color-border)",
  flexWrap: "wrap",
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  background: "transparent",
  border: "none",
  borderBottom: `2px solid ${selected ? "var(--color-accent)" : "transparent"}`,
  color: selected ? "var(--color-text)" : "var(--color-text-muted)",
  padding: "8px 14px",
  fontSize: "var(--font-size-sm)",
  fontFamily: "inherit",
  cursor: "pointer",
});

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const factsGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 8,
  margin: "12px 0 0 0",
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
  gap: 6,
  margin: "8px 0 0 0",
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "var(--color-text-muted)",
};

const codeBlockStyle: CSSProperties = {
  margin: 0,
  padding: 12,
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: "var(--font-size-xs)",
  fontFamily: "var(--font-mono)",
  whiteSpace: "pre-wrap",
  maxHeight: 280,
  overflow: "auto",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  padding: 16,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
  textAlign: "center",
};
