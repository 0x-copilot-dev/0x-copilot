// ToolsPopover — the connector-aware first-run Tools popover (PRD-P4).
//
// Replaces the flat `composer/ToolPicker` toggle list FOR the FTUE. Sections,
// top-to-bottom, byte-verbatim vs SPEC §"Tools popover":
//   • Header      — "Tools" + meta `{n} on · none required` + close
//   • Web search  — built-in toggle, default on (host owns the default;
//                   the component only reflects `webSearchEnabled`)
//   • Connected   — workspace-installed + authenticated connectors, each with
//                   a per-run active/paused toggle (no conversation exists yet,
//                   so state is held by the surface via `activeConnectorIds`)
//   • Installable — curated 1-click rows; group note
//                   `1-click connect · you approve first use`.
//                   `requiresPreRegisteredClient` → the host routes the click
//                   to the custom-config form (a keyless install would 422).
//   • Custom MCP  — "Custom MCP server" → host opens the paste-a-config form.
//
// Data comes from the host-injected `FirstRunConnectorsPort` (fetched once on
// open) and is classified by the pure `projectFirstRunConnectors`. The package
// has no `document`; portalling is opt-in via host-owned `portalTarget`.

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import {
  firstRunActiveToolCount,
  projectFirstRunConnectors,
  type FirstRunConnectedConnector,
  type FirstRunInstallableConnector,
} from "./projectFirstRunConnectors";

export const TOOLS_POPOVER_COPY = {
  title: "Tools",
  metaSuffix: "none required",
  webSearchLabel: "Web search",
  webSearchHint: "built-in",
  connectedHeader: "Connected",
  installableHeader: "Add a connector",
  installableNote: "1-click connect · you approve first use",
  connectLabel: "Connect",
  setupLabel: "Set up",
  customLabel: "Custom MCP server",
  customHint: "paste a JSON config",
  emptyConnectors: "No connectors yet",
} as const;

export interface ToolsPopoverProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Host-injected MCP surface (servers + catalog + install + auth). */
  readonly port: FirstRunConnectorsPort;
  /** Built-in web search; default TRUE is owned by the surface. */
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  /** Per-run active connector ids (component state — no conversation yet). */
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  /**
   * 1-click connect of a catalog entry. The host mirrors
   * `ChatScreen.onMcpInstallCatalog`: `requiresPreRegisteredClient` → open the
   * custom-config form; else installFromCatalog → beginAuth.
   */
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  /** Open the host's custom-MCP form. */
  readonly onAddCustom: () => void;
  /** Host-owned portal root — the package has no `document`. */
  readonly portalTarget?: HTMLElement;
}

type LoadState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | {
      readonly status: "ready";
      readonly servers: readonly McpServer[];
      readonly catalog: readonly McpCatalogEntry[];
    }
  | { readonly status: "error" };

export function ToolsPopover(props: ToolsPopoverProps): ReactNode {
  const {
    open,
    onClose,
    port,
    webSearchEnabled,
    onToggleWebSearch,
    activeConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    onAddCustom,
    portalTarget,
  } = props;

  const [state, setState] = useState<LoadState>({ status: "idle" });
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!open || loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    let cancelled = false;
    setState({ status: "loading" });
    Promise.all([port.listServers(), port.listCatalog()])
      .then(([servers, catalog]) => {
        if (cancelled) {
          return;
        }
        setState({ status: "ready", servers, catalog });
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
  }, [open, port]);

  if (!open) {
    return null;
  }

  const projection =
    state.status === "ready"
      ? projectFirstRunConnectors(state.servers, state.catalog)
      : { connected: [], installable: [] };
  const activeCount = firstRunActiveToolCount(
    webSearchEnabled,
    projection.connected,
    activeConnectorIds,
  );

  const panel = (
    <div
      role="dialog"
      aria-label="Tools"
      data-testid="first-run-tools-popover"
      style={portalTarget !== undefined ? portaledStyle : panelStyle}
    >
      <div style={headerRowStyle}>
        <span style={headerTitleStyle}>{TOOLS_POPOVER_COPY.title}</span>
        <span style={metaStyle} data-testid="first-run-tools-meta">
          {activeCount} on · {TOOLS_POPOVER_COPY.metaSuffix}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close tools"
          style={closeButtonStyle}
          data-testid="first-run-tools-close"
        >
          ×
        </button>
      </div>

      <WebSearchRow enabled={webSearchEnabled} onToggle={onToggleWebSearch} />

      <PopoverBody
        state={state}
        connected={projection.connected}
        installable={projection.installable}
        activeConnectorIds={activeConnectorIds}
        onToggleConnector={onToggleConnector}
        onConnectCatalog={onConnectCatalog}
      />

      <CustomRow onAddCustom={onAddCustom} />
    </div>
  );

  if (portalTarget !== undefined) {
    return createPortal(panel, portalTarget);
  }
  return panel;
}

