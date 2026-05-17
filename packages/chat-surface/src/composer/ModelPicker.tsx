import { type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

/* Combined Model · Depth popover.
 *
 * Design intent (chat1.md L805-820): "Model · Depth (model list + Fast/
 * Balanced/Deep grid in one popover)". A single popover that owns both
 * axes keeps the two related dials in one place rather than forcing the
 * user to discover two separate toggles.
 *
 * Depth is local-only state at the composer layer for now — the runtime
 * API doesn't accept a depth parameter on the wire yet. The host can
 * read the current depth via `onDepthChange` if it eventually maps it
 * to a model id (Fast → haiku, Balanced → sonnet, Deep → opus) or to a
 * future explicit `reasoning_depth` field. Keep this comment until the
 * backend contract lands so future agents don't ship the value to a
 * field that ignores it. */

export interface ModelDescriptor {
  readonly id: string;
  readonly label: string;
  readonly family: string;
}

export type Depth = "fast" | "balanced" | "deep";

interface DepthDescriptor {
  readonly id: Depth;
  readonly label: string;
  readonly sub: string;
}

const MODELS: ReadonlyArray<ModelDescriptor> = [
  { id: "claude-opus-4-7", label: "Opus 4.7", family: "Claude" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6", family: "Claude" },
  { id: "claude-haiku-4-5", label: "Haiku 4.5", family: "Claude" },
];

const DEPTHS: ReadonlyArray<DepthDescriptor> = [
  { id: "fast", label: "Fast", sub: "low latency" },
  { id: "balanced", label: "Balanced", sub: "default" },
  { id: "deep", label: "Deep", sub: "more reasoning" },
];

export interface ModelPickerProps {
  readonly open: boolean;
  readonly selectedModel: string;
  readonly selectedDepth?: Depth;
  readonly onSelect: (modelId: string) => void;
  readonly onDepthChange?: (depth: Depth) => void;
  readonly onClose: () => void;
  readonly portalTarget?: HTMLElement;
}

export function ModelPicker(props: ModelPickerProps): ReactNode {
  const {
    open,
    selectedModel,
    selectedDepth = "balanced",
    onSelect,
    onDepthChange,
    onClose,
    portalTarget,
  } = props;

  if (!open) {
    return null;
  }

  /* Selecting a model closes the popover — same as the design composer
   * behaviour. Depth chips do NOT close the popover; users frequently
   * tune depth after picking a model, and a closing popover here would
   * force them to re-open it. */
  const handleSelectModel = (id: string): void => {
    onSelect(id);
    onClose();
  };

  const handleSelectDepth = (depth: Depth): void => {
    if (onDepthChange !== undefined) {
      onDepthChange(depth);
    }
  };

  const panel = (
    <div
      role="listbox"
      aria-label="Choose model and depth"
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
                onClick={() => handleSelectModel(m.id)}
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
      <div style={dividerStyle} aria-hidden="true" />
      <div style={headerRowStyle}>
        <span style={headerStyle}>Depth</span>
      </div>
      <div
        role="radiogroup"
        aria-label="Choose depth"
        data-testid="depth-picker"
        style={depthGridStyle}
      >
        {DEPTHS.map((d) => {
          const selected = d.id === selectedDepth;
          return (
            <button
              key={d.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => handleSelectDepth(d.id)}
              style={depthChipStyle(selected)}
              data-testid={`depth-picker-row-${d.id}`}
            >
              <span style={depthLabelStyle}>{d.label}</span>
              <span style={depthSubStyle}>{d.sub}</span>
            </button>
          );
        })}
      </div>
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

export function listDepthDescriptors(): ReadonlyArray<DepthDescriptor> {
  return DEPTHS;
}

const panelStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  padding: 8,
  width: 280,
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const portaledStyle: CSSProperties = {
  ...panelStyle,
  position: "absolute",
  boxShadow: "var(--shadow-soft)",
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
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
};

const closeButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--color-text-muted)",
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
  background: selected ? "var(--color-surface-muted)" : "transparent",
  border: "none",
  borderRadius: 6,
  padding: "8px 10px",
  color: "var(--color-text)",
  cursor: "pointer",
  textAlign: "left",
  fontSize: 13,
});

const labelStyle: CSSProperties = {
  flex: 1,
};

const familyStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--color-text-muted)",
};

const checkStyle: CSSProperties = {
  color: "var(--color-accent)",
  fontWeight: 600,
};

const dividerStyle: CSSProperties = {
  height: 1,
  background: "var(--color-border)",
  margin: "4px 0",
};

const depthGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(3, 1fr)",
  gap: 4,
  padding: "0 2px 2px",
};

const depthChipStyle = (selected: boolean): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 2,
  background: selected ? "var(--color-surface-muted)" : "transparent",
  border: `1px solid ${selected ? "var(--color-accent)" : "var(--color-border)"}`,
  borderRadius: 6,
  padding: "6px 8px",
  color: "var(--color-text)",
  cursor: "pointer",
  textAlign: "left",
  fontSize: 12,
});

const depthLabelStyle: CSSProperties = {
  fontWeight: 600,
  color: "var(--color-text)",
};

const depthSubStyle: CSSProperties = {
  fontSize: 10.5,
  color: "var(--color-text-muted)",
};
