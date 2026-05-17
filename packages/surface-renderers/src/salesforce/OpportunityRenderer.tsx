import type { CSSProperties, ReactElement } from "react";

import {
  TcInlineDiff,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

import {
  OpportunityFieldRow,
  type SalesforceOpportunityFieldChange,
} from "./OpportunityDiff";

export interface SalesforceOpportunityCustomField {
  readonly key: string;
  readonly label: string;
  readonly value: string;
}

export interface SalesforceOpportunity {
  readonly id: string;
  readonly name: string;
  readonly account: string;
  readonly stage: string;
  readonly closeDate: string;
  readonly arr: string;
  readonly owner: string;
  readonly customFields: readonly SalesforceOpportunityCustomField[];
}

export interface SalesforceOpportunityDiff {
  readonly diffId: string;
  readonly opportunity: SalesforceOpportunity;
  readonly changes: readonly SalesforceOpportunityFieldChange[];
}

interface StandardField {
  readonly key: keyof SalesforceOpportunity;
  readonly label: string;
}

const STANDARD_FIELDS: readonly StandardField[] = [
  { key: "account", label: "Account" },
  { key: "stage", label: "Stage" },
  { key: "closeDate", label: "Close Date" },
  { key: "arr", label: "ARR" },
  { key: "owner", label: "Owner" },
];

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";

export function OpportunityRenderer(
  opportunity: SalesforceOpportunity,
): ReactElement {
  const changes = new Map<string, SalesforceOpportunityFieldChange>();
  return renderOpportunity(opportunity, changes, null);
}

export function OpportunityDiffRenderer(
  diff: SalesforceOpportunityDiff,
): ReactElement {
  const changes = new Map<string, SalesforceOpportunityFieldChange>();
  for (const change of diff.changes) {
    changes.set(change.key, change);
  }
  return renderOpportunity(diff.opportunity, changes, diff);
}

function renderOpportunity(
  opportunity: SalesforceOpportunity,
  changes: ReadonlyMap<string, SalesforceOpportunityFieldChange>,
  diff: SalesforceOpportunityDiff | null,
): ReactElement {
  const isDiff = diff !== null;
  const headerProvenance =
    diff && diff.changes.length > 0 ? diff.changes[0].provenance : undefined;
  return (
    <article
      style={pageStyle}
      data-testid="sf-opportunity-renderer"
      data-mode={isDiff ? "diff" : "current"}
      data-opportunity-id={opportunity.id}
      aria-label={`Salesforce opportunity ${opportunity.name}`}
    >
      <section style={cardStyle}>
        <header style={headerRowStyle}>
          <div style={headerTitleStyle}>
            <span style={kickerStyle}>Salesforce · Opportunity</span>
            <span style={titleStyle}>{opportunity.name}</span>
          </div>
          <span style={idPillStyle} data-testid="sf-opportunity-id">
            {opportunity.id}
          </span>
        </header>
        {isDiff ? (
          <div style={diffHeaderStyle} data-testid="sf-opportunity-diff-header">
            <TcInlineDiff
              state="streaming"
              progressPercent={100}
              provenance={headerProvenance}
              title={`${diff.changes.length} pending field ${diff.changes.length === 1 ? "change" : "changes"}`}
              description="Approve / reject in the host actions row."
            />
          </div>
        ) : null}
        <div style={fieldsStyle}>
          {STANDARD_FIELDS.map((field) => (
            <OpportunityFieldRow
              key={field.key}
              fieldKey={field.key}
              label={field.label}
              value={String(opportunity[field.key] ?? "")}
              change={changes.get(field.key)}
            />
          ))}
          {opportunity.customFields.map((custom) => (
            <OpportunityFieldRow
              key={`custom:${custom.key}`}
              fieldKey={custom.key}
              label={custom.label}
              value={custom.value}
              change={changes.get(custom.key)}
            />
          ))}
        </div>
      </section>
    </article>
  );
}

export const opportunityAdapter: SaaSRendererAdapter<
  SalesforceOpportunity,
  SalesforceOpportunityDiff
> = {
  scheme: "sf-opp",
  matches: (uri: string) => uri.startsWith("sf-opp://"),
  renderCurrent: (state: SalesforceOpportunity): ReactElement =>
    OpportunityRenderer(state),
  renderDiff: (diff: SalesforceOpportunityDiff): ReactElement =>
    OpportunityDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

const pageStyle: CSSProperties = {
  background: PALETTE.pageBg,
  minHeight: "100%",
  padding: 24,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  color: PALETTE.textHi,
  display: "flex",
  justifyContent: "center",
};

const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 14,
  width: "100%",
  maxWidth: 760,
  display: "flex",
  flexDirection: "column",
  gap: 18,
  padding: 22,
  boxShadow: "0 8px 28px rgba(0,0,0,0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: 12,
  gap: 12,
};

const headerTitleStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const kickerStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.6,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  fontSize: 18,
  color: PALETTE.textHi,
  fontWeight: 600,
};

const idPillStyle: CSSProperties = {
  background: "transparent",
  border: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  fontSize: 11,
  padding: "3px 8px",
  borderRadius: 999,
  letterSpacing: 0.4,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

const diffHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-start",
};

const fieldsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};
