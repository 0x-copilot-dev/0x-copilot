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
//
// ── Design parity, composer punch-list rows 43 + 46 ─────────────────────────
// This surface used to be styled with 100% inline `CSSProperties` objects — a
// third private idiom next to the model popover's `.ui-pop` and the composer
// plus-menu's `.aui-plus-menu`. It now renders the SHARED `.ui-pop*` recipe
// from `@0x-copilot/design-system` (the design's `.pop` family in
// `tools/design-parity/design-kit/copilot-v3.css`), at the design's 318px tools
// width, mapped 1:1:
//
//   .ui-pop / .ui-pop__h / .ui-pop__h-meta   panel + header + `{n} on` meta
//   .ui-pop__list                            the one scroll region (264px cap)
//   .ui-pop__grp                             "Connected" / "Add a connector"
//   .ui-pop-row + __lg/__m/__nm/__txt/__sb   every row (24px badge · name · sub)
//   .ui-pop-row[data-off]                    a paused row dims, as in the design
//   .ui-pop__div                             web-search ↔ connectors divider
//   .ui-pop-row--pin                         the pinned "Custom MCP server" row
//
// The design's trailing control on a `.pop-row` is a radio; ours is a TOGGLE
// (rows are per-run active/paused, not a single selection), so the toggle keeps
// the radio's trailing slot and is retuned to the design's `.ctog--sm` metrics
// (28x17 track, 11px knob, accent fill when on). The row itself stays the
// `role="switch"` control it always was — the whole row is the hit area and the
// tests assert `aria-checked` on it.
//
// ROW 46 — click-out scrim. The design puts a transparent `.pop-scrim` behind
// every popover (fixed, inset 0, z-index 70; the panel at 71) and dismisses on
// mousedown. `.ui-pop-scrim` is that element. It is RENDERED, not a `document`
// listener — this package bans bare globals, and this popover is self-anchored
// (it is not inside a design-system `Menu`, so there is no double-dismissal).
// Escape dismisses too: the panel is a `role="dialog"` with `tabIndex={-1}` and
// takes focus on open, so a keydown lands inside it without a global listener.
//
// The design's tools popover also carries a `.pop__f` footer ("Manage tools →"
// / "Approval policy →"). Deliberately NOT added: this component has no
// navigation callbacks, and inventing them would be a feature change, not a
// restyle. Likewise the design's `.permc` "acts"/"reads" chips have no data
// behind them here — `FirstRunConnectedConnector` carries no permission field.

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import { Icon } from "../icons/Icon";
import { providerInitials } from "../icons/providerMarks";
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
  const panelRef = useRef<HTMLDivElement | null>(null);

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

  // Escape-to-close needs the keydown to land inside the panel — this package
  // cannot attach a `window`/`document` listener. Taking focus on open is the
  // `role="dialog"` contract anyway, and it is what makes the design's "Escape
  // closes" work when the popover was opened with the mouse.
  useEffect(() => {
    if (!open) {
      return;
    }
    panelRef.current?.focus({ preventScroll: true });
  }, [open]);

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

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  };

  const panel = (
    <>
      {/* The design's `.pop-scrim`: transparent, viewport-filling, one z-step
          below the panel. Mousedown anywhere outside dismisses. */}
      <div className="ui-pop-scrim" onMouseDown={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-label="Tools"
        tabIndex={-1}
        data-testid="first-run-tools-popover"
        className="ui-pop"
        style={portalTarget !== undefined ? portaledStyle : panelStyle}
        onKeyDown={onKeyDown}
      >
        <div className="ui-pop__h">
          {TOOLS_POPOVER_COPY.title}
          <span className="ui-pop__h-meta" data-testid="first-run-tools-meta">
            {activeCount} on · {TOOLS_POPOVER_COPY.metaSuffix}
          </span>
          {/* The one control the `.ui-pop*` family does not name — the design's
              popovers close on the scrim, ours also keeps an explicit ✕ (the
              FTUE + both hosts wire `first-run-tools-close`). */}
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

        <div className="ui-pop__list">
          <WebSearchRow
            enabled={webSearchEnabled}
            onToggle={onToggleWebSearch}
          />
          <div className="ui-pop__div" />
          <PopoverBody
            state={state}
            connected={projection.connected}
            installable={projection.installable}
            activeConnectorIds={activeConnectorIds}
            onToggleConnector={onToggleConnector}
            onConnectCatalog={onConnectCatalog}
          />
        </div>

        <CustomRow onAddCustom={onAddCustom} />
      </div>
    </>
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
      className="ui-pop-row"
      data-off={enabled ? undefined : "true"}
      data-testid="first-run-tools-websearch"
    >
      <span className="ui-pop-row__lg">
        <Icon name="globe" size={13} />
      </span>
      <span className="ui-pop-row__m">
        <span className="ui-pop-row__nm">
          <span className="ui-pop-row__txt">
            {TOOLS_POPOVER_COPY.webSearchLabel}
          </span>
        </span>
        <span className="ui-pop-row__sb">
          {TOOLS_POPOVER_COPY.webSearchHint}
        </span>
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
        className="ui-pop-row__sb"
        style={statusStyle}
        data-testid="first-run-tools-loading"
      >
        Loading connectors…
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div
        role="alert"
        className="ui-pop-row__sb"
        style={statusStyle}
        data-testid="first-run-tools-error"
      >
        Couldn't load connectors.
      </div>
    );
  }
  if (connected.length === 0 && installable.length === 0) {
    return (
      <div
        role="status"
        className="ui-pop-row__sb"
        style={statusStyle}
        data-testid="first-run-tools-empty"
      >
        {TOOLS_POPOVER_COPY.emptyConnectors}
      </div>
    );
  }

  const activeSet = new Set(activeConnectorIds);

  return (
    <>
      {connected.length > 0 ? (
        <section data-testid="first-run-tools-connected">
          <div className="ui-pop__grp">
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
                className="ui-pop-row"
                data-off={active ? undefined : "true"}
                data-testid={`first-run-tools-connected-${row.serverId}`}
              >
                <span className="ui-pop-row__lg">
                  {providerInitials(row.displayName)}
                </span>
                <span className="ui-pop-row__m">
                  <span className="ui-pop-row__nm">
                    <span className="ui-pop-row__txt">{row.displayName}</span>
                  </span>
                  {row.scopesSummary ? (
                    <span className="ui-pop-row__sb">{row.scopesSummary}</span>
                  ) : null}
                </span>
                <ToggleGlyph on={active} />
              </button>
            );
          })}
        </section>
      ) : null}

      {installable.length > 0 ? (
        <section data-testid="first-run-tools-installable">
          <div className="ui-pop__grp">
            {TOOLS_POPOVER_COPY.installableHeader}
          </div>
          <div
            className="ui-pop-row__sb"
            style={groupNoteStyle}
            data-testid="first-run-tools-installable-note"
          >
            {TOOLS_POPOVER_COPY.installableNote}
          </div>
          {installable.map((entry) => (
            <button
              key={entry.slug}
              type="button"
              onClick={() => onConnectCatalog(entry)}
              className="ui-pop-row"
              data-testid={`first-run-tools-connect-${entry.slug}`}
            >
              <span className="ui-pop-row__lg">
                {providerInitials(entry.displayName)}
              </span>
              <span className="ui-pop-row__m">
                <span className="ui-pop-row__nm">
                  <span className="ui-pop-row__txt">{entry.displayName}</span>
                </span>
                {entry.description ? (
                  <span className="ui-pop-row__sb">{entry.description}</span>
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
    </>
  );
}

function CustomRow(props: { readonly onAddCustom: () => void }): ReactNode {
  return (
    <button
      type="button"
      onClick={props.onAddCustom}
      className="ui-pop-row ui-pop-row--pin"
      data-testid="first-run-tools-custom"
    >
      <span className="ui-pop-row__lg">
        <Icon name="plus" size={13} />
      </span>
      <span className="ui-pop-row__m">
        <span className="ui-pop-row__nm">
          <span className="ui-pop-row__txt">
            {TOOLS_POPOVER_COPY.customLabel}
          </span>
        </span>
        <span className="ui-pop-row__sb">{TOOLS_POPOVER_COPY.customHint}</span>
      </span>
    </button>
  );
}

/** The design's `.ctog--sm` — 28x17 track, 11px knob, accent fill when on.
 *  Decorative: the ROW is the `role="switch"` control. */
function ToggleGlyph(props: { readonly on: boolean }): ReactNode {
  return (
    <span
      aria-hidden="true"
      style={{
        ...toggleTrackStyle,
        background: props.on
          ? "var(--color-accent)"
          : "var(--color-surface-elevated)",
        borderColor: props.on ? "var(--color-accent)" : "var(--color-border)",
      }}
    >
      <span
        style={{
          ...toggleKnobStyle,
          background: props.on
            ? "var(--color-accent-contrast)"
            : "var(--color-text-muted)",
          transform: props.on ? "translateX(11px)" : "translateX(0)",
        }}
      />
    </span>
  );
}

/* ── the little that the `.ui-pop*` recipe does not name ─────────────────────
 * Everything structural, typographic and chromatic now comes from the shared
 * recipe. What remains here is (a) the panel's design width + the positioning
 * the host expects, (b) the close ✕ and the Connect/Set-up affordance, which
 * have no counterpart in the design's popovers, (c) two padding overrides on
 * `.ui-pop-row__sb` for non-row text, and (d) the toggle metrics. Tokens only —
 * no raw hex, no hard-coded type sizes.
 */

/** The design's `.pop` width for the tools popover (copilot-composer2 renders
 *  `<Pop width={318}>`). `position: relative` pairs with `.ui-pop`'s z-index 71
 *  so the panel sits above the scrim (70) instead of under it. */
const panelStyle: CSSProperties = {
  width: 318,
  maxWidth: "calc(100vw - 2rem)",
  position: "relative",
};

/** Portaled hosts position the panel themselves; keep `absolute` as before. */
const portaledStyle: CSSProperties = {
  ...panelStyle,
  position: "absolute",
};

const closeButtonStyle: CSSProperties = {
  padding: 0,
  border: "none",
  background: "transparent",
  color: "var(--color-text-subtle)",
  fontSize: "var(--font-size-md)",
  lineHeight: 1,
  cursor: "pointer",
};

/** Loading / error / empty text: the sub-line type, padded to the row rhythm
 *  and allowed to wrap (`.ui-pop-row__sb` truncates single-line by default). */
const statusStyle: CSSProperties = {
  padding: "8px 9px",
  whiteSpace: "normal",
};

/** The installable group note sits directly under its `.ui-pop__grp` heading,
 *  so it takes the heading's horizontal padding and none of its own top. */
const groupNoteStyle: CSSProperties = {
  margin: 0,
  padding: "0 9px 4px",
};

const connectPillStyle: CSSProperties = {
  flex: "none",
  padding: "1px 6px",
  border: "1px solid var(--color-accent-line)",
  borderRadius: "var(--radius-full)",
  color: "var(--color-accent)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  whiteSpace: "nowrap",
};

const toggleTrackStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  flexShrink: 0,
  width: 28,
  height: 17,
  padding: 2,
  borderRadius: "var(--radius-full)",
  border: "1px solid var(--color-border)",
  transition: "background var(--duration-fast) var(--ease-standard)",
};

const toggleKnobStyle: CSSProperties = {
  width: 11,
  height: 11,
  borderRadius: "var(--radius-full)",
  transition: "transform var(--duration-fast) var(--ease-standard)",
};
