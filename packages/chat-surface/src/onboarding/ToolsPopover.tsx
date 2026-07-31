// ToolsPopover — the connector-aware run-scoped Tools content (PRD-P4).
//
// This content replaces the flat `composer/ToolPicker` toggle list. Sections,
// top-to-bottom, byte-verbatim vs SPEC §"Tools popover":
//   • Header      — "Tools" + meta `{n} on · none required` + close
//   • Web search  — built-in toggle, default on (host owns the default;
//                   the component only reflects `webSearchEnabled`)
//   • Connected   — workspace-installed + authenticated connectors, each with
//                   a per-run PAUSE toggle (no conversation exists yet, so the
//                   opt-outs are held by the surface via `pausedConnectorIds`)
//   • Installable — curated 1-click rows; group note
//                   `1-click connect · you approve first use`.
//                   `requiresPreRegisteredClient` → the host routes the click
//                   to the custom-config form (a keyless install would 422).
//   • Custom MCP  — "Custom MCP server" → host opens the paste-a-config form.
//
// Data comes from the host-injected `FirstRunConnectorsPort` via
// `useConnectorPopoverData`, called by whoever OWNS the panel (the standalone
// dialog below, or `ComposerToolsTrigger`) and passed in as `data` — so the pill
// badge and these rows count the same projection. The package has no `document`;
// the legacy standalone dialog takes an opt-in host-owned `portalTarget`, while
// the shipping composer pill uses design-system `Menu`.
//
// ── Connected means ON (the toggle is an opt-OUT) ───────────────────────────
// A connected row renders ON unless its id is in `pausedConnectorIds`, which
// starts EMPTY. Before this, the toggle read an `activeConnectorIds` set that
// every host initialised to `[]` and nothing ever seeded from durable state, so
// a connector the Tools destination showed as "Connected · read" appeared here
// disabled — and stayed disabled until hand-toggled, in that one mount, for a
// connector the runtime could already call. (Omission from `connector_scopes` is
// not a pause: `McpPermissionPolicy.is_server_card_authorized` gates on
// `paused_connectors`, so the OFF state was decorative.) Defaulting to ON is
// what makes a just-authorized connector show up live, and it makes the pause
// the only claim this control makes — one the host now actually sends as
// `request_context.paused_connectors`.
//
// ── Design parity, composer punch-list rows 43 + 46 ─────────────────────────
// This surface used to be styled with 100% inline `CSSProperties` objects — a
// third private idiom next to the model popover's `.ui-pop` and the composer
// plus-menu's `.aui-plus-menu`. It now renders the SHARED `.ui-pop*` recipe
// from `@0x-copilot/design-system` (the design's `.pop` family in
// `tools/design-parity/design-kit/copilot-v3.css`), aligned to the Model
// popover's 300px width, mapped 1:1:
//
//   .ui-pop / .ui-pop__h / .ui-pop__h-meta   panel + header + `{n} on` meta
//   .ui-pop__list                            the one scroll region (264px cap)
//   .ui-pop__grp                             "Connected" / "Add a connector"
//   .ui-pop-row + __lg/__m/__nm/__txt/__sb   every row (24px badge · name; the
//                                            `__sb` sub-line only on Web search)
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
//
// ── Single-line rows, with ONE exception ────────────────────────────────────
// Every CONNECTOR row is one line — name only. The sub-lines this surface used
// to render on them (a connector's scopes summary, a catalog entry's marketing
// description, "paste a JSON config") are gone: catalog descriptions are full
// sentences, so a 318px panel of two-line rows read as clutter, and the sub-line
// was ALSO the panel's layout bug. `.ui-pop-row__sb` is rendered as a `<span>`
// inside the block `.ui-pop-row__m`, and `overflow: hidden` / `text-overflow:
// ellipsis` do not apply to an inline box — so `nowrap` grew the line past the
// panel instead of clipping it, the list scrolled sideways (badge column scrolled
// out of frame), and the overflowing sentence slid underneath the unfilled
// `Connect` pill. Both halves are fixed in the recipe itself
// (`.ui-pop-row__sb { display: block }`, `.ui-pop__list { overflow-x: hidden }`)
// so no other popover can reproduce it; dropping the rows' sub-lines is the
// product call on top.
//
// The EXCEPTION is Web search's "built-in" — kept deliberately. It is a two-word
// provenance label, not prose: it is what tells you this row is the runtime's own
// tool rather than one of your connectors, and no `Connect`/`Set up` pill says so
// for it. The other surviving second line is the "Add a connector" GROUP note,
// which discloses the approval semantics rather than describing a row.

