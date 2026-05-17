import type { CSSProperties, ReactNode } from "react";

export interface SalesforceOpportunityFieldChange {
  readonly key: string;
  readonly label: string;
  readonly previousValue: string;
  readonly nextValue: string;
  readonly provenance: string;
}

export interface OpportunityFieldRowProps {
  readonly fieldKey: string;
  readonly label: string;
  readonly value: string;
  readonly change?: SalesforceOpportunityFieldChange;
}

const PALETTE = {
  border: "#2a2d31",
  textHi: "#f4f5f6",
  textMid: "#c8ccd1",
  textLo: "#9aa0a6",
  lime: "#c2ff5a",
  limeBgSoft: "rgba(194, 255, 90, 0.12)",
} as const;

export function OpportunityFieldRow(
  props: OpportunityFieldRowProps,
): ReactNode {
  const { fieldKey, label, value, change } = props;
  if (change) {
    return (
      <div
        style={changedRowStyle}
        data-testid={`sf-field-${fieldKey}`}
        data-changed="true"
      >
        <div style={fieldHeaderStyle}>
          <span style={fieldLabelStyle}>{label}</span>
          <span
            style={provenancePillStyle}
            data-testid={`sf-field-${fieldKey}-provenance`}
          >
            <span aria-hidden="true" style={provenanceDotStyle} />
            {change.provenance}
          </span>
        </div>
        <div style={diffPairStyle}>
          <span
            style={previousValueStyle}
            data-testid={`sf-field-${fieldKey}-previous`}
          >
            {change.previousValue || " "}
          </span>
          <span aria-hidden="true" style={arrowStyle}>
            →
          </span>
          <span
            style={nextValueStyle}
            data-testid={`sf-field-${fieldKey}-next`}
          >
            {change.nextValue || " "}
          </span>
        </div>
      </div>
    );
  }
  return (
    <div style={rowStyle} data-testid={`sf-field-${fieldKey}`}>
      <span style={fieldLabelStyle}>{label}</span>
      <span style={fieldValueStyle} data-testid={`sf-field-${fieldKey}-value`}>
        {value || " "}
      </span>
    </div>
  );
}

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "140px 1fr",
  alignItems: "baseline",
  gap: 12,
  paddingBlock: 6,
  borderBottom: `1px solid ${PALETTE.border}`,
};

const changedRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  paddingBlock: 8,
  paddingInline: 10,
  borderRadius: 8,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
  marginBlock: 2,
};

const fieldHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};

const fieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 12,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  fontWeight: 600,
};

const fieldValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: 13,
};

const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.border}`,
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const provenanceDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.lime,
};

const diffPairStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  fontSize: 13,
};

const previousValueStyle: CSSProperties = {
  color: PALETTE.textLo,
  textDecoration: "line-through",
  textDecorationColor: PALETTE.textLo,
};

const arrowStyle: CSSProperties = {
  color: PALETTE.textMid,
  fontSize: 12,
};

const nextValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  background: "rgba(194, 255, 90, 0.18)",
  padding: "1px 6px",
  borderRadius: 4,
};
