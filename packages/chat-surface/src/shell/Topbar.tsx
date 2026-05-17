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

const TOPBAR_HEIGHT = 44;
const BACKGROUND = "#0E1015";
const BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";

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

function leafFromRoute(route: ArtifactRoute | null): string | null {
  if (route === null) return null;
  switch (route.kind) {
    case "chat":
    case "conversation":
      return route.conversationId !== "" ? route.conversationId : null;
    case "run":
      return route.runId;
    case "subagent":
      return route.subagentId;
    case "tool-result":
      return route.stepId;
    case "mcp":
      return route.serverId;
    case "mcp-tool":
      return route.toolName;
    case "skill":
      return route.skillId;
    case "workspace":
      return route.workspaceId;
    default:
      return null;
  }
}

export function Topbar(): ReactElement {
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

  const slug = destinationFromRoute(route);
  const destination =
    SHELL_DESTINATIONS.find((d) => d.slug === slug) ?? SHELL_DESTINATIONS[0];
  const leaf = leafFromRoute(route);

  const barStyle: CSSProperties = {
    height: TOPBAR_HEIGHT,
    minHeight: TOPBAR_HEIGHT,
    backgroundColor: BACKGROUND,
    borderBottom: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
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
    fontSize: 13,
  };
  const crumbStyle: CSSProperties = { color: TEXT_PRIMARY };
  const separatorStyle: CSSProperties = { color: TEXT_SECONDARY };
  const leafStyle: CSSProperties = { color: TEXT_SECONDARY };
  const toggleStyle: CSSProperties = {
    background: "transparent",
    border: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    fontSize: 12,
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
          {leaf ?? "—"}
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
