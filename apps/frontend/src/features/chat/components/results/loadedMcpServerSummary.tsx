import { Badge } from "@0x-copilot/design-system";
import type { ReactNode } from "react";
import {
  asRecord,
  displayToolResult,
  stringValue,
} from "../../utils/jsonUtils";
import { humanizeIdentifier } from "../../utils/toolLabels";

export function loadedMcpServerSummary(value: unknown): ReactNode | null {
  const payload = displayToolResult(value);
  const loadedServer = asRecord(asRecord(payload).loaded_server);
  if (Object.keys(loadedServer).length === 0) {
    return null;
  }
  const serverCard = asRecord(loadedServer.server_card);
  const tools = Array.isArray(loadedServer.tools) ? loadedServer.tools : [];
  const displayName =
    stringValue(serverCard.display_name) ??
    stringValue(serverCard.name) ??
    "MCP server";
  const health = stringValue(serverCard.health);
  const authState = stringValue(serverCard.auth_state);
  const visibleTools = tools
    .map((tool) => stringValue(asRecord(tool).name))
    .filter((tool): tool is string => tool !== null)
    .slice(0, 4);
  return (
    <div className="aui-mcp-result-preview">
      <p>
        Loaded {tools.length} tools from {displayName}.
      </p>
      {health || authState ? (
        <div className="aui-mcp-result-preview__badges">
          {health ? (
            <Badge tone="neutral">{humanizeIdentifier(health)}</Badge>
          ) : null}
          {authState ? (
            <Badge tone="neutral">{humanizeIdentifier(authState)}</Badge>
          ) : null}
        </div>
      ) : null}
      {visibleTools.length > 0 ? (
        <p>
          Available tools include{" "}
          {visibleTools.map(humanizeIdentifier).join(", ")}.
        </p>
      ) : null}
    </div>
  );
}
