import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { useTransport } from "../providers/TransportProvider";

/* Unified Tools popover.
 *
 * Design intent (chat1.md L805-820): "Tools (skills + MCPs combined in a
 * single popover, sectioned)". One popover, two sections. The trigger
 * button in the composer is also a single button — see Composer.tsx.
 *
 * Wire shape: the transport returns a flat list at /v1/mcp/tools. Each
 * descriptor optionally carries a `kind` ("skill" | "mcp"). Older
 * responses without `kind` fall into the MCPs section (the historical
 * meaning of /v1/mcp/tools). When the backend ships a skills bundle
 * with kind="skill" the same UI lights up — no breaking change. */

export type ToolKind = "skill" | "mcp";

export interface ToolDescriptor {
  readonly name: string;
  readonly label: string;
  readonly description?: string;
  readonly kind?: ToolKind;
}

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

interface ToolListResponse {
  readonly tools?: ReadonlyArray<ToolDescriptor>;
}

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
  /* Partition by kind. Anything without `kind` goes to MCPs — that's
   * the historical contract of /v1/mcp/tools. Skills only appear when
   * the server explicitly tags them. */
  const skills = state.tools.filter((t) => t.kind === "skill");
  const mcps = state.tools.filter((t) => t.kind !== "skill");

  return (
    <div style={sectionedListStyle}>
      {skills.length > 0 ? (
        <ToolSection
          title="Skills"
          tools={skills}
          selectedTools={selectedTools}
          onToggle={onToggle}
          testId="tool-picker-section-skills"
        />
      ) : null}
      {mcps.length > 0 ? (
        <ToolSection
          title="MCPs"
          tools={mcps}
          selectedTools={selectedTools}
          onToggle={onToggle}
          testId="tool-picker-section-mcps"
        />
      ) : null}
    </div>
  );
}

interface SectionProps {
  readonly title: string;
  readonly tools: ReadonlyArray<ToolDescriptor>;
  readonly selectedTools: ReadonlyArray<string>;
  readonly onToggle: (name: string) => void;
  readonly testId: string;
}

function ToolSection(props: SectionProps): ReactNode {
  const { title, tools, selectedTools, onToggle, testId } = props;
  return (
    <div data-testid={testId} style={sectionStyle}>
      <div style={sectionHeaderStyle}>{title}</div>
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
    </div>
  );
}

const panelStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  padding: 8,
  width: 300,
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

const statusStyle: CSSProperties = {
  padding: "10px 12px",
  color: "var(--color-text-muted)",
  fontSize: 12,
};

const sectionedListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  maxHeight: 360,
  overflowY: "auto",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const sectionHeaderStyle: CSSProperties = {
  fontSize: 10.5,
  letterSpacing: 0.4,
  color: "var(--color-text-subtle)",
  textTransform: "uppercase",
  padding: "2px 8px",
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

const descriptionStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--color-text-muted)",
};

const checkStyle: CSSProperties = {
  color: "var(--color-accent)",
  fontWeight: 600,
};
