// Connectors settings — the MCP-server management sub-feature (catalog overlay,
// connected/needs-attention groups, JSON view, and the manual-add-by-URL form
// with pre-registered OAuth client fields). Extracted verbatim from
// `SettingsScreen.tsx` so the shell no longer carries a ~400-line sub-feature;
// behavior is unchanged. This is a self-contained unit — the natural thing to
// mount from the Tools rail destination when the legacy screen is retired.

import type {
  McpOAuthClientConfigRequest,
  McpServer,
} from "@0x-copilot/api-types";
import {
  Badge,
  Button,
  Card,
  Field,
  TextInput,
} from "@0x-copilot/design-system";
import type { FormEvent, ReactElement } from "react";
import { useMemo, useState } from "react";

import { ConnectorCard } from "../../connectors/ConnectorCard";
import { JsonEditorPanel } from "../../connectors/JsonEditorPanel";
import { isAuthenticated } from "../../connectors/authStateDisplay";
import { McpOverlay } from "../../connectors/mcp/McpOverlay";
import type { ConnectorState } from "../../connectors/useConnectors";
import { errorMessage } from "../../../utils/errors";

type ConnectorView = "visual" | "json";

export function ConnectorsSettings({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  // PR 4.4 — catalog wizard. Primary path; the custom-URL form is a
  // collapsed power-user fallback that opens on demand.
  const [mcpOverlayOpen, setMcpOverlayOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [view, setView] = useState<ConnectorView>("visual");

  // PR 4.4.6 — Connected = installed + authorized. Catalog rows the
  // user hasn't authorized yet live in the McpOverlay modal as
  // "Install" / "Resume install" cards, never on the Settings page.
  const groups = useMemo(
    () => groupServers(connectors.servers),
    [connectors.servers],
  );

  const activeCount = groups.connected.filter((s) => s.enabled).length;
  const totalConnected = groups.connected.length + groups.needsAttention.length;

  return (
    <div className="settings-section">
      <div className="settings-section__header">
        <div>
          <h2>Connectors</h2>
          <p>
            External systems the agent can read from and act on. Each connector
            is scoped per workspace.
          </p>
        </div>
        <div className="settings-section__header-actions">
          <div
            className="connector-view-toggle"
            role="tablist"
            aria-label="Connector view"
          >
            <button
              type="button"
              role="tab"
              aria-selected={view === "visual"}
              className={
                view === "visual"
                  ? "connector-view-toggle__btn connector-view-toggle__btn--active"
                  : "connector-view-toggle__btn"
              }
              onClick={() => setView("visual")}
            >
              Visual
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "json"}
              className={
                view === "json"
                  ? "connector-view-toggle__btn connector-view-toggle__btn--active"
                  : "connector-view-toggle__btn"
              }
              onClick={() => setView("json")}
            >
              JSON
            </button>
          </div>
          <Button
            type="button"
            variant="secondary"
            aria-label="Refresh connectors"
            onClick={() => void connectors.refresh()}
          >
            Refresh
          </Button>
        </div>
      </div>

      <McpOverlay
        open={mcpOverlayOpen}
        onClose={() => setMcpOverlayOpen(false)}
        connectors={connectors}
      />

      {connectors.error ? (
        <p className="app-error">{connectors.error}</p>
      ) : null}

      {view === "json" ? (
        <JsonEditorPanel connectors={connectors} />
      ) : (
        <>
          {connectors.loading && connectors.servers.length === 0 ? (
            <Card>
              <p>Loading connectors...</p>
            </Card>
          ) : null}

          {!connectors.loading && totalConnected === 0 ? (
            <Card className="mcp-empty">
              <h3>No connectors installed yet</h3>
              <p>
                Browse the curated MCP catalog to install one of the well-known
                servers, or add a custom URL.
              </p>
              <Button
                type="button"
                variant="primary"
                onClick={() => setMcpOverlayOpen(true)}
              >
                Manage MCP servers
              </Button>
            </Card>
          ) : null}

          <ConnectorGroup
            title="Needs attention"
            badge={`${groups.needsAttention.length}`}
            hint="Sign-in failed or interrupted. Re-authenticate to bring these online."
            servers={groups.needsAttention}
            connectors={connectors}
          />
          <ConnectorGroup
            title="Connected"
            badge={`${activeCount} active`}
            hint="Toggle on to make a connector available to the agent."
            servers={groups.connected}
            connectors={connectors}
          />

          {totalConnected > 0 ? (
            <ManageMcpServersCta onOpen={() => setMcpOverlayOpen(true)} />
          ) : null}

          {/* Manual-add form: collapsed by default. The catalog flow
              covers almost every real case; raw URL entry is for
              unlisted servers. */}
          <details
            className="connector-manual-add"
            open={manualOpen}
            onToggle={(event) => setManualOpen(event.currentTarget.open)}
          >
            <summary className="connector-manual-add__summary">
              Add manually with URL
            </summary>
            {manualOpen ? <ManualAddForm connectors={connectors} /> : null}
          </details>
        </>
      )}
    </div>
  );
}

function ManageMcpServersCta({ onOpen }: { onOpen: () => void }): ReactElement {
  return (
    <Card className="mcp-cta">
      <div className="mcp-cta__copy">
        <h3>MCP servers</h3>
        <p>
          Custom Model Context Protocol servers — browse the catalog or paste a
          URL.
        </p>
      </div>
      <Button type="button" variant="primary" onClick={onOpen}>
        Manage MCP servers
      </Button>
    </Card>
  );
}

interface GroupedServers {
  /** Authorized servers — toggle on/off determines runtime exposure. */
  connected: McpServer[];
  /** Servers whose last auth attempt failed or is mid-flight. */
  needsAttention: McpServer[];
}

// PR 4.4.6 — unauthenticated servers are NOT in either bucket. They
// live exclusively in the McpOverlay modal as "Install" / "Resume
// install" cards. This is the architectural rule the PRD enforces:
// Connected = the user has authorized.
function groupServers(servers: McpServer[]): GroupedServers {
  const connected: McpServer[] = [];
  const needsAttention: McpServer[] = [];
  for (const server of servers) {
    if (
      server.auth_state === "auth_failed" ||
      server.auth_state === "auth_pending"
    ) {
      needsAttention.push(server);
    } else if (isAuthenticated(server.auth_state)) {
      connected.push(server);
    }
    // else: unauthenticated (e.g. install in progress, OAuth cancelled)
    // — surfaced in McpOverlay Catalog tab, not here.
  }
  return { connected, needsAttention };
}

function ConnectorGroup({
  title,
  badge,
  hint,
  servers,
  connectors,
  emptyMessage,
}: {
  title: string;
  badge?: string;
  hint: string;
  servers: McpServer[];
  connectors: ConnectorState;
  emptyMessage?: string;
}): ReactElement | null {
  if (servers.length === 0) {
    if (!emptyMessage) {
      return null;
    }
    return (
      <section className="connector-group">
        <header className="connector-group__head">
          <h3>{title}</h3>
          <Badge tone="neutral">{badge ?? "0"}</Badge>
        </header>
        <p className="connector-group__hint">{emptyMessage}</p>
      </section>
    );
  }
  return (
    <section className="connector-group">
      <header className="connector-group__head">
        <h3>{title}</h3>
        <Badge tone="neutral">{badge ?? `${servers.length}`}</Badge>
      </header>
      <p className="connector-group__hint">{hint}</p>
      <div className="connector-card-grid">
        {servers.map((server) => (
          <ConnectorCard
            key={server.server_id}
            server={server}
            connectors={connectors}
          />
        ))}
      </div>
    </section>
  );
}

function ManualAddForm({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  const [url, setUrl] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scope, setScope] = useState("");
  const [authorizationEndpoint, setAuthorizationEndpoint] = useState("");
  const [tokenEndpoint, setTokenEndpoint] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      return;
    }
    if (!isHttpsUrl(trimmedUrl)) {
      setFormError("Server URL must be a valid https:// URL.");
      return;
    }
    if (submitting) {
      return;
    }
    try {
      const oauthClient = oauthClientFromForm({
        clientId,
        clientSecret,
        scope,
        authorizationEndpoint,
        tokenEndpoint,
      });
      setFormError(null);
      setSubmitting(true);
      const server = await connectors.addServer(trimmedUrl, oauthClient);
      setUrl("");
      setDisplayName("");
      setClientId("");
      setClientSecret("");
      setScope("");
      setAuthorizationEndpoint("");
      setTokenEndpoint("");
      setAdvancedOpen(false);
      // Mirror the catalog Install path: a freshly-created server lands
      // in ``auth_pending`` and is otherwise hard to find (the page's
      // active list filters on ``isAuthenticated``). Kick off OAuth so
      // the user ends connected, not stranded.
      if (
        server.auth_mode !== "none" &&
        server.auth_state !== "auth_unsupported" &&
        server.auth_state !== "authenticated"
      ) {
        await connectors.authenticate(server.server_id);
      }
    } catch (err) {
      setFormError(errorMessage(err, "Could not add connector."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <form
        className="connector-add-form"
        onSubmit={(event) => void onSubmit(event)}
      >
        <Field label="Server URL" hint="HTTPS endpoint for the MCP server.">
          <TextInput
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://mcp.example.com"
            required
          />
        </Field>
        <Field
          label="Display name"
          hint="Optional. Defaults to the server's advertised name."
        >
          <TextInput
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="e.g. Example MCP"
          />
        </Field>

        <details
          className="connector-add-form__advanced"
          open={advancedOpen}
          onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
        >
          <summary>
            Advanced &mdash; pre-registered OAuth client (servers without
            dynamic client registration)
          </summary>
          <div className="connector-add-form__advanced-grid">
            <Field label="OAuth client ID">
              <TextInput
                autoComplete="off"
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="client_id"
              />
            </Field>
            <Field label="OAuth client secret">
              <TextInput
                type="password"
                autoComplete="new-password"
                value={clientSecret}
                onChange={(event) => setClientSecret(event.target.value)}
                placeholder="client_secret"
              />
            </Field>
            <Field label="OAuth scope">
              <TextInput
                autoComplete="off"
                value={scope}
                onChange={(event) => setScope(event.target.value)}
                placeholder="e.g. mcp"
              />
            </Field>
            <Field
              label="Authorization endpoint"
              hint="Override only when the server doesn't advertise OAuth metadata."
            >
              <TextInput
                type="url"
                autoComplete="off"
                value={authorizationEndpoint}
                onChange={(event) =>
                  setAuthorizationEndpoint(event.target.value)
                }
                placeholder="https://auth.example.com/authorize"
              />
            </Field>
            <Field label="Token endpoint" hint="Optional override.">
              <TextInput
                type="url"
                autoComplete="off"
                value={tokenEndpoint}
                onChange={(event) => setTokenEndpoint(event.target.value)}
                placeholder="https://auth.example.com/token"
              />
            </Field>
          </div>
        </details>

        <Button type="submit" disabled={submitting}>
          {submitting ? "Adding..." : "Add connector"}
        </Button>
      </form>
      {formError ? <p className="app-error">{formError}</p> : null}
    </>
  );
}

function isHttpsUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

function oauthClientFromForm({
  clientId,
  clientSecret,
  scope,
  authorizationEndpoint,
  tokenEndpoint,
}: {
  clientId: string;
  clientSecret: string;
  scope: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
}): McpOAuthClientConfigRequest | undefined {
  const trimmedClientId = clientId.trim();
  const trimmedClientSecret = clientSecret.trim();
  const trimmedScope = scope.trim();
  const trimmedAuthorizationEndpoint = authorizationEndpoint.trim();
  const trimmedTokenEndpoint = tokenEndpoint.trim();
  const hasOAuthConfig = [
    trimmedClientId,
    trimmedClientSecret,
    trimmedScope,
    trimmedAuthorizationEndpoint,
    trimmedTokenEndpoint,
  ].some(Boolean);
  if (!hasOAuthConfig) {
    return undefined;
  }
  if (!trimmedClientId) {
    throw new Error(
      "OAuth client ID is required when OAuth settings are provided.",
    );
  }
  return {
    client_id: trimmedClientId,
    ...(trimmedClientSecret
      ? {
          client_secret: trimmedClientSecret,
          token_endpoint_auth_method: "client_secret_post",
        }
      : { token_endpoint_auth_method: "none" }),
    ...(trimmedScope ? { scope: trimmedScope } : {}),
    ...(trimmedAuthorizationEndpoint
      ? { authorization_endpoint: trimmedAuthorizationEndpoint }
      : {}),
    ...(trimmedTokenEndpoint ? { token_endpoint: trimmedTokenEndpoint } : {}),
  };
}
