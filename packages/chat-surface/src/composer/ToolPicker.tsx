import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import type {
  ToolDescriptor,
  ToolKind,
  ToolListResponse,
} from "@enterprise-search/api-types";

import { useTransport } from "../providers/TransportProvider";

export type { ToolDescriptor, ToolKind } from "@enterprise-search/api-types";

export interface ToolPickerProps {
  readonly open: boolean;
  readonly selectedTools: ReadonlyArray<string>;
  readonly onToggle: (toolName: string) => void;
  readonly onClose: () => void;
  readonly portalTarget?: HTMLElement;
}

type LoadState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly tools: ReadonlyArray<ToolDescriptor> }
  | { readonly status: "error" };

export function ToolPicker(props: ToolPickerProps): ReactNode {
  const { open, selectedTools, onToggle, onClose, portalTarget } = props;
  const transport = useTransport();
  const [state, setState] = useState<LoadState>({ status: "idle" });
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!open || loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    let cancelled = false;
    setState({ status: "loading" });
    transport
      .request<ToolListResponse>({
        method: "GET",
        path: "/v1/mcp/tools",
      })
      .then((res) => {
        if (cancelled) {
          return;
        }
        setState({ status: "ready", tools: res.tools ?? [] });
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [open, transport]);

  if (!open) {
    return null;
  }

  const panel = (
    <div
      role="listbox"
      aria-multiselectable="true"
      aria-label="Choose tools"
      data-testid="tool-picker"
      style={portalTarget !== undefined ? portaledStyle : panelStyle}
    >
      <div style={headerRowStyle}>
        <span style={headerStyle}>Tools</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close tool picker"
          style={closeButtonStyle}
          data-testid="tool-picker-close"
        >
          ×
        </button>
      </div>
      <ToolPickerBody
        state={state}
        selectedTools={selectedTools}
        onToggle={onToggle}
      />
    </div>
  );

  if (portalTarget !== undefined) {
    return createPortal(panel, portalTarget);
  }
  return panel;
}

interface BodyProps {
  readonly state: LoadState;
  readonly selectedTools: ReadonlyArray<string>;
  readonly onToggle: (name: string) => void;
}

function ToolPickerBody(props: BodyProps): ReactNode {
  const { state, selectedTools, onToggle } = props;

  if (state.status === "loading" || state.status === "idle") {
    return (
      <div role="status" style={statusStyle} data-testid="tool-picker-loading">
        Loading tools…
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" style={statusStyle} data-testid="tool-picker-error">
        Failed to load tools.
      </div>
    );
  }
  if (state.tools.length === 0) {
    return (
      <div role="status" style={statusStyle} data-testid="tool-picker-empty">
        No tools available.
      </div>
    );
  }
  const skills = state.tools.filter((t) => t.kind === "skill");
  const mcps = state.tools.filter((t) => t.kind === "mcp");
  return (
    <div style={sectionsStyle}>
      <ToolPickerSection
        kind="skill"
        title="Skills"
        tools={skills}
        selectedTools={selectedTools}
        onToggle={onToggle}
      />
      <ToolPickerSection
        kind="mcp"
        title="MCPs"
        tools={mcps}
        selectedTools={selectedTools}
        onToggle={onToggle}
      />
    </div>
  );
}

interface SectionProps {
  readonly kind: ToolKind;
  readonly title: string;
  readonly tools: ReadonlyArray<ToolDescriptor>;
  readonly selectedTools: ReadonlyArray<string>;
  readonly onToggle: (name: string) => void;
}

function ToolPickerSection(props: SectionProps): ReactNode {
  const { kind, title, tools, selectedTools, onToggle } = props;
  if (tools.length === 0) {
    return null;
  }
  return (
    <section data-testid={`tool-picker-section-${kind}`}>
      <div style={sectionTitleStyle}>{title}</div>
      <ul style={listStyle}>
        {tools.map((t) => {
          const selected = selectedTools.includes(t.name);
          return (
            <li key={t.name} style={listItemStyle}>
              <button
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => onToggle(t.name)}
                style={rowStyle(selected)}
                data-testid={`tool-picker-row-${t.name}`}
              >
                <span style={labelStyle}>{t.label}</span>
                {t.description ? (
                  <span style={descriptionStyle}>{t.description}</span>
                ) : null}
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
    </section>
  );
}

const PALETTE = {
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  rowSelected: "#23262a",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  accent: "#c2ff5a",
  error: "#ef5a5a",
} as const;

const panelStyle: CSSProperties = {
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 10,
  padding: 8,
  width: 280,
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

const statusStyle: CSSProperties = {
  padding: "10px 12px",
  color: PALETTE.textLo,
  fontSize: 12,
};

const sectionsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  maxHeight: 320,
  overflowY: "auto",
};

const sectionTitleStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
  padding: "4px 6px 2px",
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

const descriptionStyle: CSSProperties = {
  fontSize: 11,
  color: PALETTE.textLo,
};

const checkStyle: CSSProperties = {
  color: PALETTE.accent,
  fontWeight: 600,
};
