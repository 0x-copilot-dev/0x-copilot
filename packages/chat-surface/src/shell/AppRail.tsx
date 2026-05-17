import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useRouter } from "../providers/RouterProvider";
import type { ArtifactRoute } from "../routing/router";

import {
  DEFAULT_SHELL_DESTINATION,
  SHELL_DESTINATIONS,
  type ShellDestinationSlug,
} from "./destinations";

const RAIL_WIDTH = 52;
const BACKGROUND = "#0E1015";
const BORDER = "#22252E";
const ICON_INACTIVE = "#3D4250";
const ICON_ACTIVE = "#7B9BFF";
const ACTIVE_TINT = "rgba(123, 155, 255, 0.08)";

function destinationFromRoute(
  route: ArtifactRoute | null,
): ShellDestinationSlug {
  if (route === null) return DEFAULT_SHELL_DESTINATION;
  switch (route.kind) {
    case "chat":
    case "conversation":
      return "chats";
    case "run":
    case "subagent":
    case "tool-result":
      return "agents";
    case "mcp":
    case "mcp-tool":
      return "connectors";
    case "skill":
      return "tools";
    case "workspace":
      return "team";
    default:
      return DEFAULT_SHELL_DESTINATION;
  }
}

function routeForDestination(slug: ShellDestinationSlug): ArtifactRoute | null {
  if (slug === "chats") return { kind: "chat", conversationId: "" };
  return null;
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

export function AppRail(): ReactElement {
  const router = useRouter<ArtifactRoute>();
  const [route, setRoute] = useState<ArtifactRoute | null>(() => {
    try {
      return router.current();
    } catch {
      return null;
    }
  });

  useEffect(() => {
    return router.subscribe((next) => setRoute(next));
  }, [router]);

  const active = destinationFromRoute(route);

  const railStyle: CSSProperties = {
    width: RAIL_WIDTH,
    minWidth: RAIL_WIDTH,
    height: "100%",
    backgroundColor: BACKGROUND,
    borderRight: `1px solid ${BORDER}`,
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
        const isActive = d.slug === active;
        const target = routeForDestination(d.slug);
        const handleClick = (): void => {
          if (target !== null) router.navigate(target);
        };
        const buttonStyle: CSSProperties = {
          width: 36,
          height: 36,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: isActive ? ACTIVE_TINT : "transparent",
          border: "none",
          borderRadius: 8,
          color: isActive ? ICON_ACTIVE : ICON_INACTIVE,
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
            onClick={handleClick}
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
