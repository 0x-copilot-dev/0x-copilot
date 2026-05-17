import { type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface ModelDescriptor {
  readonly id: string;
  readonly label: string;
  readonly family: string;
}

const MODELS: ReadonlyArray<ModelDescriptor> = [
  { id: "claude-opus-4-7", label: "Opus 4.7", family: "Claude" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6", family: "Claude" },
  { id: "claude-haiku-4-5", label: "Haiku 4.5", family: "Claude" },
];

export interface ModelPickerProps {
  readonly open: boolean;
  readonly selectedModel: string;
  readonly onSelect: (modelId: string) => void;
  readonly onClose: () => void;
  readonly portalTarget?: HTMLElement;
}

export function ModelPicker(props: ModelPickerProps): ReactNode {
  const { open, selectedModel, onSelect, onClose, portalTarget } = props;

  if (!open) {
    return null;
  }

  const handleSelect = (id: string): void => {
    onSelect(id);
    onClose();
  };

  const panel = (
    <div
      role="listbox"
      aria-label="Choose model"
      data-testid="model-picker"
      style={portalTarget !== undefined ? portaledStyle : panelStyle}
    >
      <div style={headerRowStyle}>
        <span style={headerStyle}>Model</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close model picker"
          style={closeButtonStyle}
          data-testid="model-picker-close"
        >
          ×
        </button>
      </div>
      <ul style={listStyle}>
        {MODELS.map((m) => {
          const selected = m.id === selectedModel;
          return (
            <li key={m.id} style={listItemStyle}>
              <button
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => handleSelect(m.id)}
                style={rowStyle(selected)}
                data-testid={`model-picker-row-${m.id}`}
              >
                <span style={labelStyle}>{m.label}</span>
                <span style={familyStyle}>{m.family}</span>
                {selected ? (
                  <span aria-hidden="true" style={checkStyle}>
                    ✓
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );

  if (portalTarget !== undefined) {
    return createPortal(panel, portalTarget);
  }
  return panel;
}

export function listModelDescriptors(): ReadonlyArray<ModelDescriptor> {
  return MODELS;
}

const PALETTE = {
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  rowHover: "#1f2225",
  rowSelected: "#23262a",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  accent: "#c2ff5a",
} as const;

const panelStyle: CSSProperties = {
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 10,
  padding: 8,
  width: 240,
  color: PALETTE.textHi,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: 13,
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const portaledStyle: CSSProperties = {
  ...panelStyle,
  position: "absolute",
  boxShadow: "0 8px 24px rgba(0, 0, 0, 0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "2px 6px",
};

const headerStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const closeButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: PALETTE.textLo,
  fontSize: 16,
  cursor: "pointer",
  lineHeight: 1,
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const listItemStyle: CSSProperties = {
  margin: 0,
};

const rowStyle = (selected: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  background: selected ? PALETTE.rowSelected : "transparent",
  border: "none",
  borderRadius: 6,
  padding: "8px 10px",
  color: PALETTE.textHi,
  cursor: "pointer",
  textAlign: "left",
  fontSize: 13,
});

const labelStyle: CSSProperties = {
  flex: 1,
};

const familyStyle: CSSProperties = {
  fontSize: 11,
  color: PALETTE.textLo,
};

const checkStyle: CSSProperties = {
  color: PALETTE.accent,
  fontWeight: 600,
};
