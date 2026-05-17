// Connected-tab row for the Manage MCP servers modal.
//
// PR 4.4.7 — visual parity with the Catalog tab. Same `.mcp-card`
// shell, same square `AppIcon`, single horizontal row with
// `[icon] [name + Connected pill + scope summary] [toggle] [Re-auth |
// Remove]`. The previous layout used a verbose vertical card with an
// oversized danger button; that didn't match the Catalog cards
// alongside it and made the Connected tab look like a different
// product surface.
//
// Owns its own pending + error state so failed per-row actions
// (re-auth, remove, toggle) surface inline rather than in a shared
// slot, and double-clicks on Re-authenticate don't fire two OAuth
// redirects.

import { AppIcon, Badge, Switch } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import { type ReactElement, useState } from "react";
import { ConfirmDialog } from "./ConfirmDialog";
import { authStateDisplay, isAuthenticated } from "./authStateDisplay";
import type { ConnectorState } from "./useConnectors";
import { errorMessage } from "../../utils/errors";

interface ConnectorRowProps {
  server: McpServer;
  connectors: ConnectorState;
}

type Pending = null | "toggle" | "auth" | "remove";

export function ConnectorRow({
  server,
  connectors,
}: ConnectorRowProps): ReactElement {
  const [pending, setPending] = useState<Pending>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | "remove">(null);
  const authed = isAuthenticated(server.auth_state);
  const busy = pending !== null;
  const display = authStateDisplay(server.auth_state);

  // For non-authed rows the catalog scope summary is not what's
  // load-bearing — the user needs to know *why* the row isn't usable
  // (e.g. "OAuth flow started — finish in the popup window"). Fall back
  // to the catalog/URL line only when there's nothing actionable to say.
  const subtitle = authed
    ? (server.scopes_summary ?? server.description ?? server.url)
    : display.hint;

  async function run(
    kind: Exclude<Pending, null>,
    fn: () => Promise<void>,
  ): Promise<void> {
    if (busy) {
      return;
    }
    try {
      setPending(kind);
      setRowError(null);
      await fn();
    } catch (err) {
      setRowError(errorMessage(err, "Action failed."));
    } finally {
      setPending(null);
    }
  }

  return (
    <article
      className="mcp-card"
      data-status={authed ? "connected" : "needs-auth"}
      aria-label={`${server.display_name} connected card`}
    >
      <AppIcon
        name={server.name}
        logoUrl={server.logo_url ?? null}
        size="lg"
        className="mcp-card__icon"
      />
      <div className="mcp-card__main">
        <div className="mcp-card__title-row">
          <h4 className="mcp-card__title">{server.display_name}</h4>
          <Badge tone={display.tone} className="mcp-card__pill">
            {display.label}
          </Badge>
          {authed && !server.enabled ? (
            <span className="mcp-card__setup-note" title="Disabled by you">
              · Disabled
            </span>
          ) : null}
        </div>
        <p className="mcp-card__desc">{subtitle}</p>
        {rowError ? (
          <p className="app-error mcp-card__error">{rowError}</p>
        ) : null}
      </div>
      <div className="mcp-card__actions">
        <Switch
          label=""
          checked={server.enabled}
          disabled={busy || !authed}
          onChange={(event) =>
            void run("toggle", () =>
              connectors.setEnabled(server.server_id, event.target.checked),
            )
          }
          aria-label={`${server.enabled ? "Disable" : "Enable"} ${
            server.display_name
          }`}
          title={
            authed
              ? undefined
              : "Finish sign-in before toggling — agent can't call this connector yet."
          }
        />
        {server.auth_mode !== "none" &&
        server.auth_state !== "auth_unsupported" ? (
          <button
            type="button"
            className="mcp-card__link"
            disabled={busy}
            onClick={() =>
              void run("auth", () => connectors.authenticate(server.server_id))
            }
          >
            {pending === "auth" ? "Starting…" : authed ? "Re-auth" : "Sign in"}
          </button>
        ) : null}
        <button
          type="button"
          className="mcp-card__link mcp-card__link--danger"
          disabled={busy}
          onClick={() => setConfirm("remove")}
        >
          Remove
        </button>
      </div>

      <ConfirmDialog
        open={confirm === "remove"}
        onClose={() => setConfirm(null)}
        onConfirm={() =>
          run("remove", () => connectors.removeServer(server.server_id))
        }
        title={`Remove ${server.display_name}?`}
        description={
          <>
            <p>
              This deletes the connector from your workspace and revokes any
              stored OAuth tokens. The agent will lose access to its tools.
            </p>
            <p>You can re-add it later from the catalog.</p>
          </>
        }
        confirmLabel="Remove connector"
        destructive
      />
    </article>
  );
}