import {
  useEffect,
  useRef,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Spinner } from "@0x-copilot/design-system";

import { Icon } from "../icons/Icon";
import { providerInitials } from "../icons/providerMarks";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import {
  firstRunActiveToolCount,
  isFirstRunConnectorActive,
  type FirstRunConnectedConnector,
  type FirstRunInstallableConnector,
} from "./projectFirstRunConnectors";
import {
  useConnectorPopoverData,
  type ConnectorPopoverData,
} from "./useConnectorPopoverData";

export const TOOLS_POPOVER_COPY = {
  title: "Tools",
  metaSuffix: "none required",
  webSearchLabel: "Web search",
  webSearchHint: "built-in",
  connectedHeader: "Connected",
  installableHeader: "Add a connector",
  installableNote: "1-click connect · you approve first use",
  connectLabel: "Connect",
  connectingLabel: "Connecting…",
  cancelLabel: "Cancel",
  setupLabel: "Set up",
  customLabel: "Custom MCP server",
  emptyConnectors: "No connectors yet",
} as const;

export interface ToolsPopoverProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Host-injected MCP surface (servers + catalog + install + auth). */
  readonly port: FirstRunConnectorsPort;
  /** Bump to refetch the connector list (e.g. a connect just completed). */
  readonly reloadToken?: number;
  /** Built-in web search; default TRUE is owned by the surface. */
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  /**
   * Connector ids the user paused FOR THIS RUN (component state — no
   * conversation yet). Empty means every connected connector is live.
   */
  readonly pausedConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  /**
   * 1-click connect of a catalog entry. The host mirrors
   * `ChatScreen.onMcpInstallCatalog`: `requiresPreRegisteredClient` → open the
   * custom-config form; else installFromCatalog → beginAuth.
   */
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  /**
   * Catalog slug whose OAuth is in flight, or null. Its row shows a spinner and
   * a Cancel; every other connect row disables, because main holds ONE pending
   * connect and a second click would abort the first.
   */
  readonly connectingSlug?: string | null;
  /**
   * Abort the in-flight connect. Absent ⇒ the host cannot cancel (web, whose
   * redirect unloads the document), and no Cancel affordance renders — the
   * capability is expressed rather than assumed.
   */
  readonly onCancelConnect?: () => void;
  /** Open the host's custom-MCP form. */
  readonly onAddCustom: () => void;
  /** Host-owned portal root — the package has no `document`. */
  readonly portalTarget?: HTMLElement;
}

/**
 * The run-scoped Tools body without its own trigger, overlay, or scrim.
 * `ComposerToolsTrigger` mounts this in its anchored composer-pill menu; the
 * standalone `ToolsPopover` below remains for callers that need a dialog.
 */
export interface ToolsPopoverContentProps {
  /** The owner's `useConnectorPopoverData` result — one fetch per panel. */
  readonly data: ConnectorPopoverData;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly pausedConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  /**
   * Catalog slug whose OAuth is in flight, or null. Its row shows a spinner and
   * a Cancel; every other connect row disables, because main holds ONE pending
   * connect and a second click would abort the first.
   */
  readonly connectingSlug?: string | null;
  /**
   * Abort the in-flight connect. Absent ⇒ the host cannot cancel (web, whose
   * redirect unloads the document), and no Cancel affordance renders — the
   * capability is expressed rather than assumed.
   */
  readonly onCancelConnect?: () => void;
  readonly onAddCustom: () => void;
  /** Return to a parent menu when this content is used as a drill-down. */
  readonly onBack?: () => void;
  /** Close a standalone dialog. Mutually exclusive with `onBack`. */
  readonly onClose?: () => void;
}