function WebSearchRow(props: {
  readonly enabled: boolean;
  readonly onToggle: (next: boolean) => void;
}): ReactNode {
  const { enabled, onToggle } = props;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={() => onToggle(!enabled)}
      style={rowButtonStyle}
      data-testid="first-run-tools-websearch"
    >
      <span style={rowMainStyle}>
        <span style={rowLabelStyle}>{TOOLS_POPOVER_COPY.webSearchLabel}</span>
        <span style={rowHintStyle}>{TOOLS_POPOVER_COPY.webSearchHint}</span>
      </span>
      <ToggleGlyph on={enabled} />
    </button>
  );
}

interface BodyProps {
  readonly state: LoadState;
  readonly connected: readonly FirstRunConnectedConnector[];
  readonly installable: readonly FirstRunInstallableConnector[];
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
}

function PopoverBody(props: BodyProps): ReactNode {
  const {
    state,
    connected,
    installable,
    activeConnectorIds,
    onToggleConnector,
    onConnectCatalog,
  } = props;

  if (state.status === "loading" || state.status === "idle") {
    return (
      <div
        role="status"
        style={statusStyle}
        data-testid="first-run-tools-loading"
      >
        Loading connectors…
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" style={statusStyle} data-testid="first-run-tools-error">
        Couldn't load connectors.
      </div>
    );
  }
  if (connected.length === 0 && installable.length === 0) {
    return (
      <div
        role="status"
        style={statusStyle}
        data-testid="first-run-tools-empty"
      >
        {TOOLS_POPOVER_COPY.emptyConnectors}
      </div>
    );
  }

  const activeSet = new Set(activeConnectorIds);

  return (
    <div style={scrollStyle}>
      {connected.length > 0 ? (
        <section style={sectionStyle} data-testid="first-run-tools-connected">
          <div style={sectionHeaderStyle}>
            {TOOLS_POPOVER_COPY.connectedHeader}
          </div>
          {connected.map((row) => {
            const active = activeSet.has(row.serverId);
            return (
              <button
                key={row.serverId}
                type="button"
                role="switch"
                aria-checked={active}
                onClick={() => onToggleConnector(row.serverId, !active)}
                style={rowButtonStyle}
                data-testid={`first-run-tools-connected-${row.serverId}`}
              >
                <span style={rowMainStyle}>
                  <span style={rowLabelStyle}>{row.displayName}</span>
                  {row.scopesSummary ? (
                    <span style={rowHintStyle}>{row.scopesSummary}</span>
                  ) : null}
                </span>
                <ToggleGlyph on={active} />
              </button>
            );
          })}
        </section>
      ) : null}

      {installable.length > 0 ? (
        <section style={sectionStyle} data-testid="first-run-tools-installable">
          <div style={sectionHeaderStyle}>
            {TOOLS_POPOVER_COPY.installableHeader}
          </div>
          <div
            style={sectionNoteStyle}
            data-testid="first-run-tools-installable-note"
          >
            {TOOLS_POPOVER_COPY.installableNote}
          </div>
          {installable.map((entry) => (
            <button
              key={entry.slug}
              type="button"
              onClick={() => onConnectCatalog(entry)}
              style={rowButtonStyle}
              data-testid={`first-run-tools-connect-${entry.slug}`}
            >
              <span style={rowMainStyle}>
                <span style={rowLabelStyle}>{entry.displayName}</span>
                {entry.description ? (
                  <span style={rowHintStyle}>{entry.description}</span>
                ) : null}
              </span>
              <span style={connectPillStyle} aria-hidden="true">
                {entry.requiresPreRegisteredClient
                  ? TOOLS_POPOVER_COPY.setupLabel
                  : TOOLS_POPOVER_COPY.connectLabel}
              </span>
            </button>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function CustomRow(props: { readonly onAddCustom: () => void }): ReactNode {
  return (
    <button
      type="button"
      onClick={props.onAddCustom}
      style={{
        ...rowButtonStyle,
        borderTop: "1px solid var(--color-border-subtle)",
      }}
      data-testid="first-run-tools-custom"
    >
      <span style={rowMainStyle}>
        <span style={rowLabelStyle}>{TOOLS_POPOVER_COPY.customLabel}</span>
        <span style={rowHintStyle}>{TOOLS_POPOVER_COPY.customHint}</span>
      </span>
      <span style={connectPillStyle} aria-hidden="true">
        +
      </span>
    </button>
  );
}

function ToggleGlyph(props: { readonly on: boolean }): ReactNode {
  return (
    <span
      aria-hidden="true"
      style={{
        ...toggleTrackStyle,
        background: props.on
          ? "var(--color-success)"
          : "var(--color-surface-muted)",
        borderColor: props.on
          ? "var(--color-success)"
          : "var(--color-border-strong)",
      }}
    >
      <span
        style={{
          ...toggleKnobStyle,
          transform: props.on ? "translateX(14px)" : "translateX(0)",
        }}
      />
    </span>
  );
}

/* ── styles (design-system tokens only; no raw hex) ─────────────────── */

const panelStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  padding: 8,
  width: 320,
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-sm)",
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const portaledStyle: CSSProperties = {
  ...panelStyle,
  position: "absolute",
  boxShadow: "var(--shadow-soft)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "2px 6px",
};

const headerTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  letterSpacing: 0.4,
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
};

const metaStyle: CSSProperties = {
  flex: 1,
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
};

const closeButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-lg)",
  cursor: "pointer",
  lineHeight: 1,
};

const statusStyle: CSSProperties = {
  padding: "10px 12px",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
};

const scrollStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  maxHeight: 320,
  overflowY: "auto",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const sectionHeaderStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  letterSpacing: 0.4,
  color: "var(--color-text-subtle)",
  textTransform: "uppercase",
  padding: "2px 8px",
};

const sectionNoteStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  padding: "0 8px 2px",
};

const rowButtonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  background: "transparent",
  border: "none",
  borderRadius: 6,
  padding: "8px 10px",
  color: "var(--color-text)",
  cursor: "pointer",
  textAlign: "left",
  fontSize: "var(--font-size-sm)",
};

const rowMainStyle: CSSProperties = {
  flex: 1,
  display: "flex",
  flexDirection: "column",
  gap: 1,
  minWidth: 0,
};

const rowLabelStyle: CSSProperties = {
  color: "var(--color-text)",
};

const rowHintStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
};

const connectPillStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-accent)",
  border: "1px solid var(--color-accent-line)",
  borderRadius: "var(--radius-full)",
  padding: "2px 8px",
  whiteSpace: "nowrap",
};

const toggleTrackStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  width: 30,
  height: 16,
  borderRadius: "var(--radius-full)",
  border: "1px solid var(--color-border-strong)",
  padding: 1,
  transition: "background 120ms ease",
  flexShrink: 0,
};

const toggleKnobStyle: CSSProperties = {
  width: 12,
  height: 12,
  borderRadius: "var(--radius-full)",
  background: "var(--color-bg-elevated)",
  transition: "transform 120ms ease",
};
