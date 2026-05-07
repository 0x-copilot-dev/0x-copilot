// Single connector row. Owns its own pending + error state so failed
// per-row actions (skip auth, remove, authenticate) surface inline
// instead of in a shared error slot, and double-clicks on Authenticate
// don't fire two OAuth redirects.

import { Badge, Button, Card, Switch } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import { type ReactElement, useState } from "react";
import { ConfirmDialog } from "./ConfirmDialog";
import { authStateDisplay, isAuthenticated } from "./authStateDisplay";
import type { ConnectorState } from "./useConnectors";

interface ConnectorRowProps {
  server: McpServer;
  connectors: ConnectorState;
}

type Pending = null | "toggle" | "auth" | "skip" | "remove";

export function ConnectorRow({
  server,
  connectors,
}: ConnectorRowProps): ReactElement {
  const [pending, setPending] = useState<Pending>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | "skip" | "remove">(null);

  const display = authStateDisplay(server.auth_state);
  const authed = isAuthenticated(server.auth_state);
  const busy = pending !== null;

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
      setRowError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setPending(null);
    }
  }

  return (
    <Card className="connector-settings-row">
      <div className="connector-settings-row__main">
        <div className="connector-settings-row__title">
          <h3>{server.display_name}</h3>
          <p>{server.url}</p>
        </div>
        <Badge tone={display.tone}>{display.label}</Badge>
      </div>

      <p className="connector-settings-row__hint">{display.hint}</p>

      <div className="connector-settings-row__controls">
        <Switch
          label={server.enabled ? "Enabled" : "Disabled"}
          checked={server.enabled}
          disabled={busy}
          onChange={(event) =>
            void run("toggle", () =>
              connectors.setEnabled(server.server_id, event.target.checked),
            )
          }
        />

        {/* OAuth servers: show Authenticate / Re-authenticate. Hide for
            servers that don't need OAuth (auth_unsupported). */}
        {server.auth_mode !== "none" &&
        server.auth_state !== "auth_unsupported" ? (
          <Button
            type="button"
            variant={authed ? "ghost" : "secondary"}
            disabled={busy}
            aria-label={
              authed
                ? `Re-authenticate ${server.display_name}`
                : `Authenticate ${server.display_name}`
            }
            onClick={() =>
              void run("auth", () => connectors.authenticate(server.server_id))
            }
          >
            {pending === "auth"
              ? "Starting..."
              : authed
                ? "Re-authenticate"
                : "Authenticate"}
          </Button>
        ) : null}

        {/* Skip auth: only meaningful when not yet signed in. Hidden once
            authenticated or already skipped. */}
        {!authed ? (
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            aria-label={`Skip authentication for ${server.display_name}`}
            onClick={() => setConfirm("skip")}
          >
            Skip auth
          </Button>
        ) : null}

        <Button
          type="button"
          variant="danger"
          disabled={busy}
          aria-label={`Remove ${server.display_name}`}
          onClick={() => setConfirm("remove")}
        >
          Remove
        </Button>
      </div>

      {rowError ? (
        <p className="app-error connector-settings-row__error">{rowError}</p>
      ) : null}

      <ConfirmDialog
        open={confirm === "skip"}
        onClose={() => setConfirm(null)}
        onConfirm={() =>
          run("skip", () => connectors.skipAuth(server.server_id))
        }
        title={`Skip auth for ${server.display_name}?`}
        description={
          <>
            <p>
              The agent will call this connector <strong>without OAuth</strong>.
              Only do this if the server doesn&apos;t require auth, or you
              already trust the network path.
            </p>
            <p>You can authenticate later from this page.</p>
          </>
        }
        confirmLabel="Skip auth"
        destructive
      />

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
    </Card>
  );
}
