import { type CSSProperties, type ReactElement } from "react";

import { SHELL_DESTINATIONS, type ShellDestinationSlug } from "./destinations";

// Geometry constants — kept here, not stretched into a token, because they
// describe THIS component's box (not a colour or a font). One source of
// truth: the same constant feeds the rail's own style and is exported so
// ChatShell's grid template can mirror it without redefining the literal.
const RAIL_WIDTH = 52;

export interface AppRailProps {
  /**
   * The destination the host considers active. The rail is controlled —
   * it never reads from a router itself. The host owns route↔destination
   * mapping (see `apps/frontend/src/app/App.tsx`); the rail just renders
   * a button per destination and reports clicks back.
   */
  readonly activeDestination: ShellDestinationSlug;
  /**
   * Click handler — the host translates the slug into whatever route
   * shape it owns (chat / settings / share / admin-…) and decides what
   * to do with the navigation. Click on the already-active destination
   * is delivered too; the host can ignore or treat as a deep-link reset.
   */
  readonly onNavigate: (slug: ShellDestinationSlug) => void;
}

function Glyph({ slug }: { slug: ShellDestinationSlug }): ReactElement {
  const common = {
    "aria-hidden": true,
    focusable: false,
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    viewBox: "0 0 24 24",
    width: 18,
    height: 18,
  };
  switch (slug) {
    case "home":
      return (
        <svg {...common}>
          <path d="M3 11l9-8 9 8" />
          <path d="M5 10v10h14V10" />
        </svg>
      );
    case "chats":
      return (
        <svg {...common}>
          <path d="M4 5h16v11H8l-4 4z" />
        </svg>
      );
    case "agents":
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
        </svg>
      );
    case "library":
      return (
        <svg {...common}>
          <path d="M4 5h6v14H4z" />
          <path d="M14 5h6v14h-6z" />
          <path d="M7 8h0M7 11h0" />
        </svg>
      );
    case "inbox":
      return (
        <svg {...common}>
          <path d="M3 13h5l2 3h4l2-3h5" />
          <path d="M3 13l3-8h12l3 8v6H3z" />
        </svg>
      );
    case "tools":
      return (
        <svg {...common}>
          <path d="M14 6l4 4-10 10-4-4z" />
          <path d="M14 6l3-3 4 4-3 3" />
        </svg>
      );
    case "projects":
      return (
        <svg {...common}>
          <path d="M3 6h7l2 2h9v11H3z" />
        </svg>
      );
    case "todos":
      return (
        <svg {...common}>
          <path d="M4 6h16M4 12h16M4 18h10" />
          <path d="M19 17l2 2 3-3" transform="translate(-4 0)" />
        </svg>
      );
    case "connectors":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="2.5" />
          <circle cx="18" cy="6" r="2.5" />
          <circle cx="18" cy="18" r="2.5" />
          <path d="M8.5 12L16 7M8.5 12L16 17" />
        </svg>
      );
    case "team":
      return (
        <svg {...common}>
          <circle cx="9" cy="9" r="3" />
          <circle cx="17" cy="10" r="2.5" />
          <path d="M3 20c0-3 3-5 6-5s6 2 6 5" />
          <path d="M14 20c0-2.2 1.5-4 3-4s3 1.8 3 4" />
        </svg>
      );
    case "memory":
      return (
        <svg {...common}>
          <path d="M9 5a4 4 0 0 0-4 4v6a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4V9a4 4 0 0 0-4-4z" />
          <path d="M9 9h6M9 12h6M9 15h4" />
        </svg>
      );
  }
}

export function AppRail({
  activeDestination,
  onNavigate,
}: AppRailProps): ReactElement {
  const railStyle: CSSProperties = {
    width: RAIL_WIDTH,
    minWidth: RAIL_WIDTH,
    height: "100%",
    backgroundColor: "var(--color-bg)",
    borderRight: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    paddingTop: 12,
    paddingBottom: 12,
    gap: 4,
    boxSizing: "border-box",
  };

  return (
    <nav
      aria-label="Atlas destinations"
      style={railStyle}
      data-component="app-rail"
    >
      {SHELL_DESTINATIONS.map((d) => {
        const isActive = d.slug === activeDestination;
        const buttonStyle: CSSProperties = {
          width: 36,
          height: 36,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: isActive
            ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
            : "transparent",
          border: "none",
          borderRadius: 8,
          color: isActive ? "var(--color-accent)" : "var(--color-text-subtle)",
          cursor: "pointer",
          padding: 0,
        };
        return (
          <button
            key={d.slug}
            type="button"
            aria-label={d.label}
            aria-current={isActive ? "page" : undefined}
            data-destination={d.slug}
            data-state={isActive ? "active" : "inactive"}
            onClick={() => onNavigate(d.slug)}
            style={buttonStyle}
            title={d.label}
          >
            <Glyph slug={d.slug} />
          </button>
        );
      })}
    </nav>
  );
}

export { RAIL_WIDTH as APP_RAIL_WIDTH };
