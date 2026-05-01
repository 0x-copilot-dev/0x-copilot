import type { McpAuthState, McpServer } from "@enterprise-search/api-types";
import { Badge, Button, Card } from "@enterprise-search/design-system";
import type { ReactElement } from "react";

export function ConnectorSuggestionCard({
  servers,
  onConnect,
  onSkip,
  onNone,
}: {
  servers: McpServer[];
  onConnect: (serverId: string) => void;
  onSkip: (serverId: string) => void;
  onNone: () => void;
}): ReactElement {
  return (
    <Card className="connector-suggestion-card">
      <span className="app-eyebrow">Connectors that could help</span>
      <div className="connector-suggestion-card__list">
        {servers.map((server) => (
          <div
            className="connector-suggestion-card__row"
            key={server.server_id}
          >
            <div>
              <strong>{server.display_name}</strong>
              <p>{connectorHelpText(server)}</p>
            </div>
            <Badge tone={authTone(server.auth_state)}>
              {server.auth_state.replaceAll("_", " ")}
            </Badge>
            <Button
              type="button"
              size="sm"
              title={`Connect ${server.display_name}`}
              onClick={() => onConnect(server.server_id)}
            >
              Connect
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              title={`Skip ${server.display_name}`}
              onClick={() => onSkip(server.server_id)}
            >
              Skip
            </Button>
          </div>
        ))}
      </div>
      <Button
        type="button"
        variant="secondary"
        title="Dismiss connector suggestions"
        onClick={onNone}
      >
        None of these
      </Button>
    </Card>
  );
}

export function authTone(
  authState: McpAuthState,
): "neutral" | "success" | "warning" | "danger" | "accent" {
  if (authState === "authenticated") {
    return "success";
  }
  if (authState === "auth_failed" || authState === "auth_unsupported") {
    return "danger";
  }
  if (authState === "auth_pending") {
    return "warning";
  }
  if (authState === "auth_skipped") {
    return "accent";
  }
  return "neutral";
}

function connectorHelpText(server: McpServer): string {
  if (!server.enabled) {
    return "Disabled in settings. Enable it before the agent can use it.";
  }
  if (server.auth_state === "authenticated") {
    return "Ready for the agent to use in chat.";
  }
  if (server.auth_state === "auth_skipped") {
    return "Skipped before. You can connect now if this task needs it.";
  }
  return "Authenticate so the agent can safely reference this service.";
}