export function ToolsPopover(props: ToolsPopoverProps): ReactNode {
  const { open, onClose, portalTarget } = props;
  const panelRef = useRef<HTMLDivElement | null>(null);
  // The panel owns the fetch so the content stays pure. `enabled: open` keeps
  // the pre-existing timing — a closed dialog issues no request.
  const data = useConnectorPopoverData(props.port, {
    reloadToken: props.reloadToken,
    enabled: open,
  });

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
        <ToolsPopoverContent
          data={data}
          webSearchEnabled={props.webSearchEnabled}
          onToggleWebSearch={props.onToggleWebSearch}
          pausedConnectorIds={props.pausedConnectorIds}
          onToggleConnector={props.onToggleConnector}
          onConnectCatalog={props.onConnectCatalog}
          connectingSlug={props.connectingSlug}
          onCancelConnect={props.onCancelConnect}
          onAddCustom={props.onAddCustom}
          onClose={onClose}
        />
      </div>
    </>
  );

  if (portalTarget !== undefined) {
    return createPortal(panel, portalTarget);
  }
  return panel;
}

export function ToolsPopoverContent(
  props: ToolsPopoverContentProps,
): ReactNode {
  const {
    data,
    webSearchEnabled,
    onToggleWebSearch,
    pausedConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    connectingSlug = null,
    onCancelConnect,
    onAddCustom,
    onBack,
    onClose,
  } = props;
  const activeCount = firstRunActiveToolCount(
    webSearchEnabled,
    data.connected,
    pausedConnectorIds,
  );

  return (
    <>
      <div className="ui-pop__h">
        {TOOLS_POPOVER_COPY.title}
        <span className="ui-pop__h-meta" data-testid="first-run-tools-meta">
          {activeCount} on · {TOOLS_POPOVER_COPY.metaSuffix}
        </span>
        {onBack !== undefined ? (
          <button
            type="button"
            onClick={onBack}
            aria-label="Back to attachment and tools menu"
            style={closeButtonStyle}
            data-testid="first-run-tools-back"
          >
            ←
          </button>
        ) : onClose !== undefined ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close tools"
            style={closeButtonStyle}
            data-testid="first-run-tools-close"
          >
            ×
          </button>
        ) : null}
      </div>

      <div className="ui-pop__list">
        <WebSearchRow enabled={webSearchEnabled} onToggle={onToggleWebSearch} />
        <div className="ui-pop__div" />
        <PopoverBody
          state={data.state}
          connected={data.connected}
          installable={data.installable}
          pausedConnectorIds={pausedConnectorIds}
          onToggleConnector={onToggleConnector}
          onConnectCatalog={onConnectCatalog}
          connectingSlug={connectingSlug}
          onCancelConnect={onCancelConnect}
        />
      </div>

      <CustomRow onAddCustom={onAddCustom} />
    </>
  );
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
        {/* The ONE surviving row sub-line — see the header note. */}
        <span className="ui-pop-row__sb">
          {TOOLS_POPOVER_COPY.webSearchHint}
        </span>
      </span>
      <ToggleGlyph on={enabled} />
    </button>
  );
}

interface BodyProps {
  readonly state: ConnectorPopoverData["state"];
  readonly connected: readonly FirstRunConnectedConnector[];
  readonly installable: readonly FirstRunInstallableConnector[];
  readonly pausedConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly connectingSlug: string | null;
  readonly onCancelConnect?: () => void;
}

