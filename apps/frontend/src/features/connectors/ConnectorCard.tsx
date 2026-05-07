// PR 4.4.6 — compact grid card for the redesigned Connectors page.
//
// Reads brand metadata directly from ``McpServer`` (server-supplied via
// the catalog endpoint at install time). No frontend metadata duplication.
// The toggle is the only inline action; advanced flows (re-auth, skip,
// remove) live in the Manage MCP servers modal.
//
// Per PR 4.4.6 §1.2 goal #2, this card only renders for installed
// servers — the parent ``ConnectorsSettings`` filters its input to
// ``isAuthenticated(server.auth_state)`` so unauthenticated rows never
// reach this component.

import { AppIcon, Switch } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import { type ReactElement, useState } from "react";
import { authStateDisplay } from "./authStateDisplay";
import type { ConnectorState } from "./useConnectors";

interface ConnectorCardProps {
  server: McpServer;
  connectors: ConnectorState;
}

export function ConnectorCard({
  server,
  connectors,
}: ConnectorCardProps): ReactElement {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const display = authStateDisplay(server.auth_state);

  // Sub-text priority: ``scopes_summary`` (server-supplied catalog
  // metadata) → state hint when scopes are absent (e.g. failed auth) →
  // server URL as last-resort identifier.
  const subtext = subtextFor(server, display.label);

  async function handleToggle(checked: boolean): Promise<void> {
    if (pending) {
      return;
    }
    try {
      setPending(true);
      setError(null);
      await connectors.setEnabled(server.server_id, checked);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update.");
    } finally {
      setPending(false);
    }
  }

  return (
    <article
      className="connector-card"
      data-state={server.enabled ? "active" : "inactive"}
    >
      <div className="connector-card__head">
        <AppIcon
          name={server.name}
          color={server.brand_color ?? undefined}
          size="lg"
        />
        <div className="connector-card__body">
          <h4 className="connector-card__name">{server.display_name}</h4>
          <p className="connector-card__sub">{subtext}</p>
        </div>
        <Switch
          label={server.enabled ? "Enabled" : "Disabled"}
          checked={server.enabled}
          disabled={pending}
          onChange={(event) => void handleToggle(event.target.checked)}
          aria-label={`Toggle ${server.display_name}`}
          className="connector-card__switch"
        />
      </div>
      {error ? (
        <p className="app-error connector-card__error">{error}</p>
      ) : null}
    </article>
  );
}

function subtextFor(server: McpServer, fallback: string): string {
  if (server.auth_state === "auth_failed") {
    return "Sign-in expired — re-authenticate";
  }
  if (server.auth_state === "auth_pending") {
    return "Connecting…";
  }
  if (server.scopes_summary) {
    return server.scopes_summary;
  }
  if (server.description) {
    return server.description;
  }
  return fallback;
}
