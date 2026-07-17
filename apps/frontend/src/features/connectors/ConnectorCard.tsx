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

import { AppIcon, Switch } from "@0x-copilot/design-system";
import type { McpServer } from "@0x-copilot/api-types";
import { type ReactElement, useState } from "react";
import { authStateDisplay } from "./authStateDisplay";
import type { ConnectorState } from "./useConnectors";
import { errorMessage } from "../../utils/errors";

interface ConnectorCardProps {
  server: McpServer;
  connectors: ConnectorState;
}

type Pending = null | "toggle" | "auth" | "remove";

export function ConnectorCard({
  server,
  connectors,
}: ConnectorCardProps): ReactElement {
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  const display = authStateDisplay(server.auth_state);
  const busy = pending !== null;

  // Sub-text priority: ``scopes_summary`` (server-supplied catalog
  // metadata) → state hint when scopes are absent (e.g. failed auth) →
  // server URL as last-resort identifier.
  const subtext = subtextFor(server, display.label);

  // Show inline recovery affordances for the "Needs attention" cases —
  // the parent group's hint reads "Re-authenticate to bring these
  // online", so the row should actually expose those actions instead
  // of forcing the user into the Manage MCP servers modal.
  const needsAttention =
    server.auth_state === "auth_pending" || server.auth_state === "auth_failed";

  async function run(
    kind: Exclude<Pending, null>,
    fn: () => Promise<void>,
  ): Promise<void> {
    if (busy) {
      return;
    }
    try {
      setPending(kind);
      setError(null);
      await fn();
    } catch (err) {
      setError(errorMessage(err, "Action failed."));
    } finally {
      setPending(null);
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
        {needsAttention ? (
          <div className="connector-card__actions">
            <button
              type="button"
              className="connector-card__action"
              disabled={busy}
              onClick={() =>
                void run("auth", () =>
                  connectors.authenticate(server.server_id),
                )
              }
            >
              {pending === "auth" ? "Starting…" : "Re-authenticate"}
            </button>
            <button
              type="button"
              className="connector-card__action connector-card__action--danger"
              disabled={busy}
              onClick={() =>
                void run("remove", () =>
                  connectors.removeServer(server.server_id),
                )
              }
            >
              {pending === "remove" ? "Removing…" : "Remove"}
            </button>
          </div>
        ) : (
          <Switch
            label={server.enabled ? "Enabled" : "Disabled"}
            checked={server.enabled}
            disabled={busy}
            onChange={(event) =>
              void run("toggle", () =>
                connectors.setEnabled(server.server_id, event.target.checked),
              )
            }
            aria-label={`Toggle ${server.display_name}`}
            className="connector-card__switch"
          />
        )}
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