function PopoverBody(props: BodyProps): ReactNode {
  const {
    state,
    connected,
    installable,
    pausedConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    connectingSlug,
    onCancelConnect,
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

  return (
    <>
      {connected.length > 0 ? (
        <section data-testid="first-run-tools-connected">
          <div className="ui-pop__grp">
            {TOOLS_POPOVER_COPY.connectedHeader}
          </div>
          {connected.map((row) => {
            const active = isFirstRunConnectorActive(row, pausedConnectorIds);
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
          {installable.map((entry) => {
            const connecting = connectingSlug === entry.slug;
            // Main holds ONE pending connect, so a second click would abort the
            // first. The UI says so rather than letting the user discover it.
            const blocked = connectingSlug !== null && !connecting;
            const label = entry.requiresPreRegisteredClient
              ? TOOLS_POPOVER_COPY.setupLabel
              : TOOLS_POPOVER_COPY.connectLabel;

            // A connecting row is NOT a button. It holds one (Cancel), and a
            // button inside a button is invalid HTML that browsers reparent —
            // which is why the row changes element rather than gaining a
            // nested control. `.ui-pop-row` styles both; only the interactive
            // rules are scoped to `button.ui-pop-row`.
            if (connecting) {
              return (
                <div
                  key={entry.slug}
                  className="ui-pop-row"
                  role="status"
                  data-testid={`first-run-tools-connecting-${entry.slug}`}
                >
                  <span className="ui-pop-row__lg">
                    {providerInitials(entry.displayName)}
                  </span>
                  <span className="ui-pop-row__m">
                    <span className="ui-pop-row__nm">
                      <span className="ui-pop-row__txt">
                        {entry.displayName}
                      </span>
                    </span>
                  </span>
                  <span style={connectingWrapStyle}>
                    {/* The row owns `role="status"`; the ring is decorative. */}
                    <Spinner />
                    <span style={connectingTextStyle}>
                      {TOOLS_POPOVER_COPY.connectingLabel}
                    </span>
                    {onCancelConnect === undefined ? null : (
                      <button
                        type="button"
                        onClick={onCancelConnect}
                        style={cancelPillStyle}
                        data-testid={`first-run-tools-cancel-${entry.slug}`}
                      >
                        {TOOLS_POPOVER_COPY.cancelLabel}
                      </button>
                    )}
                  </span>
                </div>
              );
            }

            return (
              <button
                key={entry.slug}
                type="button"
                onClick={() => onConnectCatalog(entry)}
                disabled={blocked}
                className="ui-pop-row"
                data-off={blocked ? "true" : undefined}
                data-testid={`first-run-tools-connect-${entry.slug}`}
              >
                <span className="ui-pop-row__lg">
                  {providerInitials(entry.displayName)}
                </span>
                <span className="ui-pop-row__m">
                  <span className="ui-pop-row__nm">
                    <span className="ui-pop-row__txt">{entry.displayName}</span>
                  </span>
                </span>
                <span style={connectPillStyle} aria-hidden="true">
                  {label}
                </span>
              </button>
            );
          })}
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

/** Match the Model popover's 300px frame. `position: relative` pairs with
 * `.ui-pop`'s z-index 71 so the panel sits above the scrim (70) instead of
 * under it. */
const panelStyle: CSSProperties = {
  width: 300,
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

/** Tail of a connecting row: spinner · "Connecting…" · Cancel. */
const connectingWrapStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  flex: "none",
  // Matches `.ui-pop-row`'s own 9px rhythm rather than inventing a spacing.
  gap: 6,
};

/** Same mono metadata register as the Connect pill, in the quiet tone. */
const connectingTextStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  whiteSpace: "nowrap",
};

/**
 * Cancel keeps the Connect pill's exact geometry so the control reads as one
 * thing in three states rather than three different controls. Neutral rather
 * than accent: the accent ring belongs to the action the user is being offered,
 * and here that action is "stop".
 */
const cancelPillStyle: CSSProperties = {
  flex: "none",
  padding: "1px 6px",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-full)",
  background: "transparent",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "inherit",
  whiteSpace: "nowrap",
  cursor: "pointer",
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
