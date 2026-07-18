import { type CSSProperties, type ReactElement } from "react";

import { SHELL_DESTINATIONS, type ShellDestinationSlug } from "./destinations";

const TOPBAR_HEIGHT = 44;

export interface TopbarProps {
  /** The destination the host considers active. Used to label the
   *  first breadcrumb. */
  readonly activeDestination: ShellDestinationSlug;
  /** Optional sub-crumb (e.g. conversation id, run id, server id). */
  readonly leaf?: string | null;
}

export function Topbar({ activeDestination, leaf }: TopbarProps): ReactElement {
  const destination =
    SHELL_DESTINATIONS.find((d) => d.slug === activeDestination) ??
    SHELL_DESTINATIONS[0];

  const barStyle: CSSProperties = {
    height: TOPBAR_HEIGHT,
    minHeight: TOPBAR_HEIGHT,
    backgroundColor: "var(--color-bg)",
    borderBottom: "1px solid var(--color-border)",
    color: "var(--color-text)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 16px",
    boxSizing: "border-box",
  };
  const breadcrumbStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: "var(--font-size-sm)",
  };
  const crumbStyle: CSSProperties = { color: "var(--color-text)" };
  const separatorStyle: CSSProperties = { color: "var(--color-text-subtle)" };
  const leafStyle: CSSProperties = { color: "var(--color-text-muted)" };
  const toggleStyle: CSSProperties = {
    background: "transparent",
    border: "1px solid var(--color-border)",
    color: "var(--color-text)",
    fontSize: "var(--font-size-xs)",
    padding: "4px 10px",
    borderRadius: 6,
    cursor: "pointer",
  };

  return (
    <header style={barStyle} data-component="topbar">
      <nav
        aria-label="Breadcrumb"
        style={breadcrumbStyle}
        data-testid="topbar-breadcrumb"
      >
        <span style={crumbStyle}>{destination.label}</span>
        <span aria-hidden="true" style={separatorStyle}>
          /
        </span>
        <span style={leafStyle} data-testid="topbar-breadcrumb-leaf">
          {leaf !== undefined && leaf !== null && leaf !== "" ? leaf : "—"}
        </span>
      </nav>
      <button
        type="button"
        aria-label="Toggle mode"
        data-testid="topbar-mode-toggle"
        style={toggleStyle}
      >
        Studio
      </button>
    </header>
  );
}

export { TOPBAR_HEIGHT };
