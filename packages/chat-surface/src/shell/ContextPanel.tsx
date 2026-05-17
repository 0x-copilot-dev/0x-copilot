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

const PANEL_WIDTH = 224;
const BACKGROUND = "#0E1015";
const BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";

const FILTER_ROWS: readonly string[] = [
  "Filter row 1",
  "Filter row 2",
  "Filter row 3",
];

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

export function ContextPanel(): ReactElement {
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

  const panelStyle: CSSProperties = {
    width: PANEL_WIDTH,
    minWidth: PANEL_WIDTH,
    height: "100%",
    backgroundColor: BACKGROUND,
    borderRight: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    display: "flex",
    flexDirection: "column",
    boxSizing: "border-box",
  };
  const headerStyle: CSSProperties = {
    padding: "16px 16px 12px",
    fontSize: 13,
    fontWeight: 600,
    letterSpacing: 0.2,
    borderBottom: `1px solid ${BORDER}`,
  };
  const listStyle: CSSProperties = {
    listStyle: "none",
    margin: 0,
    padding: "8px 8px",
    display: "flex",
    flexDirection: "column",
    gap: 2,
  };
  const rowStyle: CSSProperties = {
    padding: "8px 12px",
    borderRadius: 6,
    color: TEXT_SECONDARY,
    fontSize: 13,
    cursor: "default",
  };

  return (
    <aside
      aria-label={`${destination.label} filters`}
      style={panelStyle}
      data-component="context-panel"
      data-destination={destination.slug}
    >
      <div style={headerStyle} data-testid="context-panel-header">
        {destination.label}
      </div>
      <ul style={listStyle} data-testid="context-panel-rows">
        {FILTER_ROWS.map((row) => (
          <li key={row} style={rowStyle}>
            {row}
          </li>
        ))}
      </ul>
    </aside>
  );
}
